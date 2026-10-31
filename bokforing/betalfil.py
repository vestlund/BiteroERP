"""Betalfil till banken för leverantörsfakturor: ISO 20022 pain.001.001.03.

Bankgirot slutade ta emot det gamla LB-formatet under 2026, så filen skapas i ISO 20022-format som
svenska banker tar emot. Bankgiro: ClrSysId SESBA, MmbId 9900, kontoschema BGNR.
PlusGiro: ClrSysId SESBA, MmbId 9960, kontoschema BBAN.
"""

import datetime as dt
from decimal import Decimal
from xml.sax.saxutils import escape

from django.utils import timezone


class BetalfilFel(Exception):
    pass


def _ocr_giltig(ref):
    """Luhn-kontroll (mod 10) för OCR-nummer."""
    if not ref.isdigit() or len(ref) < 2:
        return False
    summa = 0
    for i, c in enumerate(reversed(ref)):
        n = int(c) * (2 if i % 2 else 1)
        summa += n - 9 if n > 9 else n
    return summa % 10 == 0


def _siffror(v):
    return "".join(c for c in (v or "") if c.isdigit())


def skapa(fakturor, foretag, betaldatum=None):
    if not foretag.iban and not foretag.bankgiro:
        raise BetalfilFel("Ange företagets IBAN under Inställningar → Företagsuppgifter (kontot pengarna dras från).")
    if not foretag.iban:
        raise BetalfilFel("Betalfilen kräver företagets IBAN (det konto betalningarna dras från).")
    poster, fel = [], []
    for lf in fakturor:
        lev = lf.leverantor
        if not lev:
            fel.append(f"{lf}: saknar leverantör")
            continue
        if lev.bankgiro and _siffror(lev.bankgiro):
            konto, mmb, schema = _siffror(lev.bankgiro), "9900", "<Prtry>BGNR</Prtry>"
        elif lev.plusgiro and _siffror(lev.plusgiro):
            konto, mmb, schema = _siffror(lev.plusgiro), "9960", "<Cd>BBAN</Cd>"
        else:
            fel.append(f"{lev.namn}: saknar bankgiro/plusgiro")
            continue
        poster.append((lf, konto, mmb, schema))
    if fel:
        raise BetalfilFel("; ".join(fel))
    if not poster:
        raise BetalfilFel("Välj minst en faktura.")

    nu = timezone.now()
    msg_id = f"BITERO{nu:%Y%m%d%H%M%S}"
    datum = betaldatum or timezone.localdate()
    total = sum((lf.belopp for lf, *_ in poster), Decimal(0))
    x = []
    a = x.append
    a('<?xml version="1.0" encoding="UTF-8"?>')
    a('<Document xmlns="urn:iso:std:iso:20022:tech:xsd:pain.001.001.03" '
      'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">')
    a("<CstmrCdtTrfInitn><GrpHdr>")
    a(f"<MsgId>{msg_id}</MsgId><CreDtTm>{nu:%Y-%m-%dT%H:%M:%S}</CreDtTm>")
    a(f"<NbOfTxs>{len(poster)}</NbOfTxs><CtrlSum>{total:.2f}</CtrlSum>")
    a(f"<InitgPty><Nm>{escape(foretag.namn[:70])}</Nm>")
    if foretag.orgnr:
        a(f"<Id><OrgId><Othr><Id>{_siffror(foretag.orgnr)}</Id><SchmeNm><Cd>BANK</Cd></SchmeNm></Othr></OrgId></Id>")
    a("</InitgPty></GrpHdr>")
    a(f"<PmtInf><PmtInfId>{msg_id}-1</PmtInfId><PmtMtd>TRF</PmtMtd><BtchBookg>false</BtchBookg>")
    a(f"<NbOfTxs>{len(poster)}</NbOfTxs><CtrlSum>{total:.2f}</CtrlSum>")
    a("<PmtTpInf><SvcLvl><Cd>NURG</Cd></SvcLvl><CtgyPurp><Cd>SUPP</Cd></CtgyPurp></PmtTpInf>")
    a(f"<ReqdExctnDt>{datum:%Y-%m-%d}</ReqdExctnDt>")
    a(f"<Dbtr><Nm>{escape(foretag.namn[:70])}</Nm></Dbtr>")
    a(f"<DbtrAcct><Id><IBAN>{foretag.iban.replace(' ', '')}</IBAN></Id><Ccy>SEK</Ccy></DbtrAcct>")
    if foretag.bic:
        a(f"<DbtrAgt><FinInstnId><BIC>{foretag.bic.strip()}</BIC></FinInstnId></DbtrAgt>")
    else:
        a("<DbtrAgt><FinInstnId/></DbtrAgt>")
    for i, (lf, konto, mmb, schema) in enumerate(poster, 1):
        a("<CdtTrfTxInf>")
        a(f"<PmtId><InstrId>{lf.pk}</InstrId><EndToEndId>LF{lf.pk}</EndToEndId></PmtId>")
        a(f'<Amt><InstdAmt Ccy="SEK">{lf.belopp:.2f}</InstdAmt></Amt>')
        a(f"<CdtrAgt><FinInstnId><ClrSysMmbId><ClrSysId><Cd>SESBA</Cd></ClrSysId><MmbId>{mmb}</MmbId>"
          "</ClrSysMmbId></FinInstnId></CdtrAgt>")
        a(f"<Cdtr><Nm>{escape(lf.leverantor.namn[:70])}</Nm></Cdtr>")
        a(f"<CdtrAcct><Id><Othr><Id>{konto}</Id><SchmeNm>{schema}</SchmeNm></Othr></Id></CdtrAcct>")
        ocr = _siffror(lf.ocr)
        if ocr and _ocr_giltig(ocr):
            a("<RmtInf><Strd><CdtrRefInf><Tp><CdOrPrtry><Cd>SCOR</Cd></CdOrPrtry></Tp>"
              f"<Ref>{ocr}</Ref></CdtrRefInf></Strd></RmtInf>")
        else:
            ref = lf.ocr or (f"Faktura {lf.fakturanummer}" if lf.fakturanummer else lf.beskrivning) or "Betalning"
            a(f"<RmtInf><Ustrd>{escape(ref[:140])}</Ustrd></RmtInf>")
        a("</CdtTrfTxInf>")
    a("</PmtInf></CstmrCdtTrfInitn></Document>")
    return "\n".join(x).encode("utf-8"), len(poster), total
