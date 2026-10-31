"""Import och export av register i Visma eEkonomis CSV-format (semikolon, UTF-8 med BOM).

Kolumnordningen är densamma som i Vismas egna exporter, så filerna kan läsas in i Visma igen.
"""

import csv
import io
from decimal import Decimal

from django.db import transaction

from .management.commands.importera_visma import Command as Importor
from .models import Artikel, Konto, Kostnadsstalle, Kund, Leverantor

RUBRIKER = {
    "kunder": ("Customers", "IsActive;CustomerNumber;Name;IsPrivatePerson;CorporateIdentityNumber;InvoiceAddress1;"
               "InvoiceAddress2;InvoicePostalCode;InvoiceCity;InvoiceCountryCode;SalesDocumentLanguage;EdiGlnNumber;"
               "VatNumber;Telephone;MobilePhone;EmailAddress;CcEmailAddresses;WwwAddress;Note;ForceBookkeepVat;"
               "ContactPersonName;ContactPersonEmail;ContactPersonPhone;ContactPersonMobile;TermOfDeliveryName;"
               "DeliveryMethodName;DeliveryCustomerName;DeliveryAddress1;DeliveryAddress2;DeliveryPostalCode;"
               "DeliveryCity;DeliveryCountryCode;TermsOfPaymentCode;CurrencyCode;PayToAccount;DiscountPercentage;"
               "CustomerLabels"),
    "leverantorer": ("Suppliers", "IsActive;SupplierNumber;Name;CorporateIdentityNumber;Address1;Address2;PostalCode;"
                     "City;CountryCode;Telephone;MobilePhone;EmailAddress;WwwAddress;Note;ContactPersonName;"
                     "ContactPersonEmail;ContactPersonPhone;ContactPersonMobile;TermsOfPaymentCode;CurrencyCode;"
                     "BankgiroNumber;PlusgiroNumber;Ocr;AutomaticPaymentService;BankName;BankCountryCode;BankBic;"
                     "BankIban;BankBban;BankCode;ForeignPaymentCode;BankFeeCode;PayFromBankAccount"),
    "artiklar": ("Articles", "IsActive;Number;Name;NameEnglish;Notes;Barcode;ArticleAccountCoding;Unit;NetPrice;"
                 "PurchasePrice;StockBalance"),
    "konton": ("ChartAccounts", "IsActive;AccountNumber;AccountName;VatCodeAndPercent;IsCostCenterAllowed;"
               "IsProjectAllowed;AllowTransactionText;IsBlockedForManualBooking"),
    "kostnadsstallen": ("CostCenters", "CostCenterNumber;CostCenterItemIsActive;CostCenterItemName;"
                        "CostCenterItemShortName"),
}

NAMN = {"kunder": "kundregister", "leverantorer": "leverantörsregister", "artiklar": "artikelregister",
        "konton": "kontoplan", "kostnadsstallen": "resultatenheter (kostnadsställen)"}


def _b(v):
    return "True" if v else "False"


def _d(v, dec=4):
    return f"{Decimal(v or 0):.{dec}f}".replace(".", ",")


def _villkor(o):
    return o.betalningsvillkor.kod if o.betalningsvillkor else ""


def _rader(typ):
    if typ == "kunder":
        for k in Kund.objects.select_related("betalningsvillkor").order_by("nummer"):
            yield [_b(k.aktiv), k.nummer, k.namn, _b(k.privatperson), k.orgnr, k.adress1, k.adress2, k.postnummer,
                   k.ort, k.landkod, k.sprak, k.gln, k.momsnummer, k.telefon, k.mobil, k.epost, k.kopia_epost, k.webb,
                   k.anteckning, "False", k.kontaktperson, k.kontakt_epost, k.kontakt_telefon, k.kontakt_mobil, "", "",
                   k.leverans_namn, k.leverans_adress1, k.leverans_adress2, k.leverans_postnummer, k.leverans_ort,
                   k.landkod, _villkor(k), k.valuta, "", _d(k.rabatt_procent), k.etiketter]
    elif typ == "leverantorer":
        for l in Leverantor.objects.select_related("betalningsvillkor").order_by("nummer"):
            yield [_b(l.aktiv), l.nummer, l.namn, l.orgnr, l.adress1, l.adress2, l.postnummer, l.ort, l.landkod,
                   l.telefon, l.mobil, l.epost, l.webb, l.anteckning, l.kontaktperson, l.kontakt_epost,
                   l.kontakt_telefon, l.kontakt_mobil, _villkor(l), l.valuta, l.bankgiro, l.plusgiro, _b(l.ocr),
                   _b(l.autogiro), l.bank, l.landkod, l.bic, l.iban, l.bban, "", "", "0", ""]
    elif typ == "artiklar":
        for a in Artikel.objects.order_by("nummer"):
            yield [_b(a.aktiv), a.nummer, a.namn, a.namn_engelska, a.anteckning, "", a.visma_kontering, a.enhet,
                   _d(a.pris), _d(a.inkopspris), _d(0)]
    elif typ == "konton":
        for k in Konto.objects.order_by("nummer"):
            yield [_b(k.aktiv), k.nummer, k.namn, k.momskod, _b(k.kostnadsstalle_tillatet), _b(k.projekt_tillatet),
                   "True", _b(k.spärrat_manuell_bokning)]
    elif typ == "kostnadsstallen":
        for k in Kostnadsstalle.objects.order_by("nummer"):
            yield [k.nummer, _b(k.aktiv), k.namn, k.kortnamn]


def exportera(typ):
    filnamn, rubrik = RUBRIKER[typ]
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    w.writerow(rubrik.split(";"))
    for rad in _rader(typ):
        w.writerow(["" if v is None else v for v in rad])
    return ("﻿" + buf.getvalue()).encode("utf-8"), filnamn


@transaction.atomic
def importera(typ, data: bytes):
    text = data.decode("utf-8-sig", errors="replace")
    rader = [r for r in csv.DictReader(io.StringIO(text), delimiter=";")
             if any((v or "").strip() for v in r.values())]
    forvantade = RUBRIKER[typ][1].split(";")
    saknas = [k for k in forvantade[:3] if rader and k not in rader[0]]
    if not rader:
        raise ValueError("Filen innehåller inga rader.")
    if saknas:
        raise ValueError(f"Filen ser inte ut som en Visma-export av {NAMN[typ]} (saknar {', '.join(saknas)}).")
    imp = Importor()
    funktion = {"kunder": imp.kunder, "leverantorer": imp.leverantorer, "artiklar": imp.artiklar,
                "konton": imp.konton, "kostnadsstallen": imp.kostnadsstallen}[typ]
    return funktion(rader)
