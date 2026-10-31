"""Deklarations- och betalningsdagar (för företag med omsättning upp till 40 miljoner kr).

Källor: Skatteverket, "När ska jag lämna arbetsgivardeklaration?", "När ska jag deklarera moms?" och
"Deklarera åt ett aktiebolag". Infaller dagen på en lördag, söndag eller helgdag flyttas den till närmast
följande vardag.
"""

import datetime as dt
import hashlib

from budget.tjanster import helgdagar


def vardag(d):
    lediga = helgdagar(d.year)
    while d.weekday() >= 5 or d in lediga:
        d += dt.timedelta(days=1)
        if d.month == 1 and d.day == 1:
            lediga = helgdagar(d.year)
    return d


def skattedag(ar, manad):
    """Den 12:e varje månad, den 17:e i januari och augusti."""
    return vardag(dt.date(ar, manad, 17 if manad in (1, 8) else 12))


def _plus_manader(ar, manad, n):
    m = manad - 1 + n
    return ar + m // 12, m % 12 + 1


def agi_dag(utbetalningsmanad):
    """Arbetsgivardeklaration och betalning för lön utbetald en viss månad (date med valfri dag)."""
    return skattedag(*_plus_manader(utbetalningsmanad.year, utbetalningsmanad.month, 1))


def momsdag(period_slut, momsperiod):
    """Sista dag för momsdeklaration och betalning för en redovisningsperiod som slutar period_slut."""
    if momsperiod == "ar":
        # Helårsmoms för aktiebolag utan EU-handel: 26:e i andra månaden efter beskattningsåret
        ar, m = _plus_manader(period_slut.year, period_slut.month, 2)
        return vardag(dt.date(ar, m, 26))
    return skattedag(*_plus_manader(period_slut.year, period_slut.month, 2))


def momsperioder(ar, momsperiod):
    """[(från, till)] för räkenskapsåret."""
    def slut(a, m):
        a2, m2 = _plus_manader(a, m, 1)
        return dt.date(a2, m2, 1) - dt.timedelta(days=1)
    if momsperiod == "manad":
        return [(dt.date(ar, m, 1), slut(ar, m)) for m in range(1, 13)]
    if momsperiod == "kvartal":
        return [(dt.date(ar, 3 * q - 2, 1), slut(ar, 3 * q)) for q in range(1, 5)]
    return [(dt.date(ar, 1, 1), dt.date(ar, 12, 31))]


def momsperiod_for(d, momsperiod):
    return next(p for p in momsperioder(d.year, momsperiod) if p[0] <= d <= p[1])


MANADSNAMN = ["januari", "februari", "mars", "april", "maj", "juni", "juli", "augusti", "september",
              "oktober", "november", "december"]


def frister(fran, till, foretag, med_lon=True):
    """Alla frister mellan två datum, sorterade. Varje frist är en dict(datum, rubrik, text, typ, url)."""
    res = []
    for ar in range(fran.year - 1, till.year + 1):
        for m in range(1, 13):
            d = skattedag(ar, m)
            fm_ar, fm = _plus_manader(ar, m, -1)
            delar = ["betala debiterad preliminärskatt (F-skatt)"]
            if med_lon:
                delar.insert(0, f"lämna arbetsgivardeklaration och betala skatt och avgifter för lön utbetald i "
                                f"{MANADSNAMN[fm - 1]}")
            res.append({"datum": d, "typ": "skatt", "rubrik": "Skattedeklaration",
                        "text": ("; ".join(delar) + ".")[0].upper() + ("; ".join(delar) + ".")[1:], "url": "agi",
                        "query": f"?period={fm_ar}-{fm:02d}"})
        for p_fran, p_till in momsperioder(ar, foretag.momsperiod):
            res.append({"datum": momsdag(p_till, foretag.momsperiod), "typ": "moms",
                        "rubrik": f"Moms {p_fran:%Y-%m}–{p_till:%m}" if p_fran.month != p_till.month
                        else f"Moms {p_fran:%Y-%m}",
                        "text": f"Lämna momsdeklarationen och betala momsen för {p_fran} – {p_till}.",
                        "url": "moms", "query": ""})
        # räkenskapsåret = kalenderår
        res.append({"datum": vardag(dt.date(ar + 1, 2, 12)), "typ": "skatt",
                    "rubrik": f"Fyllnadsinbetalning {ar}",
                    "text": "Betala in eventuell kvarskatt över 30 000 kr för att undvika kostnadsränta.",
                    "url": "skatt", "query": ""})
        res.append({"datum": dt.date(ar + 1, 6, 30), "typ": "bolag", "rubrik": f"Årsstämma {ar}",
                    "text": "Årsstämman ska hållas inom sex månader efter räkenskapsårets slut och fastställa "
                            "resultat- och balansräkningen.", "url": "bokslut", "query": f"?ar={ar}"})
        res.append({"datum": dt.date(ar + 1, 7, 31), "typ": "bolag", "rubrik": f"Årsredovisning {ar}",
                    "text": "Årsredovisningen ska ha kommit in till Bolagsverket.", "url": "bokslut",
                    "query": f"?ar={ar}"})
        res.append({"datum": vardag(dt.date(ar + 1, 8, 1)), "typ": "skatt", "rubrik": f"Inkomstdeklaration 2 ({ar})",
                    "text": "Lämna bolagets inkomstdeklaration till Skatteverket.", "url": "bokslut",
                    "query": f"?ar={ar}"})
    return sorted((f for f in res if fran <= f["datum"] <= till), key=lambda f: (f["datum"], f["rubrik"]))


def ics(frister_lista, foretag):
    """Kalenderfil (iCalendar) med heldagshändelser och påminnelse tre dagar innan."""
    from django.utils import timezone

    def esc(t):
        return t.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")

    nu = timezone.now().strftime("%Y%m%dT%H%M%SZ")
    rader = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Bitero ERP//Frister//SV", "CALSCALE:GREGORIAN",
             f"X-WR-CALNAME:{esc(foretag.namn)} – frister"]
    for f in frister_lista:
        uid = hashlib.sha1(f"{f['datum']}{f['rubrik']}".encode()).hexdigest()[:16] + "@bitero-erp"
        rader += ["BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{nu}", f"DTSTART;VALUE=DATE:{f['datum']:%Y%m%d}",
                  f"DTEND;VALUE=DATE:{f['datum'] + dt.timedelta(days=1):%Y%m%d}",
                  f"SUMMARY:{esc(f['rubrik'])}", f"DESCRIPTION:{esc(f['text'])}", "TRANSP:TRANSPARENT",
                  "BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{esc(f['rubrik'])}", "TRIGGER:-P3D",
                  "END:VALARM", "END:VEVENT"]
    rader.append("END:VCALENDAR")
    return ("\r\n".join(rader) + "\r\n").encode("utf-8")
