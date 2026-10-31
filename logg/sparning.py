"""Spårar alla ändringar i registren och bokföringen och skriver dem till behandlingshistoriken.

Fångar:
- skapa, ändra och radera via modellerna (signaler),
- massuppdateringar med QuerySet.update() via LoggadQuerySet (logg/managers.py),
- bokförda verifikationer (loggas från bokforing.tjanster.bokfor med alla rader),
- inloggningar och misslyckade inloggningar,
- ändringar av själva programmet (registrera_systemversion, körs vid start).
"""

import contextvars
import datetime as dt
import hashlib
import json
import sys
import threading
from decimal import Decimal
from pathlib import Path

from django.db.models.signals import post_delete, post_save, pre_save
from django.utils import timezone

SPARADE_APPAR = {"register", "verksamhet", "bokforing", "lon", "budget", "auth"}
UNDANTAGNA = {
    "bokforing.Transaktion",     # loggas tillsammans med sin verifikation
    "lon.Skattetabellrad",       # Skatteverkets tabeller, tusentals rader per år
    "auth.Permission", "auth.Group",
}
EJ_LOGGADE_FALT = {"auth.User": {"last_login"}}
DOLDA_FALT = {"auth.User": {"password"}}

_anvandare = contextvars.ContextVar("logg_anvandare", default=None)
_kalla = contextvars.ContextVar("logg_kalla", default=None)
_las = threading.Lock()
_inkopplad = False


# ------------------------------------------------------------------ kontext

def satt_kontext(anvandare, kalla):
    return _anvandare.set(anvandare), _kalla.set(kalla)


def aterstall_kontext(tokens):
    _anvandare.reset(tokens[0])
    _kalla.reset(tokens[1])


def _standardkalla():
    argv = [Path(sys.argv[0]).name] + sys.argv[1:3] if sys.argv else []
    return "kommando: " + " ".join(argv)


class LoggMiddleware:
    """Talar om för loggen vem som gör ändringen och från vilken sida."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        namn = user.get_username() if user is not None and user.is_authenticated else ""
        tokens = satt_kontext(namn, f"{request.method} {request.path}"[:300])
        try:
            return self.get_response(request)
        finally:
            aterstall_kontext(tokens)


# ------------------------------------------------------------------ hjälp

def nyckel(modell):
    return f"{modell._meta.app_label}.{modell._meta.object_name}"


def spåras(modell):
    return (modell._meta.app_label in SPARADE_APPAR and nyckel(modell) not in UNDANTAGNA
            and not modell._meta.abstract and not modell._meta.proxy)


def _varde(v):
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, Decimal):
        return format(v.normalize(), "f") if v == v.to_integral() else str(v)
    if isinstance(v, (dt.date, dt.datetime, dt.time)):
        return v.isoformat()
    if hasattr(v, "name"):  # FieldFile
        return v.name or ""
    if isinstance(v, (list, dict)):
        return v
    return str(v)


def _falt(modell):
    ej = EJ_LOGGADE_FALT.get(nyckel(modell), set())
    # auto_now-fält ("senast ändrad") ändras vid varje sparning och säger inget i sig
    return [f for f in modell._meta.concrete_fields if f.name not in ej and not getattr(f, "auto_now", False)]


def _normalisera(falt, v):
    """Samma värde ska se likadant ut oavsett om det kommer från formuläret eller databasen (0 och 0.00)."""
    try:
        v = falt.to_python(v)
    except Exception:
        pass
    return _varde(v)


def _ogonblick(obj):
    return {f.attname: _normalisera(f, getattr(obj, f.attname)) for f in _falt(type(obj))}


def _rad_till_ogonblick(modell, rad):
    falt = {f.attname: f for f in modell._meta.concrete_fields}
    return {k: _normalisera(falt[k], v) if k in falt else _varde(v) for k, v in rad.items()}


def _dolj(modell, data, diff=False):
    """Lösenord och liknande: visa att de ändrats, men aldrig värdet."""
    dolda = {f.attname for f in modell._meta.concrete_fields if f.name in DOLDA_FALT.get(nyckel(modell), set())}
    for k in dolda & set(data):
        data[k] = ["***", "*** (ändrat)"] if diff else "***"
    return data


def _diff(fore, efter):
    return {k: [fore.get(k), v] for k, v in efter.items() if k in fore and fore.get(k) != v}


def _text(obj):
    try:
        return str(obj)[:300]
    except Exception:
        return f"{type(obj).__name__} {obj.pk}"


def logga(handling, modell, objekt_id="", objekt="", andringar=None):
    """Skriver en händelse. Fel släpps igenom med flit: hellre att ändringen stoppas
    (och rullas tillbaka) än att den genomförs utan att synas i historiken."""
    from .models import Handelse

    anvandare = _anvandare.get()
    kalla = _kalla.get()
    with _las:
        Handelse(
            tid=timezone.now(), anvandare=anvandare if anvandare is not None else "system",
            kalla=kalla if kalla is not None else _standardkalla(), handling=handling,
            modell=modell, objekt_id=str(objekt_id or ""), objekt=(objekt or "")[:300],
            andringar=andringar or {},
        ).save()


def logga_objekt(handling, obj, andringar=None):
    logga(handling, nyckel(type(obj)), obj.pk, _text(obj), andringar)


# ------------------------------------------------------------------ signaler

def _fore_sparning(sender, instance, raw=False, **kwargs):
    if raw or not spåras(sender) or instance._state.adding or instance.pk is None:
        instance._logg_fore = None
        return
    rad = sender._base_manager.filter(pk=instance.pk).values(*[f.attname for f in _falt(sender)]).first()
    instance._logg_fore = _rad_till_ogonblick(sender, rad) if rad else None


def _efter_sparning(sender, instance, created, raw=False, **kwargs):
    if raw or not spåras(sender):
        return
    if nyckel(sender) == "bokforing.Verifikation" and created:
        return  # loggas som "bokförd" när raderna finns, se bokforing.tjanster.bokfor
    efter = _ogonblick(instance)
    fore = getattr(instance, "_logg_fore", None)
    if created or fore is None:
        logga_objekt("skapad", instance, _dolj(sender, {k: v for k, v in efter.items() if v not in (None, "", False)}))
    else:
        andringar = _diff(fore, efter)
        if andringar:
            logga_objekt("andrad", instance, _dolj(sender, andringar, diff=True))
    instance._logg_fore = None


def _efter_radering(sender, instance, **kwargs):
    if spåras(sender):
        logga_objekt("raderad", instance,
                     _dolj(sender, {k: v for k, v in _ogonblick(instance).items() if v not in (None, "")}))


def _inloggad(sender, request, user, **kwargs):
    tokens = satt_kontext(user.get_username(), f"{request.method} {request.path}" if request else "")
    try:
        logga("inloggning", "auth.User", user.pk, user.get_username(), {"resultat": "lyckad"})
    finally:
        aterstall_kontext(tokens)


def _misslyckad(sender, credentials, request=None, **kwargs):
    logga("inloggning", "auth.User", "", credentials.get("username", ""), {"resultat": "misslyckad"})


def uppdatera_med_logg(qs, uppdatera, kwargs):
    """Kör QuerySet.update och loggar före- och eftervärden för varje påverkad rad."""
    modell = qs.model
    if not spåras(modell) or not kwargs:
        return uppdatera(**kwargs)
    attnames = [modell._meta.get_field(k).attname for k in kwargs]
    fore = {r["pk"]: r for r in qs.values("pk", *attnames)}
    antal = uppdatera(**kwargs)
    if fore:
        efter = modell._base_manager.filter(pk__in=fore).values("pk", *attnames)
        objekt = modell._base_manager.in_bulk(list(fore))
        for r in efter:
            f = _rad_till_ogonblick(modell, {k: v for k, v in fore[r["pk"]].items() if k != "pk"})
            e = _rad_till_ogonblick(modell, {k: v for k, v in r.items() if k != "pk"})
            andringar = _diff(f, e)
            if andringar:
                obj = objekt.get(r["pk"])
                logga("andrad", nyckel(modell), r["pk"], _text(obj) if obj else "", _dolj(modell, andringar, True))
    return antal


def koppla_in():
    global _inkopplad
    if _inkopplad:
        return
    from django.contrib.auth.signals import user_logged_in, user_login_failed

    pre_save.connect(_fore_sparning, dispatch_uid="logg_pre_save")
    post_save.connect(_efter_sparning, dispatch_uid="logg_post_save")
    post_delete.connect(_efter_radering, dispatch_uid="logg_post_delete")
    user_logged_in.connect(_inloggad, dispatch_uid="logg_inloggad")
    user_login_failed.connect(_misslyckad, dispatch_uid="logg_misslyckad")
    _inkopplad = True


# ------------------------------------------------------------------ kontroll

def kontrollera_kedjan():
    """Går igenom hela historiken. Returnerar dict(ok, antal, fel=[(id, orsak)])."""
    from .models import Handelse

    fel, foregaende, antal = [], "", 0
    for h in Handelse.objects.order_by("id").iterator(chunk_size=2000):
        antal += 1
        if h.foregaende != foregaende:
            fel.append((h.pk, "länken till föregående händelse stämmer inte (rad borttagen eller infogad)"))
        if h.berakna_hash() != h.hash:
            fel.append((h.pk, "innehållet har ändrats efter att det skrevs"))
        foregaende = h.hash
        if len(fel) >= 20:
            break
    return {"ok": not fel, "antal": antal, "fel": fel}


# ------------------------------------------------------------------ systemversion

PROGRAMFILER = ["**/*.py", "templates/**/*.html", "static/**/*.js", "static/**/*.css",
                "pyproject.toml", "uv.lock", "start.bat", "start.sh", "docs/**/*.md"]
IGNORERA = {".venv", "__pycache__", "backups", "import", "node_modules", ".git"}


def programfiler(bas):
    bas = Path(bas)
    filer = {}
    for monster in PROGRAMFILER:
        for p in bas.glob(monster):
            rel = p.relative_to(bas)
            if p.is_file() and not IGNORERA & set(rel.parts):
                # Radslut normaliseras så att Windows/OneDrive inte ger falska ändringar
                filer[rel.as_posix()] = hashlib.sha256(p.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    return dict(sorted(filer.items()))


def git_version(bas):
    """Kort commit-hash, t.ex. 'a1b2c3d' eller 'a1b2c3d+ändringar'. Tom sträng om git saknas."""
    import subprocess

    if not (Path(bas) / ".git").exists():
        return ""
    try:
        def git(*args):
            return subprocess.run(["git", *args], cwd=bas, capture_output=True, text=True, timeout=10,
                                  check=True).stdout.strip()
        commit = git("rev-parse", "--short", "HEAD")
        smutsig = git("status", "--porcelain", "--untracked-files=no")
        return commit + ("+ändringar" if smutsig else "")
    except (OSError, subprocess.SubprocessError):
        return ""


def _avtryck(filer):
    return hashlib.sha256(json.dumps(filer, sort_keys=True).encode()).hexdigest()


def har_andrats(bas):
    from .models import Systemversion

    senaste = Systemversion.objects.order_by("-id").first()
    return senaste is None or senaste.fingeravtryck != _avtryck(programfiler(bas))


def registrera_systemversion(bas, version, tester=None):
    """Sparar en ny systemversion om programfilerna har ändrats sedan förra starten.

    tester: resultatet av de automatiska testerna för versionen, t.ex. "64 tester OK".
    """
    from .models import Systemversion

    filer = programfiler(bas)
    avtryck = _avtryck(filer)
    senaste = Systemversion.objects.order_by("-id").first()
    if senaste and senaste.fingeravtryck == avtryck:
        return None
    gamla = senaste.filer if senaste else {}
    sv = Systemversion.objects.create(
        tid=timezone.now(), version=version, fingeravtryck=avtryck, filer=filer, git=git_version(bas),
        tillagda=sorted(set(filer) - set(gamla)) if senaste else [],
        andrade=sorted(f for f in filer if f in gamla and gamla[f] != filer[f]),
        borttagna=sorted(set(gamla) - set(filer)),
    )
    logga("system", "logg.Systemversion", sv.pk, f"Version {version}", {
        "version": version, "fingeravtryck": avtryck, "git": sv.git,
        "forsta": senaste is None, "antal_filer": len(filer), "tester": tester,
        "tillagda": sv.tillagda, "andrade": sv.andrade, "borttagna": sv.borttagna,
    })
    return sv
