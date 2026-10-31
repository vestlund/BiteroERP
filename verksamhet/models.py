"""Uppdrag, tidrapportering och fakturor."""

from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal

from django.db import models, transaction
from django.urls import reverse
from django.utils import timezone

from logg.managers import LoggadQuerySet
from register.models import Artikel, Foretag, Konto, Kostnadsstalle, Kund

TVA = Decimal("0.01")


def avrunda(belopp):
    return Decimal(str(belopp)).quantize(TVA, rounding=ROUND_HALF_UP)


class Uppdrag(models.Model):
    objects = LoggadQuerySet.as_manager()

    kund = models.ForeignKey(Kund, on_delete=models.PROTECT, related_name="uppdrag")
    namn = models.CharField(max_length=200)
    artikel = models.ForeignKey(
        Artikel, on_delete=models.PROTECT, null=True, blank=True,
        help_text="Styr fakturatext, enhet, konto och moms. Priset används om timpris lämnas tomt.",
    )
    timpris = models.DecimalField(
        "timpris (exkl. moms)", max_digits=10, decimal_places=2, null=True, blank=True,
        help_text="Lämna tomt för att använda artikelns pris",
    )
    kundreferens = models.CharField("kundens referens", max_length=200, blank=True)
    ordernummer = models.CharField("PO/ordernummer", max_length=100, blank=True)
    kostnadsstalle = models.ForeignKey(Kostnadsstalle, on_delete=models.SET_NULL, null=True, blank=True)
    startdatum = models.DateField(null=True, blank=True)
    slutdatum = models.DateField(null=True, blank=True)
    budget_timmar = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    aktiv = models.BooleanField(default=True)
    anteckning = models.TextField(blank=True)

    class Meta:
        verbose_name = "uppdrag"
        verbose_name_plural = "uppdrag"
        ordering = ["-aktiv", "kund__namn", "namn"]

    def __str__(self):
        return f"{self.kund.namn} – {self.namn}"

    def get_absolute_url(self):
        return reverse("uppdrag_detalj", args=[self.pk])

    @property
    def pris(self):
        if self.timpris is not None:
            return self.timpris
        return self.artikel.pris if self.artikel else Decimal(0)

    def timmar_totalt(self):
        return avrunda(self.tidposter.aggregate(s=models.Sum("timmar"))["s"] or 0)

    def timmar_ofakturerade(self):
        s = self.tidposter.filter(fakturerbar=True, fakturarad__isnull=True).aggregate(
            s=models.Sum("timmar"))["s"] or 0
        return avrunda(s)


class Tidpost(models.Model):
    objects = LoggadQuerySet.as_manager()

    uppdrag = models.ForeignKey(Uppdrag, on_delete=models.PROTECT, related_name="tidposter")
    datum = models.DateField(default=timezone.localdate)
    timmar = models.DecimalField(max_digits=5, decimal_places=2)
    beskrivning = models.CharField(max_length=300, blank=True)
    fakturerbar = models.BooleanField(default=True)
    fakturarad = models.ForeignKey(
        "Fakturarad", on_delete=models.SET_NULL, null=True, blank=True, related_name="tidposter", editable=False
    )
    skapad = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "tidpost"
        verbose_name_plural = "tidrapport"
        ordering = ["-datum", "-skapad"]

    def __str__(self):
        return f"{self.datum} {self.uppdrag} {self.timmar} h"

    @property
    def fakturerad(self):
        return self.fakturarad_id is not None


class Faktura(models.Model):
    objects = LoggadQuerySet.as_manager()

    UTKAST, SKICKAD, BETALD, MAKULERAD = "utkast", "skickad", "betald", "makulerad"
    STATUS = [(UTKAST, "Utkast"), (SKICKAD, "Skickad"), (BETALD, "Betald"), (MAKULERAD, "Makulerad")]

    nummer = models.PositiveIntegerField(unique=True, null=True, blank=True, editable=False)
    kund = models.ForeignKey(Kund, on_delete=models.PROTECT, related_name="fakturor")
    status = models.CharField(max_length=10, choices=STATUS, default=UTKAST)
    fakturadatum = models.DateField(default=timezone.localdate)
    forfallodatum = models.DateField("förfallodatum", null=True, blank=True)
    betaldatum = models.DateField(null=True, blank=True)
    er_referens = models.CharField("er referens", max_length=200, blank=True)
    var_referens = models.CharField("vår referens", max_length=200, blank=True)
    ordernummer = models.CharField("PO/ordernummer", max_length=100, blank=True)
    period = models.CharField(max_length=100, blank=True, help_text="T.ex. 'Oktober 2026'")
    meddelande = models.TextField("meddelande på fakturan", blank=True)
    skapad = models.DateTimeField(auto_now_add=True)
    sie_exporterad = models.DateTimeField(null=True, blank=True, editable=False)
    fran_visma = models.BooleanField("importerad från Visma", default=False, editable=False,
                                     help_text="Bokföringen finns i Visma och skapas inte här")
    ocr = models.CharField("OCR", max_length=30, blank=True)

    class Meta:
        verbose_name = "faktura"
        verbose_name_plural = "fakturor"
        ordering = ["-fakturadatum", "-nummer"]

    def __str__(self):
        return f"Faktura {self.nummer or '(utkast)'} – {self.kund.namn}"

    def get_absolute_url(self):
        return reverse("faktura_detalj", args=[self.pk])

    @property
    def last(self):
        return self.status != self.UTKAST

    @property
    def forfallen(self):
        return self.status == self.SKICKAD and self.forfallodatum and self.forfallodatum < timezone.localdate()

    def summering(self):
        """Returnerar netto, moms per sats, öresavrundning och att betala."""
        netto = Decimal(0)
        moms_per_sats = defaultdict(lambda: {"underlag": Decimal(0), "moms": Decimal(0)})
        for rad in self.rader.all():
            netto += rad.belopp
            m = moms_per_sats[rad.momssats]
            m["underlag"] += rad.belopp
        moms_total = Decimal(0)
        for sats, m in moms_per_sats.items():
            m["moms"] = avrunda(m["underlag"] * sats / 100)
            moms_total += m["moms"]
        brutto = netto + moms_total
        att_betala = brutto.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        return {
            "netto": netto,
            "moms": dict(sorted(moms_per_sats.items(), reverse=True)),
            "moms_total": moms_total,
            "oresavrundning": att_betala - brutto,
            "att_betala": att_betala,
        }

    @transaction.atomic
    def slutfor(self):
        """Ger fakturan nummer och låser den."""
        if self.status != self.UTKAST:
            return
        foretag = Foretag.objects.select_for_update().get(pk=Foretag.hamta().pk)
        hogsta = Faktura.objects.aggregate(m=models.Max("nummer"))["m"] or 0
        self.nummer = max(foretag.nasta_fakturanummer, hogsta + 1)
        foretag.nasta_fakturanummer = self.nummer + 1
        foretag.save(update_fields=["nasta_fakturanummer"])
        if not self.forfallodatum:
            dagar = self.kund.betalningsvillkor.dagar if self.kund.betalningsvillkor else 30
            self.forfallodatum = self.fakturadatum + timezone.timedelta(days=dagar)
        self.status = self.SKICKAD
        self.save()


class Fakturarad(models.Model):
    objects = LoggadQuerySet.as_manager()

    faktura = models.ForeignKey(Faktura, on_delete=models.CASCADE, related_name="rader")
    artikel = models.ForeignKey(Artikel, on_delete=models.PROTECT, null=True, blank=True)
    beskrivning = models.CharField(max_length=300)
    antal = models.DecimalField(max_digits=10, decimal_places=2, default=1)
    enhet = models.CharField(max_length=10, default="tim")
    a_pris = models.DecimalField("à-pris", max_digits=12, decimal_places=2, default=0)
    momssats = models.DecimalField("moms %", max_digits=4, decimal_places=1, default=Decimal("25"))
    konto = models.ForeignKey(Konto, on_delete=models.PROTECT, null=True, blank=True, verbose_name="intäktskonto")
    kostnadsstalle = models.ForeignKey(Kostnadsstalle, on_delete=models.SET_NULL, null=True, blank=True)
    ordning = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "fakturarad"
        verbose_name_plural = "fakturarader"
        ordering = ["ordning", "id"]

    def __str__(self):
        return self.beskrivning

    def save(self, *args, **kwargs):
        if not self.pk and not self.ordning:
            hogsta = Fakturarad.objects.filter(faktura_id=self.faktura_id).aggregate(m=models.Max("ordning"))["m"]
            self.ordning = (hogsta or 0) + 1
        super().save(*args, **kwargs)

    @property
    def belopp(self):
        return avrunda(self.antal * self.a_pris)
