"""Kopplar faktura-PDF:er (t.ex. från Visma) till kundfakturorna i systemet.

Fakturanumret läses ur PDF:ens text ('Fakturanummer 178') och annars ur filnamnet (Faktura178.pdf).
För fakturor importerade från Visma uppdateras förfallodatum och OCR från PDF:en, eftersom de inte finns i SIE-filen.
"""

import hashlib
import io
import re
from datetime import datetime
from decimal import Decimal

from django.core.files.base import ContentFile
from django.db import transaction


def las_pdf(data: bytes):
    try:
        from pypdf import PdfReader
        text = "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(data)).pages[:3])
    except Exception:
        return {}
    info = {}

    def hitta(monster):
        m = re.search(monster, text, re.I)
        return m.group(1).strip() if m else None

    if v := hitta(r"Fakturanummer\s*:?\s*(\d+)"):
        info["nummer"] = int(v)
    for nyckel, rubrik in (("fakturadatum", "Fakturadatum"), ("forfallodatum", "Förfallodatum")):
        if v := hitta(rubrik + r"\s*:?\s*(\d{4}-\d{2}-\d{2})"):
            info[nyckel] = datetime.strptime(v, "%Y-%m-%d").date()
    if v := hitta(r"\bOCR\s*:?\s*(\d{2,25})"):
        info["ocr"] = v
    if v := hitta(r"Att betala\s*:?\s*(?:SEK\s*)?(-?[\d\s ]+,\d{2})"):
        info["att_betala"] = Decimal(re.sub(r"[\s ]", "", v).replace(",", "."))
    if v := hitta(r"Er referens\s*:?\s*([^\n]+)"):
        info["er_referens"] = v[:200]
    return info


@transaction.atomic
def koppla(filer):
    """filer: lista av (filnamn, bytes). Returnerar lista av resultatrader."""
    from verksamhet.models import Faktura

    from .models import Bilaga

    resultat = []
    for namn, data in filer:
        if not data[:5] == b"%PDF-":
            resultat.append({"fil": namn, "ok": False, "text": "inte en PDF"})
            continue
        info = las_pdf(data)
        nummer = info.get("nummer")
        if not nummer and (m := re.search(r"(\d+)", namn)):
            nummer = int(m.group(1))
        f = Faktura.objects.filter(nummer=nummer).select_related("kund").first() if nummer else None
        if not f:
            resultat.append({"fil": namn, "ok": False, "text": f"hittar ingen faktura nr {nummer or '?'} – importera fakturorna först"})
            continue
        sha = hashlib.sha256(data).hexdigest()
        varningar = []
        if Bilaga.objects.filter(kundfaktura=f, sha256=sha).exists():
            resultat.append({"fil": namn, "ok": True, "faktura": f, "text": "redan kopplad"})
            continue
        b = Bilaga(kundfaktura=f, namn=namn)
        b.fil.save(f"kundfaktura_{f.nummer}.pdf", ContentFile(data), save=False)
        b.save()
        if f.fran_visma:
            andrat = []
            if info.get("forfallodatum") and info["forfallodatum"] != f.forfallodatum:
                f.forfallodatum = info["forfallodatum"]
                andrat.append("förfallodatum")
            if info.get("ocr") and info["ocr"] != f.ocr:
                f.ocr = info["ocr"][:30]
                andrat.append("OCR")
            if info.get("er_referens") and not f.er_referens:
                f.er_referens = info["er_referens"]
                andrat.append("er referens")
            if andrat:
                f.save()
                varningar.append("uppdaterade " + ", ".join(andrat))
        if "att_betala" in info and info["att_betala"] != f.summering()["att_betala"]:
            varningar.append(f"OBS: PDF:en säger {info['att_betala']:.2f} kr, systemet {f.summering()['att_betala']:.2f} kr")
        resultat.append({"fil": namn, "ok": True, "faktura": f, "text": "; ".join(varningar) or "kopplad"})
    return resultat
