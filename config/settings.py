"""Inställningar för konsultsystemet.

Systemet är tänkt att köras lokalt på en laptop och bara lyssna på 127.0.0.1.
"""

import os
import secrets
from pathlib import Path

from .datadir import hitta_backupmapp, hitta_datamapp

BASE_DIR = Path(__file__).resolve().parent.parent
# Databasen ligger utanför OneDrive, se config/datadir.py
DATA_DIR = hitta_datamapp(BASE_DIR)
BACKUP_DIR = hitta_backupmapp(BASE_DIR)  # i OneDrive, se config/datadir.py
IMPORT_DIR = BASE_DIR / "import"

# Hemlig nyckel skapas vid första start och sparas lokalt (ska inte delas).
_key_file = DATA_DIR / "secret_key.txt"
if not _key_file.exists():
    _key_file.write_text(secrets.token_urlsafe(50))
SECRET_KEY = _key_file.read_text().strip()

# Driftläge som standard: waitress + WhiteNoise, inga felsidor med kod.
# Utvecklingsläge (Djangos felsidor, runserver): sätt miljövariabeln BITERO_DEBUG=1.
DEBUG = os.environ.get("BITERO_DEBUG") == "1"
ALLOWED_HOSTS = ["127.0.0.1", "localhost"]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "register",
    "verksamhet",
    "bokforing",
    "lon",
    "budget",
    "logg",
    "planering",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "config.middleware.LoginRequiredEverywhere",
    "logg.sparning.LoggMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "register.context_processors.foretag",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": DATA_DIR / "konsultsystem.sqlite3",
        "OPTIONS": {"init_command": "PRAGMA journal_mode=WAL;"},
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
]

LANGUAGE_CODE = "sv"
TIME_ZONE = "Europe/Stockholm"
USE_I18N = True
USE_TZ = True
USE_THOUSAND_SEPARATOR = False  # belopp formateras med filtret |kr

STATIC_URL = "static/"

# Bilagor (kvitton, fakturor) sparas bredvid databasen, utanför OneDrive.
# De visas bara via inloggad vy, aldrig som publika filer.
MEDIA_ROOT = DATA_DIR / "bilagor"
MEDIA_URL = "/bilagor-ej-publikt/"
FILE_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024
DATA_UPLOAD_MAX_NUMBER_FILES = 2000
DATA_UPLOAD_MAX_MEMORY_SIZE = 200 * 1024 * 1024
STATICFILES_DIRS = [BASE_DIR / "static"]
# WhiteNoise serverar CSS/JS direkt från static/ (ingen collectstatic behövs för en lokal installation)
WHITENOISE_USE_FINDERS = True
WHITENOISE_AUTOREFRESH = DEBUG

# Originalfakturor visas i en iframe på fakturasidan – tillåt det inom systemet men inte från andra sidor
X_FRAME_OPTIONS = "SAMEORIGIN"

# Fel skrivs till bitero-erp-data/fel.log (och terminalen), eftersom felsidor med detaljer är avstängda
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"tid": {"format": "{asctime} {levelname} {name}: {message}", "style": "{"}},
    "handlers": {
        "fil": {"class": "logging.handlers.RotatingFileHandler", "filename": DATA_DIR / "fel.log",
                "maxBytes": 2_000_000, "backupCount": 3, "encoding": "utf-8", "formatter": "tid",
                "level": "ERROR"},
        "terminal": {"class": "logging.StreamHandler", "level": "ERROR"},
    },
    "loggers": {"django": {"handlers": ["fil", "terminal"], "level": "ERROR", "propagate": False}},
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "/admin/login/"
LOGIN_REDIRECT_URL = "/"
SESSION_COOKIE_AGE = 60 * 60 * 24 * 14
