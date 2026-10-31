"""Kryptering av säkerhetskopior (AES-256-GCM).

Nyckeln skapas vid första säkerhetskopian och sparas i datamappen (`backupnyckel.txt`), alltså på datorns
krypterade disk men inte i OneDrive. Den ska också sparas i en lösenordshanterare: utan nyckeln går
kopiorna inte att läsa om datorn försvinner.

Filformat: MAGI + nyckel-id (8 byte) + nonce (12 byte) + krypterad data med autentiseringstagg.
Nyckel-id gör att fel nyckel ger ett tydligt felmeddelande i stället för ett allmänt dekrypteringsfel.
"""

import base64
import hashlib
import os
import secrets
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.conf import settings

MAGI = b"BITERO-KRYPT-1\n"
ANDELSE = ".krypterad"


class KrypteringsFel(Exception):
    pass


def nyckelfil():
    return Path(settings.DATA_DIR) / "backupnyckel.txt"


def nyckel_som_text(nyckel):
    t = base64.b32encode(nyckel).decode().rstrip("=")
    return "-".join(t[i:i + 4] for i in range(0, len(t), 4))


def text_till_nyckel(text):
    t = "".join(c for c in text.upper() if c.isalnum())
    try:
        nyckel = base64.b32decode(t + "=" * (-len(t) % 8))
    except ValueError as e:
        raise KrypteringsFel("Nyckeln är felaktigt skriven.") from e
    if len(nyckel) != 32:
        raise KrypteringsFel("Nyckeln har fel längd.")
    return nyckel


def nyckel_id(nyckel):
    return hashlib.sha256(b"bitero-nyckel-id" + nyckel).digest()[:8]


def finns_nyckel():
    return nyckelfil().exists()


def hamta_nyckel():
    f = nyckelfil()
    if not f.exists():
        raise KrypteringsFel(f"Backupnyckeln saknas ({f}). Skapa en ny eller ange din sparade nyckel med "
                             "`uv run python manage.py backupnyckel`.")
    return text_till_nyckel(f.read_text(encoding="utf-8"))


def skapa_nyckel():
    """Skapar en ny nyckel. Returnerar den som text, för att visas för användaren en gång."""
    f = nyckelfil()
    if f.exists():
        raise KrypteringsFel("Det finns redan en backupnyckel.")
    nyckel = secrets.token_bytes(32)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(nyckel_som_text(nyckel) + "\n", encoding="utf-8")
    return nyckel_som_text(nyckel)


def ange_nyckel(text):
    nyckel = text_till_nyckel(text)
    nyckelfil().parent.mkdir(parents=True, exist_ok=True)
    nyckelfil().write_text(nyckel_som_text(nyckel) + "\n", encoding="utf-8")
    return nyckel


def kryptera(data, nyckel=None):
    nyckel = nyckel or hamta_nyckel()
    nonce = os.urandom(12)
    return MAGI + nyckel_id(nyckel) + nonce + AESGCM(nyckel).encrypt(nonce, data, MAGI)


def ar_krypterad(data):
    return data[:len(MAGI)] == MAGI


def dekryptera(data, nyckel=None):
    nyckel = nyckel or hamta_nyckel()
    if not ar_krypterad(data):
        raise KrypteringsFel("Filen är inte en krypterad Bitero-kopia.")
    n = len(MAGI)
    if data[n:n + 8] != nyckel_id(nyckel):
        raise KrypteringsFel("Filen är krypterad med en annan nyckel än den som finns på datorn.")
    try:
        return AESGCM(nyckel).decrypt(data[n + 8:n + 20], data[n + 20:], MAGI)
    except InvalidTag as e:
        raise KrypteringsFel("Filen är skadad eller har ändrats.") from e


def skriv_krypterad(mal, data, nyckel=None):
    """Skriver atomärt: en halvskriven fil får aldrig ligga kvar med rätt namn."""
    mal = Path(mal)
    mal.parent.mkdir(parents=True, exist_ok=True)
    tmp = mal.with_name(mal.name + ".tmp")
    tmp.write_bytes(kryptera(data, nyckel))
    tmp.replace(mal)
    return mal
