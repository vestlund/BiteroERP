"""Budget och prognos per år, med rader för intäkter (timmar × pris eller fasta belopp) och kostnader per konto."""

from decimal import Decimal

from django.db import models
from django.urls import reverse

from logg.managers import LoggadQuerySet
from register.models import Konto, Kund
from verksamhet.models import Uppdrag

MANADER = range(1, 13)


class Budget(models.Model):
    objects = LoggadQuerySet.as_manager()

    ar = models.PositiveSmallIntegerField("år")
    namn = models.CharField(max_length=100)
    huvudbudget = models.BooleanField(
        default=True, help_text="Används på översikten och i uppföljningen som standard för året")
    beskrivning = models.TextField(blank=True)
    skapad = models.DateTimeField(auto_now_add=True)
    andrad = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "budget"
        verbose_name_plural = "budgetar"
        ordering = ["-ar", "-huvudbudget", "namn"]

    def __str__(self):
        return f"{self.namn} ({self.ar})"

    def get_absolute_url(self):
        return reverse("budget_uppfoljning", args=[self.pk])

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.huvudbudget:
            Budget.objects.filter(ar=self.ar).exclude(pk=self.pk).update(huvudbudget=False)

    def per_manad(self):
        """{'timmar': [12], 'intakter': [12], 'kostnader': [12]}"""
        r = {"timmar": [Decimal(0)] * 12, "intakter": [Decimal(0)] * 12, "kostnader": [Decimal(0)] * 12}
        for rad in self.rader.all():
            for i, v in enumerate(rad.varden):
                if rad.typ == Budgetrad.TID:
                    r["timmar"][i] += v
                    r["intakter"][i] += v * (rad.timpris or 0)
                elif rad.typ == Budgetrad.INTAKT:
                    r["intakter"][i] += v
                else:
                    r["kostnader"][i] += v
        return r


class Budgetrad(models.Model):
    objects = LoggadQuerySet.as_manager()

    def __str__(self):
        return f"{self.beskrivning} ({self.get_typ_display()})"

    TID, INTAKT, KOSTNAD = "tid", "intakt", "kostnad"
    TYPER = [(TID, "Intäkt – timmar × pris"), (INTAKT, "Intäkt – belopp"), (KOSTNAD, "Kostnad")]

    budget = models.ForeignKey(Budget, on_delete=models.CASCADE, related_name="rader")
    typ = models.CharField(max_length=10, choices=TYPER)
    beskrivning = models.CharField(max_length=200)
    uppdrag = models.ForeignKey(Uppdrag, on_delete=models.SET_NULL, null=True, blank=True)
    kund = models.ForeignKey(Kund, on_delete=models.SET_NULL, null=True, blank=True)
    konto = models.ForeignKey(Konto, on_delete=models.SET_NULL, null=True, blank=True)
    timpris = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    ordning = models.PositiveSmallIntegerField(default=0)
    m1 = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    m2 = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    m3 = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    m4 = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    m5 = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    m6 = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    m7 = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    m8 = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    m9 = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    m10 = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    m11 = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    m12 = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    class Meta:
        ordering = ["typ", "ordning", "id"]

    @property
    def varden(self):
        return [getattr(self, f"m{m}") for m in MANADER]

    @varden.setter
    def varden(self, lista):
        for m, v in zip(MANADER, lista):
            setattr(self, f"m{m}", Decimal(v or 0))

    @property
    def summa(self):
        return sum(self.varden, Decimal(0))
