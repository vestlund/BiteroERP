"""Läser kontoutdrag i CSV-format från svenska banker (Handelsbanken, SEB, Swedbank,
Nordea, Danske m.fl.) genom att leta upp kolumnerna på rubrikerna."""

import csv
import datetime as dt
import hashlib
import io
import re
from collections import Counter
from decimal import Decimal, InvalidOperation

DATUMKOLUMNER = ["bokföringsdag", "bokföringsdatum", "bokförd", "reskontradatum", "transaktionsdag",
                 "transaktionsdatum", "datum", "date", "valutadag", "valutadatum"]
BELOPPKOLUMNER = ["belopp", "amount", "belopp sek"]
TEXTKOLUMNER = ["text/mottagare", "text", "beskrivning", "rubrik", "meddelande", "namn", "mottagare",
                "referens", "avsändare", "description"]
SALDOKOLUMNER = ["bokfört saldo", "saldo", "balance", "tillgängligt belopp"]


class BankimportFel(Exception):
    pass


def _avkoda(data: bytes) -> str:
    for kod in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return data.decode(kod)
        except UnicodeDecodeError:
            continue
    raise BankimportFel("Kunde inte läsa filens teckenkodning.")


def _tal(v):
    v = (v or "").strip().replace(" ", "").replace(" ", "").replace("−", "-").replace("kr", "")
    if not v:
        return None
    if "," in v and "." in v:
        v = v.replace(".", "")
    v = v.replace(",", ".")
    try:
        return Decimal(v)
    except InvalidOperation:
        return None


def _datum(v):
    v = (v or "").strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%d/%m/%Y", "%d.%m.%Y", "%Y/%m/%d", "%d-%m-%Y"):
        try:
            return dt.datetime.strptime(v[:10], fmt).date()
        except ValueError:
            continue
    return None


def _hitta(rubriker, namn):
    for n in namn:
        for i, r in enumerate(rubriker):
            if r == n:
                return i
    for n in namn:
        for i, r in enumerate(rubriker):
            if n in r:
                return i
    return None


def las_kontoutdrag(data: bytes, kontonr=1930):
    text = _avkoda(data)
    rader = text.splitlines()
    # Hitta rubrikraden: första raden som innehåller både ett datum- och ett beloppsord
    for start, rad in enumerate(rader[:30]):  # noqa: B007 - start används efter loopen
        l = rad.lower()
        if any(b in l for b in BELOPPKOLUMNER) and any(d in l for d in ("datum", "dag", "date", "bokförd")):
            break
    else:
        raise BankimportFel("Hittade ingen rubrikrad med datum och belopp i filen.")
    innehall = "\n".join(rader[start:])
    try:
        dialekt = csv.Sniffer().sniff(rader[start], delimiters=";,\t")
    except csv.Error:
        class dialekt(csv.excel):
            delimiter = ";"
    rad_lista = list(csv.reader(io.StringIO(innehall), dialekt))
    rubriker = [r.strip().strip('"').lower() for r in rad_lista[0]]
    i_datum, i_belopp = _hitta(rubriker, DATUMKOLUMNER), _hitta(rubriker, BELOPPKOLUMNER)
    i_saldo = _hitta(rubriker, SALDOKOLUMNER)
    textkol = [i for i, r in enumerate(rubriker) if any(t == r or t in r for t in TEXTKOLUMNER)
               and i not in (i_datum, i_belopp, i_saldo)]
    if i_datum is None or i_belopp is None:
        raise BankimportFel(f"Kunde inte hitta datum- och beloppskolumn bland: {', '.join(rubriker)}")

    poster, forekomst = [], Counter()
    for r in rad_lista[1:]:
        if len(r) <= max(i_datum, i_belopp):
            continue
        datum, belopp = _datum(r[i_datum]), _tal(r[i_belopp])
        if not datum or belopp is None:
            continue
        delar = [r[i].strip() for i in textkol if i < len(r) and r[i].strip()]
        txt = re.sub(r"\s+", " ", " / ".join(dict.fromkeys(delar)))[:300] or "(ingen text)"
        saldo = _tal(r[i_saldo]) if i_saldo is not None and i_saldo < len(r) else None
        bas = f"{kontonr}|{datum}|{belopp}|{txt}|{saldo}"
        forekomst[bas] += 1
        nyckel = hashlib.sha256(f"{bas}|{forekomst[bas]}".encode()).hexdigest()
        poster.append({"datum": datum, "belopp": belopp, "text": txt, "saldo": saldo, "nyckel": nyckel})
    if not poster:
        raise BankimportFel("Filen innehöll inga transaktioner som gick att läsa.")
    return poster


# ------------------------------------------------------------------ ISO 20022 camt.053 / camt.054

def _ns_fri(elem):
    """Tar bort namnrymder så att camt.053.001.02, .08 m.fl. kan läsas likadant."""
    for e in elem.iter():
        if isinstance(e.tag, str) and "}" in e.tag:
            e.tag = e.tag.split("}", 1)[1]
    return elem


def _t(elem, vag):
    if elem is None:
        return ""
    x = elem.find(vag)
    return (x.text or "").strip() if x is not None and x.text else ""


def ar_camt(data: bytes):
    huvud = data[:2000].decode("utf-8", errors="ignore")
    return "<Document" in huvud and "camt.05" in huvud


def las_camt(data: bytes, kontonr=1930):
    """Läser kontoutdrag (camt.053) eller avisering (camt.054) och returnerar bankposter.

    En post med flera transaktioner (t.ex. en samlingsinsättning av bankgiroinbetalningar)
    delas upp i en bankpost per transaktion när beloppen finns per transaktion.
    """
    import xml.etree.ElementTree as ET

    try:
        rot = _ns_fri(ET.fromstring(data))
    except ET.ParseError as e:
        raise BankimportFel(f"Ogiltig XML: {e}")
    rapporter = rot.findall(".//Stmt") + rot.findall(".//Ntfctn") + rot.findall(".//Rpt")
    if not rapporter:
        raise BankimportFel("Hittade inget kontoutdrag (Stmt) eller avisering (Ntfctn) i filen.")
    poster, iban = [], ""
    for rap in rapporter:
        iban = iban or _t(rap, "Acct/Id/IBAN") or _t(rap, "Acct/Id/Othr/Id")
        stangning = None
        for bal in rap.findall("Bal"):
            if _t(bal, "Tp/CdOrPrtry/Cd") in ("CLBD", "ITBD"):
                stangning = Decimal(_t(bal, "Amt") or "0") * (-1 if _t(bal, "CdtDbtInd") == "DBIT" else 1)
        for ntry in rap.findall("Ntry"):
            if _t(ntry, "Sts") in ("PDNG", "INFO") or _t(ntry, "Sts/Cd") in ("PDNG", "INFO"):
                continue
            tecken = -1 if _t(ntry, "CdtDbtInd") == "DBIT" else 1
            datum = _datum(_t(ntry, "BookgDt/Dt") or _t(ntry, "BookgDt/DtTm") or _t(ntry, "ValDt/Dt"))
            if not datum:
                continue
            ntry_belopp = Decimal(_t(ntry, "Amt") or "0") * tecken
            ntry_ref = _t(ntry, "AcctSvcrRef") or _t(ntry, "NtryRef")
            ntry_text = _t(ntry, "AddtlNtryInf")
            txs = ntry.findall("NtryDtls/TxDtls")
            delar = []
            for tx in txs:
                amt = _t(tx, "AmtDtls/TxAmt/Amt") or _t(tx, "Amt")
                tx_tecken = -1 if (_t(tx, "CdtDbtInd") or _t(ntry, "CdtDbtInd")) == "DBIT" else 1
                motpart = (_t(tx, "RltdPties/Dbtr/Nm") or _t(tx, "RltdPties/Dbtr/Pty/Nm")) if tecken > 0 else \
                    (_t(tx, "RltdPties/Cdtr/Nm") or _t(tx, "RltdPties/Cdtr/Pty/Nm"))
                ref = _t(tx, "RmtInf/Strd/CdtrRefInf/Ref") or _t(tx, "RmtInf/Strd/RfrdDocInf/Nb")
                ustrd = " ".join((u.text or "").strip() for u in tx.findall("RmtInf/Ustrd") if u.text)
                e2e = _t(tx, "Refs/EndToEndId")
                delar.append({
                    "belopp": Decimal(amt) * tx_tecken if amt else None, "motpart": motpart, "referens": ref,
                    "ustrd": ustrd, "end_to_end": "" if e2e == "NOTPROVIDED" else e2e,
                    "acct_ref": _t(tx, "Refs/AcctSvcrRef") or _t(tx, "Refs/TxId"),
                })
            if len(delar) > 1 and all(d["belopp"] is not None for d in delar) and \
                    sum(d["belopp"] for d in delar) == ntry_belopp:
                lista = delar
            else:
                forsta = delar[0] if delar else {}
                lista = [{**forsta, "belopp": ntry_belopp,
                          "ustrd": " / ".join(filter(None, [d.get("ustrd") for d in delar])) or forsta.get("ustrd", "")}]
            for i, d in enumerate(lista):
                text = " / ".join(dict.fromkeys(filter(None, [d.get("motpart"), d.get("ustrd"), ntry_text]))) \
                    or d.get("referens") or "(ingen text)"
                unik = d.get("acct_ref") or f"{ntry_ref}-{i}" if (d.get("acct_ref") or ntry_ref) else None
                bas = f"{kontonr}|{datum}|{d['belopp']}|{unik or text}"
                poster.append({
                    "datum": datum, "belopp": d["belopp"], "text": text[:300], "saldo": None,
                    "referens": (d.get("referens") or "")[:140], "end_to_end": (d.get("end_to_end") or "")[:60],
                    "motpart": (d.get("motpart") or "")[:140],
                    "nyckel": hashlib.sha256(bas.encode()).hexdigest(),
                })
        if stangning is not None and poster:
            sista = max((p for p in poster), key=lambda p: p["datum"])
            sista["saldo"] = stangning
    if not poster:
        raise BankimportFel("Filen innehöll inga bokförda transaktioner.")
    return poster, iban
