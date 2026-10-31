"""Var databasen ligger.

Databasen ska inte ligga i en OneDrive-synkad mapp: SQLite kan skadas om
OneDrive synkar filen medan programmet kör. Därför ligger den som standard i
%USERPROFILE%\\bitero-erp-data (Windows) eller ~/bitero-erp-data (Mac/Linux).
Vill du ha en annan plats: sätt miljövariabeln BITERO_DATA_DIR.

Säkerhetskopior sparas däremot i OneDrive (se hitta_backupmapp): de är färdiga
kopior som inte är öppna, så de kan synkas utan risk.
"""

import os
import sqlite3
from pathlib import Path

DB_NAMN = "konsultsystem.sqlite3"


def hitta_datamapp(base_dir: Path) -> Path:
    data_dir = Path(os.environ.get("BITERO_DATA_DIR") or Path.home() / "bitero-erp-data")
    data_dir.mkdir(parents=True, exist_ok=True)
    _flytta_gammal_databas(base_dir / "data", data_dir)
    return data_dir


def _flytta_gammal_databas(gammal: Path, ny: Path):
    """Flyttar en databas från projektets data/-mapp (tidigare plats) en gång."""
    gammal_db, ny_db = gammal / DB_NAMN, ny / DB_NAMN
    if not gammal_db.exists() or ny_db.exists() or gammal.resolve() == ny.resolve():
        return
    kalla, dest = sqlite3.connect(gammal_db), sqlite3.connect(ny_db)
    try:
        kalla.backup(dest)  # säker kopia, tar med eventuell -wal-data
    finally:
        dest.close()
        kalla.close()
    nyckel = gammal / "secret_key.txt"
    if nyckel.exists() and not (ny / "secret_key.txt").exists():
        (ny / "secret_key.txt").write_text(nyckel.read_text())
    # Byt namn på originalet så det inte används av misstag (raderas inte).
    for f in [gammal_db, gammal / f"{DB_NAMN}-wal", gammal / f"{DB_NAMN}-shm"]:
        try:
            if f.exists():
                f.rename(f.with_name(f.name + ".flyttad"))
        except OSError:
            pass
    print(f"Databasen flyttades till {ny_db}")


def hitta_backupmapp(base_dir: Path) -> Path:
    """Var säkerhetskopiorna sparas.

    1. Miljövariabeln BITERO_BACKUP_DIR om den är satt.
    2. Annars en mapp i OneDrive (företagets OneDrive i första hand), så att kopiorna
       synkas bort från datorn: t.ex. C:\\Users\\vestl\\OneDrive - Bitero\\bitero-erp-backups.
    3. Annars backups/ i programmappen (som tidigare).
    """
    if satt := os.environ.get("BITERO_BACKUP_DIR"):
        return Path(satt)
    for var in ("OneDriveCommercial", "OneDrive"):
        rot = os.environ.get(var)
        if rot and Path(rot).is_dir():
            return Path(rot) / "bitero-erp-backups"
    return base_dir / "backups"


def hitta_inkorg(data_dir: Path) -> Path:
    """Inkorg för kvitton och fakturor som Claude ska bokföra.

    I OneDrive (företagets i första hand) så att du kan fota in kvitton från mobilen, annars i datamappen.
    Miljövariabeln BITERO_INKORG_DIR går före.
    """
    if satt := os.environ.get("BITERO_INKORG_DIR"):
        return Path(satt)
    for var in ("OneDriveCommercial", "OneDrive"):
        rot = os.environ.get(var)
        if rot and Path(rot).is_dir():
            return Path(rot) / "Kvitton inkorg"
    return data_dir / "inkorg"
