"""Årsarkiv: all räkenskapsinformation för ett räkenskapsår i en zip-fil.

Innehåll (se LASMIG.txt i arkivet):
- SIE 4 med kontoplan, ingående och utgående balanser, resultat och alla verifikationer,
- PDF: verifikationslista, huvudbok, resultat- och balansrapport, momsrapporter,
- behandlingshistoriken för året (CSV) och resultatet av kontrollen av hashkedjan,
- systemdokumentation och ändringslogg,
- alla bilagor (underlag) som hör till årets verifikationer och fakturor,
- en innehållsförteckning med SHA-256 för varje fil.
"""

import csv
import datetime as dt
import hashlib
import io
import zipfile
from collections import defaultdict
from decimal import Decimal

from django.conf import settings
from django.utils import timezone

from config.pdf import Rapport, kr
from config.version import VERSION
from register.models import Foretag, Konto

from . import tjanster as bok
from .models import Bilaga, Transaktion, Verifikation


def _q(t):
    return '"' + str(t).replace('"', "'").replace("–", "-").replace("—", "-") + '"'


def sie_ar(ar, foretag):
    """Fullständig SIE 4-fil för året, med våra egna serier och nummer."""
    fran, till = dt.date(ar, 1, 1), dt.date(ar, 12, 31)
    s = bok.saldon(fran, till)
    vers = Verifikation.objects.filter(ar=ar).order_by("serie", "nummer").prefetch_related("transaktioner")
    anvanda = set(s) | set(Transaktion.objects.filter(verifikation__ar=ar).values_list("konto_id", flat=True))
    rader = [
        "#FLAGGA 0", "#FORMAT PC8", "#SIETYP 4", f"#PROGRAM {_q('Bitero ERP')} {VERSION}",
        f"#GEN {timezone.localdate():%Y%m%d}", f"#FNAMN {_q(foretag.namn)}",
    ]
    if foretag.orgnr:
        rader.append(f"#ORGNR {foretag.orgnr}")
    rader.append(f"#RAR 0 {fran:%Y%m%d} {till:%Y%m%d}")
    for k in Konto.objects.filter(nummer__in=anvanda).order_by("nummer"):
        rader.append(f"#KONTO {k.nummer} {_q(k.namn)}")
    for k, v in s.items():
        if k < 3000:
            if v["ib"]:
                rader.append(f"#IB 0 {k} {v['ib']:.2f}")
            if v["ub"]:
                rader.append(f"#UB 0 {k} {v['ub']:.2f}")
        elif v["period"]:
            rader.append(f"#RES 0 {k} {v['period']:.2f}")
    for v in vers:
        rader.append(f'#VER "{v.serie}" "{v.nummer}" {v.datum:%Y%m%d} {_q(v.text)} {v.skapad:%Y%m%d}')
        rader.append("{")
        for t in v.transaktioner.all():
            rader.append(f"   #TRANS {t.konto_id} {{}} {t.belopp:.2f}" + (f' "" {_q(t.text)}' if t.text else ""))
        rader.append("}")
    return ("\r\n".join(rader) + "\r\n").encode("cp437", errors="replace")


def verifikationslista_pdf(ar, foretag):
    pdf = Rapport("Verifikationslista", f"Räkenskapsår {ar}", foretag.namn)
    kol = [("Ver", 16, "L"), ("Datum", 20, "L"), ("Konto", 14, "L"), ("Text", 80, "L"),
           ("Debet", 25, "R"), ("Kredit", 25, "R")]
    rader = []
    vers = Verifikation.objects.filter(ar=ar).order_by("serie", "nummer").prefetch_related(
        "transaktioner__konto").select_related("rattar")
    for v in vers:
        text = v.text + (f" (rättar {v.rattar.vernr})" if v.rattar else "")
        rader.append({"fet": True, "varden": [v.vernr, v.datum, "", text, "", ""]})
        for t in v.transaktioner.all():
            rader.append(["", "", t.konto_id, t.text or t.konto.namn,
                          kr(t.belopp) if t.belopp > 0 else "", kr(-t.belopp) if t.belopp < 0 else ""])
    pdf.tabell(kol, rader)
    pdf.stycke(f"\n{len(vers)} verifikationer. Registreringstidpunkt och användare finns i behandlingshistoriken.")
    return pdf.bytes()


def huvudbok_pdf(ar, foretag):
    fran, till = dt.date(ar, 1, 1), dt.date(ar, 12, 31)
    s = bok.saldon(fran, till)
    namn = dict(Konto.objects.values_list("nummer", "namn"))
    per_konto = defaultdict(list)
    for t in Transaktion.objects.filter(verifikation__ar=ar).select_related("verifikation").order_by(
            "konto_id", "verifikation__datum", "verifikation__serie", "verifikation__nummer"):
        per_konto[t.konto_id].append(t)
    pdf = Rapport("Huvudbok", f"{fran} - {till}", foretag.namn)
    kol = [("Datum", 20, "L"), ("Ver", 16, "L"), ("Text", 79, "L"), ("Debet", 22, "R"), ("Kredit", 22, "R"),
           ("Saldo", 22, "R")]
    for k, v in s.items():
        if not per_konto.get(k) and not v["ib"]:
            continue
        pdf.rubrik(f"{k} {namn.get(k, '')}", 9)
        saldo = v["ib"]
        rader = [["", "", "Ingående balans", "", "", kr(saldo)]]
        for t in per_konto.get(k, []):
            saldo += t.belopp
            rader.append([t.verifikation.datum, t.verifikation.vernr, t.text or t.verifikation.text,
                          kr(t.belopp) if t.belopp > 0 else "", kr(-t.belopp) if t.belopp < 0 else "", kr(saldo)])
        pdf.tabell(kol, rader, summa=["", "", "Utgående balans", "", "", kr(v["ub"])])
    return pdf.bytes()


def resultat_balans_pdf(ar, foretag):
    fran, till = dt.date(ar, 1, 1), dt.date(ar, 12, 31)
    s = bok.saldon(fran, till)
    namn = dict(Konto.objects.values_list("nummer", "namn"))
    pdf = Rapport("Resultat- och balansrapport", f"{fran} - {till}", foretag.namn)
    pdf.rubrik("Resultaträkning")
    kol = [("Konto", 18, "L"), ("Namn", 122, "L"), ("Belopp", 40, "R")]
    res = [[k, namn.get(k, ""), kr(-v["period"])] for k, v in s.items() if k >= 3000 and v["period"]]
    resultat = -sum((v["period"] for k, v in s.items() if k >= 3000), Decimal(0))
    pdf.tabell(kol, res, summa=["", "Årets resultat", kr(resultat)])
    pdf.rubrik("Balansräkning")
    kol = [("Konto", 18, "L"), ("Namn", 92, "L"), ("Ingående", 35, "R"), ("Utgående", 35, "R")]
    bal = [[k, namn.get(k, ""), kr(v["ib"]), kr(v["ub"])] for k, v in s.items() if k < 3000 and (v["ib"] or v["ub"])]
    tillg = sum((v["ub"] for k, v in s.items() if k < 2000), Decimal(0))
    skuld = sum((v["ub"] for k, v in s.items() if 2000 <= k < 3000), Decimal(0))
    pdf.tabell(kol, bal)
    diff = tillg + skuld - resultat
    pdf.rubrik("Avstämning", 9)
    pdf.tabell([("", 140, "L"), ("Belopp", 40, "R")], [
        ["Summa tillgångar (utgående)", kr(tillg)],
        ["Summa eget kapital och skulder (utgående)", kr(-skuld)],
        ["Årets resultat", kr(resultat)],
        {"fet": True, "varden": ["Differens", kr(diff)]},
    ])
    if diff:
        pdf.stycke("Differensen motsvarar att de ingående balanserna inte balanserar, oftast för att föregående års "
                   "resultat inte är bokat mot eget kapital (2099/2091) i de ingående balanserna.")
    return pdf.bytes()


def moms_pdf(ar, foretag):
    pdf = Rapport("Momsrapporter", f"Räkenskapsår {ar}", foretag.namn)
    if foretag.momsperiod == "manad":
        perioder = [(dt.date(ar, m, 1), (dt.date(ar + (m == 12), m % 12 + 1, 1) - dt.timedelta(days=1)))
                    for m in range(1, 13)]
    elif foretag.momsperiod == "kvartal":
        perioder = [(dt.date(ar, 3 * q - 2, 1), dt.date(ar + (q == 4), (3 * q) % 12 + 1, 1) - dt.timedelta(days=1))
                    for q in range(1, 5)]
    else:
        perioder = [(dt.date(ar, 1, 1), dt.date(ar, 12, 31))]
    kol = [("Ruta", 15, "L"), ("Beskrivning", 125, "L"), ("Belopp", 40, "R")]
    for fran, till in perioder:
        r = bok.momsrapport(fran, till)["rutor"]
        pdf.rubrik(f"{fran} - {till}")
        rader = [[n, bok.RUTNAMN.get(n, ""), kr(b)] for n, b in sorted(r.items()) if b and n != "49"]
        pdf.tabell(kol, rader, summa=["49", bok.RUTNAMN["49"], kr(r.get("49", 0))])
    return pdf.bytes()


def behandlingshistorik_csv(ar):
    from logg.models import Handelse

    buf = io.StringIO()
    buf.write("﻿")
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Id", "Tid", "Användare", "Källa", "Händelse", "Typ", "Objekt-id", "Objekt", "Ändringar",
                "Föregående hash", "Hash"])
    for h in Handelse.objects.filter(tid__year=ar).order_by("id").iterator(chunk_size=2000):
        w.writerow([h.pk, h.tid.isoformat(), h.anvandare, h.kalla, h.get_handling_display(), h.modell,
                    h.objekt_id, h.objekt, h.andringar, h.foregaende, h.hash])
    return buf.getvalue().encode("utf-8")


def _bilagor(ar):
    from verksamhet.models import Faktura

    qs = (Bilaga.objects.filter(verifikation__ar=ar) |
          Bilaga.objects.filter(levfaktura__verifikationer__ar=ar) |
          Bilaga.objects.filter(kundfaktura__in=Faktura.objects.filter(fakturadatum__year=ar)))
    return qs.distinct().select_related("verifikation", "levfaktura", "kundfaktura")


def _bilagenamn(b):
    if b.kundfaktura_id:
        typ = f"kundfaktura_{b.kundfaktura.nummer}"
    elif b.verifikation_id:
        typ = b.verifikation.vernr
    else:
        ver = b.levfaktura.verifikationer.filter(serie="L").first()
        typ = ver.vernr if ver else f"levfaktura_{b.levfaktura_id}"
    return f"bilagor/{typ}_{b.pk}_{b.namn}".replace("\\", "_")


def _systemdokumentation():
    import markdown
    delar = []
    for namn in ("systemdokumentation.md", "andringslogg.md"):
        fil = settings.BASE_DIR / "docs" / namn
        if fil.exists():
            delar.append(markdown.markdown(fil.read_text(encoding="utf-8"), extensions=["tables"]))
    return ("<!doctype html><meta charset='utf-8'><title>Systemdokumentation</title>"
            "<style>body{font-family:sans-serif;max-width:50rem;margin:2rem auto}table{border-collapse:collapse}"
            "td,th{border:1px solid #ccc;padding:.2rem .4rem}</style>" + "<hr>".join(delar)).encode("utf-8")


def skapa(ar):
    """Returnerar (filnamn, zip-bytes, sammanfattning)."""
    from logg.sparning import kontrollera_kedjan

    foretag = Foretag.hamta()
    filer = {
        f"SIE4_{ar}.se": sie_ar(ar, foretag),
        "verifikationslista.pdf": verifikationslista_pdf(ar, foretag),
        "huvudbok.pdf": huvudbok_pdf(ar, foretag),
        "resultat_och_balans.pdf": resultat_balans_pdf(ar, foretag),
        "momsrapporter.pdf": moms_pdf(ar, foretag),
        "behandlingshistorik.csv": behandlingshistorik_csv(ar),
        "systemdokumentation.html": _systemdokumentation(),
    }
    k = kontrollera_kedjan()
    filer["behandlingshistorik_kontroll.txt"] = (
        f"Kontroll av behandlingshistorikens hashkedja {timezone.localtime():%Y-%m-%d %H:%M}\n"
        f"Resultat: {'orörd' if k['ok'] else 'AVVIKELSER'} ({k['antal']} händelser kontrollerade)\n"
        + "".join(f"Händelse {i}: {o}\n" for i, o in k["fel"])).encode("utf-8")
    saknas = []
    for b in _bilagor(ar):
        try:
            with b.fil.open("rb") as f:
                filer[_bilagenamn(b)] = f.read()
        except FileNotFoundError:
            saknas.append(b.namn)
    manifest = [f"Årsarkiv för {foretag.namn} {foretag.orgnr}, räkenskapsår {ar}",
                f"Skapat {timezone.localtime():%Y-%m-%d %H:%M} med Bitero ERP {VERSION}",
                "", "Fil; storlek; SHA-256"]
    manifest += [f"{n}; {len(d)}; {hashlib.sha256(d).hexdigest()}" for n, d in sorted(filer.items())]
    if saknas:
        manifest += ["", "Bilagor som saknades på disken:"] + saknas
    filer["LASMIG.txt"] = ("\r\n".join(manifest) + "\r\n").encode("utf-8")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for namn, data in sorted(filer.items()):
            z.writestr(namn, data)
    filnamn = f"arsarkiv_{ar}_{timezone.localtime():%Y%m%d_%H%M}.zip"
    antal_bilagor = sum(1 for n in filer if n.startswith("bilagor/"))
    return filnamn, buf.getvalue(), {"filer": len(filer), "bilagor": antal_bilagor, "saknas": saknas,
                                     "kedjan_ok": k["ok"]}


def spara_kopia(filnamn, data):
    """Sparar en krypterad kopia bland säkerhetskopiorna (OneDrive), så att arkivet finns utanför datorn.

    Läses med `uv run python manage.py dekryptera <fil>` och backupnyckeln."""
    from config import kryptering

    return kryptering.skriv_krypterad(settings.BACKUP_DIR / "arsarkiv" / (filnamn + kryptering.ANDELSE), data)

