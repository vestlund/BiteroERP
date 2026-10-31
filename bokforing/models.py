"""Bokföring: verifikationer, transaktioner, bilagor, ingående balanser,
leverantörsfakturor/kvitton och bankposter.

Verifikationer är oföränderliga när de väl är skapade. Fel rättas med en ny
verifikation (rättelse), precis som bokföringslagen kräver.
"""

import hashlib
from decimal import Decimal
from pathlib import Path

from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
from django.utils import timezone

from logg.managers import LoggadQuerySet
from register.models import Konto, Kostnadsstalle, Leverantor


class OforanderligError(Exception):
    pass


class VerifikationQuerySet(LoggadQuerySet):
    """Bokförda verifikationer: massradering är förbjuden, och massuppdatering får bara röra
    exportmarkeringen och Visma-id:t (som sätts direkt efter import)."""

    TILLATNA = {"sie_exporterad", "extern_id"}

    def update(self, **kwargs):
        if otillatna := set(kwargs) - self.TILLATNA:
            raise OforanderligError(f"Bokförda verifikationer kan inte ändras ({', '.join(sorted(otillatna))}).")
        return super().update(**kwargs)

    def delete(self):
        raise OforanderligError("En bokförd verifikation kan inte raderas – gör en rättelse i stället.")


class TransaktionQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise OforanderligError("Transaktioner kan inte ändras.")

    def delete(self):
        raise OforanderligError("Transaktioner kan inte raderas.")


class Verifikation(models.Model):
    objects = VerifikationQuerySet.as_manager()

    SERIER = [
        ("A", "Manuell"),
        ("F", "Kundfaktura"),
        ("I", "Inbetalning"),
        ("L", "Leverantörsfaktura/kvitto"),
        ("U", "Utbetalning"),
        ("B", "Bank"),
        ("M", "Momsredovisning"),
        ("S", "Lön"),
        ("V", "Importerad från Visma"),
    ]

    serie = models.CharField(max_length=2, choices=SERIER, default="A")
    ar = models.PositiveIntegerField("räkenskapsår")
    nummer = models.PositiveIntegerField()
    datum = models.DateField("bokföringsdatum")
    text = models.CharField(max_length=300)
    skapad = models.DateTimeField(default=timezone.now, editable=False)
    skapad_av = models.CharField(max_length=150, blank=True)
    rattar = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="rattelser",
        verbose_name="rättar verifikation",
    )
    kundfaktura = models.ForeignKey(
        "verksamhet.Faktura", on_delete=models.PROTECT, null=True, blank=True, related_name="verifikationer")
    levfaktura = models.ForeignKey(
        "Leverantorsfaktura", on_delete=models.PROTECT, null=True, blank=True, related_name="verifikationer")
    lonekorning = models.ForeignKey(
        "lon.Lonekorning", on_delete=models.PROTECT, null=True, blank=True, related_name="verifikationer")
    sie_exporterad = models.DateTimeField(null=True, blank=True)
    extern_id = models.CharField("id i Visma", max_length=60, null=True, blank=True, unique=True)

    class Meta:
        verbose_name = "verifikation"
        verbose_name_plural = "verifikationer"
        ordering = ["-datum", "-skapad"]
        constraints = [models.UniqueConstraint(fields=["serie", "ar", "nummer"], name="unik_vernummer")]

    def __str__(self):
        return f"{self.vernr} {self.text}"

    @property
    def vernr(self):
        return f"{self.serie}{self.nummer}"

    def get_absolute_url(self):
        return reverse("verifikation", args=[self.pk])

    def save(self, *args, **kwargs):
        if self.pk and set(kwargs.get("update_fields") or []) != {"sie_exporterad"}:
            raise OforanderligError("En bokförd verifikation kan inte ändras – gör en rättelse i stället.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise OforanderligError("En bokförd verifikation kan inte raderas – gör en rättelse i stället.")

    @property
    def omsattning(self):
        return sum((t.belopp for t in self.transaktioner.all() if t.belopp > 0), Decimal(0))

    @property
    def ar_rattad(self):
        return self.rattelser.exists()


class Transaktion(models.Model):
    objects = TransaktionQuerySet.as_manager()

    def __str__(self):
        return f"{self.konto_id} {self.belopp}"

    verifikation = models.ForeignKey(Verifikation, on_delete=models.PROTECT, related_name="transaktioner")
    konto = models.ForeignKey(Konto, on_delete=models.PROTECT, related_name="transaktioner")
    belopp = models.DecimalField(max_digits=14, decimal_places=2, help_text="Positivt = debet, negativt = kredit")
    text = models.CharField(max_length=300, blank=True)
    kostnadsstalle = models.ForeignKey(Kostnadsstalle, on_delete=models.PROTECT, null=True, blank=True)

    class Meta:
        verbose_name = "transaktion"
        verbose_name_plural = "transaktioner"
        ordering = ["id"]

    def save(self, *args, **kwargs):
        if self.pk:
            raise OforanderligError("Transaktioner kan inte ändras.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise OforanderligError("Transaktioner kan inte raderas.")

    @property
    def debet(self):
        return self.belopp if self.belopp > 0 else None

    @property
    def kredit(self):
        return -self.belopp if self.belopp < 0 else None


def bilaga_sokvag(instance, filnamn):
    idag = timezone.localdate()
    return f"{idag.year}/{idag:%m}/{filnamn}"


class Bilaga(models.Model):
    objects = LoggadQuerySet.as_manager()

    verifikation = models.ForeignKey(Verifikation, on_delete=models.PROTECT, null=True, blank=True,
                                     related_name="bilagor")
    levfaktura = models.ForeignKey("Leverantorsfaktura", on_delete=models.PROTECT, null=True, blank=True,
                                   related_name="bilagor")
    kundfaktura = models.ForeignKey("verksamhet.Faktura", on_delete=models.CASCADE, null=True, blank=True,
                                    related_name="bilagor", help_text="Originalfakturan, t.ex. PDF från Visma")
    fil = models.FileField(upload_to=bilaga_sokvag)
    namn = models.CharField(max_length=255)
    sha256 = models.CharField(max_length=64, editable=False)
    uppladdad = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "bilaga"
        verbose_name_plural = "bilagor"

    def __str__(self):
        return self.namn

    def save(self, *args, **kwargs):
        if not self.sha256 and self.fil:
            h = hashlib.sha256()
            if getattr(self.fil, "_committed", True):
                # Redan sparad fil: öppna och stäng själv. Annars lämnas filen öppen, och Windows låser den.
                with self.fil.storage.open(self.fil.name, "rb") as f:
                    for chunk in iter(lambda: f.read(1 << 16), b""):
                        h.update(chunk)
            else:
                for chunk in self.fil.chunks():
                    h.update(chunk)
            self.sha256 = h.hexdigest()
        if not self.namn and self.fil:
            self.namn = Path(self.fil.name).name
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.verifikation_id or (self.levfaktura_id and self.levfaktura.verifikationer.exists()):
            raise OforanderligError("Bilagor till bokförda verifikationer kan inte raderas.")
        super().delete(*args, **kwargs)

    @property
    def ar_bild(self):
        return self.namn.lower().rsplit(".", 1)[-1] in {"jpg", "jpeg", "png", "gif", "webp", "heic"}


class IngaendeBalans(models.Model):
    objects = LoggadQuerySet.as_manager()

    def __str__(self):
        return f"IB {self.ar} {self.konto_id}: {self.belopp}"

    ar = models.PositiveIntegerField("räkenskapsår")
    konto = models.ForeignKey(Konto, on_delete=models.PROTECT)
    belopp = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        verbose_name = "ingående balans"
        verbose_name_plural = "ingående balanser"
        constraints = [models.UniqueConstraint(fields=["ar", "konto"], name="unik_ib")]
        ordering = ["ar", "konto"]


class Leverantorsfaktura(models.Model):
    objects = LoggadQuerySet.as_manager()

    FAKTURA, KVITTO = "faktura", "kvitto"
    TYPER = [(FAKTURA, "Leverantörsfaktura"), (KVITTO, "Kvitto / direktbetalt")]

    typ = models.CharField(max_length=10, choices=TYPER, default=FAKTURA)
    leverantor = models.ForeignKey(Leverantor, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name="fakturor", verbose_name="leverantör")
    motpart = models.CharField("motpart (om ingen leverantör)", max_length=200, blank=True,
                               help_text="T.ex. butik eller restaurang på ett kvitto")
    fakturanummer = models.CharField(max_length=100, blank=True)
    ocr = models.CharField("OCR/referens", max_length=50, blank=True)
    fakturadatum = models.DateField("faktura-/kvittodatum", default=timezone.localdate)
    forfallodatum = models.DateField("förfallodatum", null=True, blank=True)
    beskrivning = models.CharField(max_length=300, blank=True)
    belopp = models.DecimalField("belopp inkl. moms", max_digits=12, decimal_places=2)
    moms = models.DecimalField("varav moms", max_digits=12, decimal_places=2, default=0)
    konto = models.ForeignKey(Konto, on_delete=models.PROTECT, related_name="+", verbose_name="kostnadskonto")
    kostnadsstalle = models.ForeignKey(Kostnadsstalle, on_delete=models.SET_NULL, null=True, blank=True)
    betalkonto = models.ForeignKey(
        Konto, on_delete=models.PROTECT, related_name="+", null=True, blank=True,
        help_text="För kvitton: varifrån det betalades (1930 företagskonto, 2893 eget utlägg ...)")
    betaldatum = models.DateField(null=True, blank=True)
    skapad = models.DateTimeField(auto_now_add=True)
    fran_visma = models.BooleanField("importerad från Visma", default=False, editable=False)
    extern_nyckel = models.CharField(max_length=120, null=True, blank=True, unique=True, editable=False)

    class Meta:
        verbose_name = "leverantörsfaktura/kvitto"
        verbose_name_plural = "leverantörsfakturor och kvitton"
        ordering = ["-fakturadatum", "-skapad"]

    def __str__(self):
        return f"{self.motpartsnamn} {self.fakturanummer or ''} {self.belopp} kr".strip()

    def get_absolute_url(self):
        return reverse("levfaktura", args=[self.pk])

    def clean(self):
        if self.moms and abs(self.moms) > abs(self.belopp):
            raise ValidationError({"moms": "Momsen kan inte vara större än beloppet."})
        if not self.leverantor and not self.motpart:
            raise ValidationError("Ange leverantör eller motpart.")

    @property
    def motpartsnamn(self):
        return self.leverantor.namn if self.leverantor else self.motpart

    @property
    def netto(self):
        return self.belopp - self.moms

    @property
    def bokford(self):
        return self.verifikationer.filter(serie="L").exists()

    @property
    def betald(self):
        return self.betaldatum is not None

    @property
    def forfallen(self):
        return not self.betald and self.forfallodatum and self.forfallodatum < timezone.localdate()

    @property
    def status(self):
        if self.betald:
            return "betald"
        return "forfallen" if self.forfallen else "obetald"


class Bankpost(models.Model):
    objects = LoggadQuerySet.as_manager()

    datum = models.DateField()
    text = models.CharField(max_length=300)
    belopp = models.DecimalField(max_digits=14, decimal_places=2)
    saldo = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    konto = models.ForeignKey(Konto, on_delete=models.PROTECT, related_name="+", default=1930)
    importfil = models.CharField(max_length=255, blank=True)
    referens = models.CharField("referens/OCR", max_length=140, blank=True)
    end_to_end = models.CharField("end-to-end-id", max_length=60, blank=True)
    motpart = models.CharField(max_length=140, blank=True)
    automatisk = models.BooleanField("matchad automatiskt", default=False)
    nyckel = models.CharField(max_length=64, unique=True, editable=False)
    verifikation = models.ForeignKey(Verifikation, on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name="bankposter")
    ignorerad = models.BooleanField(default=False)
    importerad = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "bankpost"
        verbose_name_plural = "bankposter"
        ordering = ["-datum", "-id"]

    def __str__(self):
        return f"{self.datum} {self.text} {self.belopp}"

    @property
    def hanterad(self):
        return self.verifikation_id is not None or self.ignorerad


class Matchningsregel(models.Model):
    objects = LoggadQuerySet.as_manager()

    IN, UT, BADA = "in", "ut", "bada"

    namn = models.CharField(max_length=100)
    text_innehaller = models.CharField("texten innehåller", max_length=100,
                                       help_text="Skiftlägesokänsligt. Söker i text, motpart och referens.")
    riktning = models.CharField(max_length=5, default=UT,
                                choices=[(UT, "Utbetalning"), (IN, "Inbetalning"), (BADA, "Båda")])
    belopp = models.DecimalField("exakt belopp", max_digits=12, decimal_places=2, null=True, blank=True,
                                 help_text="Lämna tomt för valfritt belopp")
    motkonto = models.ForeignKey(Konto, on_delete=models.PROTECT, related_name="+")
    momssats = models.DecimalField("moms %", max_digits=4, decimal_places=1, default=0,
                                   help_text="Moms som ingår i beloppet: ingående moms på utbetalningar, utgående på inbetalningar. "
                                             "0 för t.ex. bankavgifter och skatter.")
    verifikationstext = models.CharField(max_length=200, blank=True, help_text="Tomt = bankens text")
    automatisk = models.BooleanField("bokför automatiskt", default=True,
                                     help_text="Annars visas regeln som förslag på bankposten")
    aktiv = models.BooleanField(default=True)
    traffar = models.PositiveIntegerField(default=0, editable=False)

    class Meta:
        verbose_name = "matchningsregel"
        verbose_name_plural = "matchningsregler"
        ordering = ["namn"]

    def __str__(self):
        return self.namn

    def passar(self, post):
        if self.riktning == self.IN and post.belopp < 0 or self.riktning == self.UT and post.belopp > 0:
            return False
        if self.belopp is not None and abs(post.belopp) != abs(self.belopp):
            return False
        hay = f"{post.text} {post.motpart} {post.referens}".lower()
        return self.text_innehaller.lower() in hay


class Inventarie(models.Model):
    """Inventarier och andra materiella anläggningstillgångar som skrivs av enligt plan (rak linje).

    Inventarier under ett halvt prisbasbelopp och med kort livslängd kostnadsförs i stället direkt
    (förbrukningsinventarier, 5410) och läggs inte upp här.
    """

    objects = LoggadQuerySet.as_manager()

    namn = models.CharField(max_length=200)
    anskaffningsdatum = models.DateField()
    anskaffningsvarde = models.DecimalField("anskaffningsvärde (exkl. moms)", max_digits=12, decimal_places=2)
    livslangd_ar = models.PositiveSmallIntegerField("avskrivningstid (år)", default=5,
                                                    help_text="Datorer ofta 3 år, övriga inventarier 5 år")
    tillgangskonto = models.ForeignKey(Konto, on_delete=models.PROTECT, related_name="+", default=1220,
                                       verbose_name="tillgångskonto")
    ack_konto = models.ForeignKey(Konto, on_delete=models.PROTECT, related_name="+", default=1229,
                                  verbose_name="konto för ackumulerade avskrivningar")
    avskrivningskonto = models.ForeignKey(Konto, on_delete=models.PROTECT, related_name="+", default=7832,
                                          verbose_name="kostnadskonto för avskrivning")
    avyttrad = models.DateField("såld/utrangerad", null=True, blank=True)
    anteckning = models.TextField(blank=True)

    class Meta:
        verbose_name = "inventarie"
        verbose_name_plural = "inventarier"
        ordering = ["-anskaffningsdatum", "namn"]

    def __str__(self):
        return f"{self.namn} ({self.anskaffningsdatum:%Y-%m})"

    @property
    def aktiv(self):
        return self.avyttrad is None

    def avskrivning(self, ar):
        """Planenlig avskrivning för räkenskapsåret, per påbörjad månad från anskaffningen."""
        start = self.anskaffningsdatum.year * 12 + self.anskaffningsdatum.month - 1
        slut_plan = start + self.livslangd_ar * 12
        slut_ar = ar * 12 + 12
        if self.avyttrad:
            slut_ar = min(slut_ar, self.avyttrad.year * 12 + self.avyttrad.month - 1)
        manader = max(0, min(slut_ar, slut_plan) - max(start, ar * 12))
        per_manad = self.anskaffningsvarde / (self.livslangd_ar * 12)
        return (per_manad * manader).quantize(Decimal("0.01"))

    def bokfort_varde(self, ar):
        """Restvärde vid utgången av året."""
        tidigare = sum((self.avskrivning(a) for a in range(self.anskaffningsdatum.year, ar + 1)), Decimal(0))
        return self.anskaffningsvarde - tidigare
