"""Lönehantering: anställda, lönekörningar, lönespecifikationer och lönerader."""

import datetime as dt
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse

from logg.managers import LoggadQuerySet
from register.models import Konto, Kostnadsstalle

KR = Decimal("1")
ORE = Decimal("0.01")


def kr(v):
    return Decimal(v).quantize(KR, rounding=ROUND_HALF_UP)


# Lönearter: kod -> (namn, konto, skattepliktig, avgiftspliktig, utbetalas kontant, AGI-ruta, enhet)
# Förmåner betalas inte ut men är skatte- och avgiftspliktiga (bokförs 7389 mot 7399).
LONEARTER = {
    "manadslon": ("Månadslön", 7210, True, True, True, "011", "mån"),
    "timlon": ("Timlön", 7210, True, True, True, "011", "tim"),
    "overtid": ("Övertid", 7210, True, True, True, "011", "tim"),
    "bonus": ("Bonus / provision", 7210, True, True, True, "011", "st"),
    "franvaro": ("Frånvaroavdrag", 7210, True, True, True, "011", "dag"),
    "sjuklon": ("Sjuklön", 7281, True, True, True, "011", "dag"),
    "karens": ("Karensavdrag", 7281, True, True, True, "011", "st"),
    "semesterlon": ("Semesterlön / semestertillägg", 7285, True, True, True, "011", "dag"),
    "formansbil": ("Bilförmån", 7385, True, True, False, "013", "mån"),
    "forman": ("Övrig förmån", 7389, True, True, False, "012", "st"),
    "traktamente": ("Traktamente, skattefritt", 7321, False, False, True, "", "dag"),
    "milersattning": ("Bilersättning, skattefri", 7331, False, False, True, "", "mil"),
    "utlagg": ("Utlägg (ersätts netto)", 2893, False, False, True, "", "st"),
    "nettoavdrag": ("Nettolöneavdrag", 2893, False, False, True, "", "st"),
}
LONEART_VAL = [(k, v[0]) for k, v in LONEARTER.items()]

# Arbetsgivaravgifter. Källor: Skatteverket 2026.
FULL_AVGIFT = Decimal("31.42")
ALDERSPENSION = Decimal("10.21")
UNGA_AVGIFT = Decimal("20.81")
UNGA_TAK = Decimal("25000")
UNGA_FRAN, UNGA_TILL = dt.date(2026, 4, 1), dt.date(2027, 9, 30)


def avgiftsgrupp(fodelsear, utbetalning):
    """Returnerar (grupp, sats) för arbetsgivaravgift."""
    if fodelsear is None:
        return "full", FULL_AVGIFT
    alder_vid_arets_ingang = utbetalning.year - 1 - fodelsear
    if fodelsear <= 1937:
        return "ingen", Decimal(0)
    if alder_vid_arets_ingang >= 67 or (utbetalning.year <= 2025 and alder_vid_arets_ingang >= 66):
        return "aldre", ALDERSPENSION
    if UNGA_FRAN <= utbetalning <= UNGA_TILL and 18 <= alder_vid_arets_ingang <= 22:
        return "unga", UNGA_AVGIFT
    return "full", FULL_AVGIFT


def berakna_avgift(underlag, fodelsear, utbetalning):
    grupp, sats = avgiftsgrupp(fodelsear, utbetalning)
    if grupp == "unga" and underlag > UNGA_TAK:
        return (UNGA_TAK * UNGA_AVGIFT / 100 + (underlag - UNGA_TAK) * FULL_AVGIFT / 100).quantize(ORE), grupp
    return (underlag * sats / 100).quantize(ORE), grupp


class Anstalld(models.Model):
    objects = LoggadQuerySet.as_manager()

    MANAD, TIM = "manad", "tim"
    SKATT_TABELL, SKATT_PROCENT, SKATT_INGEN = "tabell", "procent", "ingen"

    nummer = models.PositiveIntegerField("anställningsnummer", unique=True)
    fornamn = models.CharField("förnamn", max_length=100)
    efternamn = models.CharField(max_length=100)
    personnummer = models.CharField(max_length=13, help_text="ÅÅÅÅMMDD-XXXX")
    adress = models.CharField(max_length=200, blank=True)
    postnummer = models.CharField(max_length=10, blank=True)
    ort = models.CharField(max_length=100, blank=True)
    epost = models.EmailField("e-post", blank=True)
    telefon = models.CharField(max_length=50, blank=True)
    clearingnummer = models.CharField(max_length=10, blank=True)
    bankkonto = models.CharField("kontonummer", max_length=30, blank=True)
    befattning = models.CharField(max_length=100, blank=True)
    anstalld_fran = models.DateField("anställd från", null=True, blank=True)
    anstalld_till = models.DateField("anställd till", null=True, blank=True)
    lonetyp = models.CharField("lönetyp", max_length=10, choices=[(MANAD, "Månadslön"), (TIM, "Timlön")],
                               default=MANAD)
    manadslon = models.DecimalField("månadslön", max_digits=10, decimal_places=2, default=0)
    timlon = models.DecimalField("timlön", max_digits=8, decimal_places=2, default=0)
    loneskonto = models.ForeignKey(Konto, on_delete=models.PROTECT, null=True, blank=True, related_name="+",
                                   verbose_name="lönekonto",
                                   help_text="7210 tjänstemän, 7220 företagsledare, 7010 kollektivanställda")
    skattemetod = models.CharField(max_length=10, default=SKATT_TABELL, choices=[
        (SKATT_TABELL, "Skattetabell"), (SKATT_PROCENT, "Procent (jämkning, beslut eller sidoinkomst 30 %)"),
        (SKATT_INGEN, "Ingen skatt (t.ex. ungdom under gränsen)")])
    skattetabell = models.PositiveSmallIntegerField(null=True, blank=True, help_text="T.ex. 32")
    skattekolumn = models.PositiveSmallIntegerField(default=1, help_text="Normalt kolumn 1")
    skatteprocent = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    semesterdagar = models.PositiveSmallIntegerField("semesterdagar per år", default=25)
    kostnadsstalle = models.ForeignKey(Kostnadsstalle, on_delete=models.SET_NULL, null=True, blank=True)
    aktiv = models.BooleanField(default=True)
    anteckning = models.TextField(blank=True)

    class Meta:
        verbose_name = "anställd"
        verbose_name_plural = "anställda"
        ordering = ["-aktiv", "efternamn", "fornamn"]

    def __str__(self):
        return self.namn

    def get_absolute_url(self):
        return reverse("anstalld", args=[self.pk])

    def clean(self):
        if self.personnummer and self.fodelsear is None:
            raise ValidationError({"personnummer": "Ange personnumret som ÅÅÅÅMMDD-XXXX."})
        if self.skattemetod == self.SKATT_TABELL and not self.skattetabell:
            raise ValidationError({"skattetabell": "Ange skattetabell (finns på den anställdes skattebesked)."})
        if self.skattemetod == self.SKATT_PROCENT and self.skatteprocent is None:
            raise ValidationError({"skatteprocent": "Ange procent."})

    @property
    def namn(self):
        return f"{self.fornamn} {self.efternamn}"

    @property
    def fodelsear(self):
        siffror = "".join(c for c in self.personnummer if c.isdigit())
        if len(siffror) >= 12:
            return int(siffror[:4])
        if len(siffror) == 10:
            yy = int(siffror[:2])
            sekel = 1900 if (yy > dt.date.today().year % 100 or "+" in self.personnummer) else 2000
            return sekel + yy
        return None

    @property
    def skattetext(self):
        if self.skattemetod == self.SKATT_TABELL:
            return f"Tabell {self.skattetabell}, kolumn {self.skattekolumn}"
        if self.skattemetod == self.SKATT_PROCENT:
            return f"{self.skatteprocent} %"
        return "Ingen skatt"


class Lonekorning(models.Model):
    objects = LoggadQuerySet.as_manager()

    UTKAST, BOKFORD = "utkast", "bokford"

    period = models.CharField(max_length=7, help_text="ÅÅÅÅ-MM")
    utbetalningsdatum = models.DateField()
    status = models.CharField(max_length=10, default=UTKAST, choices=[(UTKAST, "Utkast"), (BOKFORD, "Bokförd")])
    anteckning = models.CharField(max_length=200, blank=True)
    skapad = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "lönekörning"
        verbose_name_plural = "lönekörningar"
        ordering = ["-utbetalningsdatum", "-id"]

    def __str__(self):
        return f"Lön {self.period} (utbetalning {self.utbetalningsdatum})"

    def get_absolute_url(self):
        return reverse("lonekorning", args=[self.pk])

    @property
    def last(self):
        return self.status == self.BOKFORD

    def summor(self):
        s = {k: Decimal(0) for k in ("brutto", "formaner", "skatt", "netto", "avgift", "skattefritt")}
        for spec in self.specar.all():
            b = spec.berakna()
            for k in s:
                s[k] += b[k]
        s["kostnad"] = s["brutto"] + s["avgift"] + s["skattefritt"]
        return s


class Lonespec(models.Model):
    objects = LoggadQuerySet.as_manager()

    korning = models.ForeignKey(Lonekorning, on_delete=models.CASCADE, related_name="specar")
    anstalld = models.ForeignKey(Anstalld, on_delete=models.PROTECT, related_name="specar")
    skatteavdrag = models.DecimalField(max_digits=10, decimal_places=0, default=0)
    skatt_manuell = models.BooleanField(default=False, help_text="Skatteavdraget är inskrivet för hand")
    meddelande = models.CharField(max_length=300, blank=True)

    class Meta:
        verbose_name = "lönespecifikation"
        verbose_name_plural = "lönespecifikationer"
        ordering = ["anstalld__efternamn"]
        constraints = [models.UniqueConstraint(fields=["korning", "anstalld"], name="en_spec_per_korning")]

    def __str__(self):
        return f"{self.anstalld} {self.korning.period}"

    def get_absolute_url(self):
        return reverse("lonespec", args=[self.pk])

    def berakna(self):
        brutto = formaner = skattefritt = netto_poster = Decimal(0)
        per_ruta = {"011": Decimal(0), "012": Decimal(0), "013": Decimal(0)}
        for r in self.rader.all():
            art = LONEARTER[r.typ]
            if art[2]:  # skattepliktig
                if art[4]:
                    brutto += r.belopp
                else:
                    formaner += r.belopp
                if art[5]:
                    per_ruta[art[5]] += r.belopp
            elif r.typ in ("utlagg", "nettoavdrag"):
                netto_poster += r.belopp
            else:
                skattefritt += r.belopp
        underlag = brutto + formaner
        avgift, grupp = berakna_avgift(underlag, self.anstalld.fodelsear, self.korning.utbetalningsdatum)
        skatt = Decimal(self.skatteavdrag or 0)
        return {
            "brutto": brutto, "formaner": formaner, "underlag": underlag, "skatt": skatt,
            "skattefritt": skattefritt, "netto_poster": netto_poster,
            "netto": brutto - skatt + skattefritt + netto_poster,
            "avgift": avgift, "avgiftsgrupp": grupp, "rutor": per_ruta,
        }

    def foreslagen_skatt(self):
        """Skatteavdrag för metod procent; för tabell: senaste kända belopp vid samma underlag."""
        a = self.anstalld
        underlag = self.berakna()["underlag"]
        if a.skattemetod == a.SKATT_INGEN or underlag <= 0:
            return Decimal(0)
        if a.skattemetod == a.SKATT_PROCENT:
            return kr(underlag * (a.skatteprocent or 0) / 100)
        from .skattetabell import sla_upp
        belopp = sla_upp(self.korning.utbetalningsdatum.year, a.skattetabell, a.skattekolumn, underlag)
        if belopp is not None:
            return belopp
        tidigare = Lonespec.objects.filter(anstalld=a, korning__status=Lonekorning.BOKFORD).exclude(
            pk=self.pk).order_by("-korning__utbetalningsdatum").first()
        if tidigare and tidigare.berakna()["underlag"] == underlag:
            return tidigare.skatteavdrag
        return None


class Lonerad(models.Model):
    objects = LoggadQuerySet.as_manager()

    def __str__(self):
        return f"{self.beskrivning or self.get_typ_display()}: {self.antal} × {self.a_pris}"

    spec = models.ForeignKey(Lonespec, on_delete=models.CASCADE, related_name="rader")
    typ = models.CharField("löneart", max_length=20, choices=LONEART_VAL)
    beskrivning = models.CharField(max_length=200, blank=True)
    antal = models.DecimalField(max_digits=8, decimal_places=2, default=1)
    a_pris = models.DecimalField("à-pris", max_digits=10, decimal_places=2, default=0)
    ordning = models.PositiveSmallIntegerField(default=0)

    class Meta:
        verbose_name = "lönerad"
        verbose_name_plural = "lönerader"
        ordering = ["ordning", "id"]

    @property
    def belopp(self):
        return (self.antal * self.a_pris).quantize(ORE, rounding=ROUND_HALF_UP)

    @property
    def artnamn(self):
        return LONEARTER[self.typ][0]


class Skattetabellrad(models.Model):
    """Skatteverkets allmänna skattetabeller (månadslön), importerade från deras fil."""

    def __str__(self):
        return f"Tabell {self.tabell} ({self.ar}) {self.inkomst_fran}–{self.inkomst_till} kr"

    ar = models.PositiveSmallIntegerField("år")
    tabell = models.PositiveSmallIntegerField()
    procent = models.BooleanField(default=False, help_text="Raden anger procent i stället för kronor")
    inkomst_fran = models.PositiveIntegerField()
    inkomst_till = models.PositiveIntegerField()
    kolumn1 = models.PositiveIntegerField()
    kolumn2 = models.PositiveIntegerField()
    kolumn3 = models.PositiveIntegerField()
    kolumn4 = models.PositiveIntegerField()
    kolumn5 = models.PositiveIntegerField()
    kolumn6 = models.PositiveIntegerField()

    class Meta:
        indexes = [models.Index(fields=["ar", "tabell", "inkomst_fran"])]
