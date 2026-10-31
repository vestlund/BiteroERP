"""Grundregister: konton, kostnadsställen, kunder, leverantörer, artiklar och företagsuppgifter.

Fältnamnen följer exporterna från Visma eEkonomi så att import och framtida
export blir enkel att följa.
"""

from decimal import Decimal

from django.db import models

from logg.managers import LoggadQuerySet


class Konto(models.Model):
    objects = LoggadQuerySet.as_manager()

    nummer = models.PositiveIntegerField("kontonummer", primary_key=True)
    namn = models.CharField(max_length=200)
    aktiv = models.BooleanField(default=False)
    momskod = models.CharField("momskod", max_length=20, blank=True, help_text="T.ex. 05-25% från Visma")
    kostnadsstalle_tillatet = models.BooleanField("kostnadsställe tillåtet", default=False)
    projekt_tillatet = models.BooleanField("projekt tillåtet", default=False)
    spärrat_manuell_bokning = models.BooleanField("spärrat för manuell bokning", default=False)

    class Meta:
        verbose_name = "konto"
        verbose_name_plural = "kontoplan"
        ordering = ["nummer"]

    def __str__(self):
        return f"{self.nummer} {self.namn}"

    @property
    def momssats(self):
        """Momssats i procent tolkad ur momskoden (t.ex. '05-25%' -> 25)."""
        if "-" in self.momskod and self.momskod.endswith("%"):
            try:
                return Decimal(self.momskod.split("-")[1].rstrip("%"))
            except Exception:
                return None
        return None


class Kostnadsstalle(models.Model):
    objects = LoggadQuerySet.as_manager()

    nummer = models.CharField(max_length=20, unique=True)
    namn = models.CharField(max_length=200)
    kortnamn = models.CharField(max_length=50, blank=True)
    aktiv = models.BooleanField(default=True)

    class Meta:
        verbose_name = "kostnadsställe"
        verbose_name_plural = "kostnadsställen"
        ordering = ["nummer"]

    def __str__(self):
        return f"{self.nummer} {self.namn}"


class Betalningsvillkor(models.Model):
    objects = LoggadQuerySet.as_manager()

    kod = models.CharField(max_length=10, unique=True, help_text="Koden från Visma-exporten")
    dagar = models.PositiveIntegerField("antal dagar netto")
    beskrivning = models.CharField(max_length=100, blank=True)

    class Meta:
        verbose_name = "betalningsvillkor"
        verbose_name_plural = "betalningsvillkor"
        ordering = ["dagar"]

    def __str__(self):
        return self.beskrivning or f"{self.dagar} dagar netto"


class Part(models.Model):
    """Gemensamma fält för kunder och leverantörer."""

    objects = LoggadQuerySet.as_manager()

    nummer = models.PositiveIntegerField(unique=True)
    namn = models.CharField(max_length=200)
    aktiv = models.BooleanField(default=True)
    orgnr = models.CharField("organisationsnummer", max_length=20, blank=True)
    adress1 = models.CharField("adress", max_length=200, blank=True)
    adress2 = models.CharField("adress 2", max_length=200, blank=True)
    postnummer = models.CharField(max_length=20, blank=True)
    ort = models.CharField(max_length=100, blank=True)
    landkod = models.CharField(max_length=2, default="SE")
    telefon = models.CharField(max_length=50, blank=True)
    mobil = models.CharField(max_length=50, blank=True)
    epost = models.EmailField("e-post", blank=True)
    webb = models.CharField(max_length=200, blank=True)
    anteckning = models.TextField(blank=True)
    kontaktperson = models.CharField(max_length=200, blank=True)
    kontakt_epost = models.CharField("kontaktpersonens e-post", max_length=200, blank=True)
    kontakt_telefon = models.CharField("kontaktpersonens telefon", max_length=50, blank=True)
    kontakt_mobil = models.CharField("kontaktpersonens mobil", max_length=50, blank=True)
    betalningsvillkor = models.ForeignKey(Betalningsvillkor, on_delete=models.PROTECT, null=True, blank=True)
    valuta = models.CharField(max_length=3, default="SEK")

    class Meta:
        abstract = True
        ordering = ["namn"]

    def __str__(self):
        return f"{self.nummer} {self.namn}"

    @property
    def postadress(self):
        return " ".join(p for p in [self.postnummer, self.ort] if p)


class Kund(Part):
    privatperson = models.BooleanField(default=False)
    sprak = models.CharField("dokumentspråk", max_length=5, default="sv")
    gln = models.CharField("GLN (e-faktura)", max_length=20, blank=True)
    momsnummer = models.CharField("VAT-nummer", max_length=30, blank=True)
    kopia_epost = models.CharField("kopia e-post", max_length=300, blank=True)
    leverans_namn = models.CharField("leveransnamn", max_length=200, blank=True)
    leverans_adress1 = models.CharField("leveransadress", max_length=200, blank=True)
    leverans_adress2 = models.CharField("leveransadress 2", max_length=200, blank=True)
    leverans_postnummer = models.CharField(max_length=20, blank=True)
    leverans_ort = models.CharField(max_length=100, blank=True)
    rabatt_procent = models.DecimalField("rabatt %", max_digits=5, decimal_places=2, default=0)
    etiketter = models.CharField(max_length=200, blank=True)

    class Meta(Part.Meta):
        verbose_name = "kund"
        verbose_name_plural = "kunder"


class Leverantor(Part):
    standardkonto = models.ForeignKey(
        Konto, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
        verbose_name="standard kostnadskonto", help_text="Föreslås när du registrerar fakturor/kvitton",
    )
    standard_momssats = models.DecimalField("standard moms %", max_digits=4, decimal_places=1, default=Decimal("25"))
    bankgiro = models.CharField(max_length=20, blank=True)
    plusgiro = models.CharField(max_length=20, blank=True)
    ocr = models.BooleanField("använder OCR", default=False)
    autogiro = models.BooleanField(default=False)
    bank = models.CharField(max_length=100, blank=True)
    bic = models.CharField("BIC", max_length=20, blank=True)
    iban = models.CharField("IBAN", max_length=40, blank=True)
    bban = models.CharField("BBAN", max_length=40, blank=True)

    class Meta(Part.Meta):
        verbose_name = "leverantör"
        verbose_name_plural = "leverantörer"


class Artikel(models.Model):
    objects = LoggadQuerySet.as_manager()

    ENHETER = [("HUR", "tim"), ("PCE", "st"), ("DAY", "dag"), ("MON", "mån")]

    nummer = models.CharField(max_length=30, unique=True)
    namn = models.CharField(max_length=200)
    namn_engelska = models.CharField("namn (engelska)", max_length=200, blank=True)
    aktiv = models.BooleanField(default=True)
    enhet = models.CharField(max_length=5, choices=ENHETER, default="HUR")
    pris = models.DecimalField("pris exkl. moms", max_digits=12, decimal_places=2, default=0)
    inkopspris = models.DecimalField("inköpspris", max_digits=12, decimal_places=2, default=0)
    intaktskonto = models.ForeignKey(
        Konto, on_delete=models.PROTECT, related_name="+", verbose_name="intäktskonto", null=True, blank=True
    )
    momssats = models.DecimalField("moms %", max_digits=4, decimal_places=1, default=Decimal("25"))
    visma_kontering = models.CharField(
        "Visma konteringskod", max_length=20, blank=True, help_text="ArticleAccountCoding från exporten"
    )
    anteckning = models.TextField(blank=True)

    class Meta:
        verbose_name = "artikel"
        verbose_name_plural = "artiklar"
        ordering = ["namn"]

    def __str__(self):
        return f"{self.nummer} – {self.namn}"


class Foretag(models.Model):
    """Ditt eget bolag. Används på fakturor och i SIE-export. Endast en rad."""

    objects = LoggadQuerySet.as_manager()

    namn = models.CharField("företagsnamn", max_length=200, default="Mitt Konsultbolag AB")
    orgnr = models.CharField("organisationsnummer", max_length=20, blank=True)
    momsnummer = models.CharField("momsregistreringsnummer", max_length=30, blank=True)
    adress = models.CharField(max_length=200, blank=True)
    postnummer = models.CharField(max_length=20, blank=True)
    ort = models.CharField(max_length=100, blank=True)
    telefon = models.CharField(max_length=50, blank=True)
    epost = models.EmailField("e-post", blank=True)
    webb = models.CharField(max_length=200, blank=True)
    bankgiro = models.CharField(max_length=20, blank=True)
    plusgiro = models.CharField(max_length=20, blank=True)
    iban = models.CharField("IBAN", max_length=40, blank=True)
    bic = models.CharField("BIC", max_length=20, blank=True)
    f_skatt = models.BooleanField("godkänd för F-skatt", default=True)
    var_referens = models.CharField("vår referens", max_length=100, blank=True)
    nasta_fakturanummer = models.PositiveIntegerField("nästa fakturanummer", default=1)
    drojsmalsranta = models.CharField("dröjsmålsränta", max_length=100, default="Referensränta + 8 %")
    MOMSPERIODER = [("manad", "Månad"), ("kvartal", "Kvartal"), ("ar", "År")]
    momsperiod = models.CharField("momsredovisning", max_length=10, choices=MOMSPERIODER, default="kvartal")
    last_tom = models.DateField(
        "bokföring låst t.o.m.", null=True, blank=True,
        help_text="Inga verifikationer kan skapas med datum till och med detta datum",
    )
    kundfordringskonto = models.ForeignKey(
        Konto, on_delete=models.PROTECT, related_name="+", null=True, blank=True, verbose_name="kundfordringskonto"
    )
    standardartikel = models.ForeignKey(
        Artikel, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
        help_text="Förvald artikel för nya uppdrag",
    )

    class Meta:
        verbose_name = "företagsuppgifter"
        verbose_name_plural = "företagsuppgifter"

    def __str__(self):
        return self.namn

    @classmethod
    def hamta(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
