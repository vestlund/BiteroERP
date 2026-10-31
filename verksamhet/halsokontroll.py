"""Kontroller som körs vid varje start.

Resultatet visas i terminalen och sparas i datamappen (`halsokontroll.json`), så att en varning syns överst
på varje sida tills nästa start där allt är i ordning.
"""

import json
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path

from django.conf import settings
from django.db import connection
from django.db.models import Sum

FEL, VARNING = "fel", "varning"
EXTERN_INTERVALL = 35  # dagar mellan kopior till extern disk


@dataclass
class Anmarkning:
    niva: str
    rubrik: str
    text: str


def databasen():
    with connection.cursor() as c:
        c.execute("PRAGMA integrity_check")
        svar = [r[0] for r in c.fetchall()]
    if svar != ["ok"]:
        return Anmarkning(FEL, "Databasen är skadad",
                          "SQLite rapporterar: " + "; ".join(svar[:3]) + ". Återställ från säkerhetskopian.")


def behandlingshistoriken():
    from logg.sparning import kontrollera_kedjan

    k = kontrollera_kedjan()
    if not k["ok"]:
        forsta = k["fel"][0]
        return Anmarkning(FEL, "Behandlingshistoriken är bruten",
                          f"{len(k['fel'])} fel, första vid händelse {forsta[0]}: {forsta[1]}.")


def balans():
    from bokforing.models import Transaktion, Verifikation

    idn = list(Transaktion.objects.values("verifikation").annotate(s=Sum("belopp")).exclude(s=0)
               .values_list("verifikation", flat=True)[:10])
    if idn:
        return Anmarkning(FEL, "Verifikationer som inte balanserar",
                          ", ".join(v.vernr for v in Verifikation.objects.filter(pk__in=idn)) + ".")


def sakerhetskopian(backup_fel=None):
    from verksamhet.management.commands.backup import kontrollera_kopia, senaste_kopia

    if backup_fel:
        return Anmarkning(FEL, "Säkerhetskopian misslyckades", str(backup_fel))
    fil = senaste_kopia()
    if fil is None:
        return Anmarkning(FEL, "Ingen säkerhetskopia", f"Inga kopior finns i {settings.BACKUP_DIR}.")
    alder = datetime.now() - datetime.fromtimestamp(fil.stat().st_mtime)
    if alder > timedelta(days=2):
        return Anmarkning(FEL, "Säkerhetskopian är gammal", f"Senaste kopian är från {alder.days} dagar sedan.")
    try:
        kontrollera_kopia(fil)
    except Exception as e:  # noqa: BLE001 - alla fel betyder att kopian inte går att återställa
        return Anmarkning(FEL, "Säkerhetskopian går inte att läsa", f"{fil.name}: {e}")


def extern_kopia():
    f = Path(settings.DATA_DIR) / "senaste_externa_kopia.txt"
    if not f.exists():
        return Anmarkning(VARNING, "Ingen kopia på extern disk",
                          "Anslut en USB-disk och kör extern-kopia.bat (en gång i månaden).")
    datum = datetime.fromisoformat(f.read_text(encoding="utf-8").strip())
    dagar = (datetime.now() - datum).days
    if dagar > EXTERN_INTERVALL:
        return Anmarkning(VARNING, "Dags för kopia på extern disk",
                          f"Senaste kopian togs för {dagar} dagar sedan. Kör extern-kopia.bat.")


def lokala_andringar(bas=None):
    """Programmet ska köras från en version som finns på GitHub, så att historiken går att spåra."""
    bas = Path(bas or settings.BASE_DIR)
    if not (bas / ".git").exists():
        return Anmarkning(VARNING, "Programmet körs inte från git",
                          "Versionen går inte att spåra till GitHub. Kör flytta-till-git.bat.")
    try:
        r = subprocess.run(["git", "status", "--porcelain"], cwd=bas, capture_output=True, text=True,
                           timeout=15, check=True)
    except (OSError, subprocess.SubprocessError):
        return Anmarkning(VARNING, "git saknas", "Kunde inte kontrollera programversionen mot GitHub.")
    filer = [rad[3:] for rad in r.stdout.splitlines() if rad.strip()]
    if filer:
        visa = ", ".join(filer[:5]) + (f" och {len(filer) - 5} till" if len(filer) > 5 else "")
        return Anmarkning(FEL, "Programfilerna är ändrade lokalt",
                          f"{visa}. Kör `git status` och `git checkout .` för att återgå till GitHub-versionen, "
                          "eller checka in ändringen.")


def kor_alla(backup_fel=None):
    kontroller = [databasen, behandlingshistoriken, balans, lambda: sakerhetskopian(backup_fel), extern_kopia,
                  lokala_andringar]
    resultat = []
    for kontroll in kontroller:
        try:
            a = kontroll()
        except Exception as e:  # noqa: BLE001 - en kontroll som kraschar ska också synas
            a = Anmarkning(FEL, "Kontrollen kunde inte köras", f"{getattr(kontroll, '__name__', '')}: {e}")
        if a:
            resultat.append(a)
    spara(resultat)
    return resultat


def fil():
    return Path(settings.DATA_DIR) / "halsokontroll.json"


def spara(resultat):
    fil().write_text(json.dumps({"tid": datetime.now().isoformat(timespec="seconds"),
                                 "anmarkningar": [asdict(a) for a in resultat]}, ensure_ascii=False, indent=1),
                     encoding="utf-8")


def senaste():
    try:
        data = json.loads(fil().read_text(encoding="utf-8"))
        return [Anmarkning(**a) for a in data["anmarkningar"]]
    except (OSError, ValueError, KeyError, TypeError):
        return []
