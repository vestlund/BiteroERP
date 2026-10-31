"""Behandlingshistorik (5 kap. 11 § BFL, BFNAR 2013:2 kap. 9).

Varje händelse kedjas till föregående med en SHA-256-hash. Ändras eller raderas en
rad i efterhand, direkt i databasfilen, går kedjan sönder och det syns vid kontroll.
"""

import hashlib
import json

from django.db import models, transaction


class OforanderligLogg(Exception):
    pass


def _kanonisk(data):
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


class Handelse(models.Model):
    SKAPAD, ANDRAD, RADERAD = "skapad", "andrad", "raderad"
    BOKFORD, IMPORT, SYSTEM, INLOGGNING = "bokford", "import", "system", "inloggning"
    HANDLINGAR = [
        (SKAPAD, "Skapad"), (ANDRAD, "Ändrad"), (RADERAD, "Raderad"), (BOKFORD, "Bokförd"),
        (IMPORT, "Import"), (SYSTEM, "Systemändring"), (INLOGGNING, "Inloggning"),
    ]

    tid = models.DateTimeField(db_index=True)
    anvandare = models.CharField("användare", max_length=150, blank=True)
    kalla = models.CharField("källa", max_length=300, blank=True, help_text="Sida eller kommando")
    handling = models.CharField(max_length=12, choices=HANDLINGAR)
    modell = models.CharField(max_length=60, db_index=True)
    objekt_id = models.CharField(max_length=60, blank=True, db_index=True)
    objekt = models.CharField(max_length=300, blank=True)
    andringar = models.JSONField(default=dict, blank=True)
    foregaende = models.CharField(max_length=64, blank=True)
    hash = models.CharField(max_length=64, unique=True)

    class Meta:
        verbose_name = "händelse"
        verbose_name_plural = "behandlingshistorik"
        ordering = ["-id"]

    def __str__(self):
        return f"{self.tid:%Y-%m-%d %H:%M} {self.get_handling_display()} {self.modell} {self.objekt}"

    def innehall(self):
        return {
            "tid": self.tid.isoformat(), "anvandare": self.anvandare, "kalla": self.kalla,
            "handling": self.handling, "modell": self.modell, "objekt_id": self.objekt_id,
            "objekt": self.objekt, "andringar": self.andringar, "foregaende": self.foregaende,
        }

    def berakna_hash(self):
        return hashlib.sha256(_kanonisk(self.innehall()).encode()).hexdigest()

    def save(self, *args, **kwargs):
        if self.pk:
            raise OforanderligLogg("Behandlingshistoriken kan inte ändras.")
        with transaction.atomic():
            senaste = Handelse.objects.order_by("-id").values_list("hash", flat=True).first()
            self.foregaende = senaste or ""
            # JSON-rundtur så att hashen räknas på exakt det som sparas
            self.andringar = json.loads(_kanonisk(self.andringar or {}))
            self.hash = self.berakna_hash()
            super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise OforanderligLogg("Behandlingshistoriken kan inte raderas.")


class Systemversion(models.Model):
    """En rad per programversion som har körts. Visar när systemet ändrades och vilka filer."""

    tid = models.DateTimeField()
    version = models.CharField(max_length=30)
    fingeravtryck = models.CharField(max_length=64, help_text="SHA-256 över alla programfiler")
    git = models.CharField("git-version", max_length=80, blank=True,
                           help_text="Commit i GitHub, '+ändringar' om det fanns ändringar som inte var incheckade")
    filer = models.JSONField(help_text="{fil: sha256}")
    tillagda = models.JSONField(default=list)
    andrade = models.JSONField(default=list)
    borttagna = models.JSONField(default=list)

    class Meta:
        verbose_name = "systemversion"
        verbose_name_plural = "systemversioner"
        ordering = ["-id"]

    def __str__(self):
        return f"{self.version} ({self.tid:%Y-%m-%d %H:%M})"

    @property
    def antal_andringar(self):
        return len(self.tillagda) + len(self.andrade) + len(self.borttagna)
