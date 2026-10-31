"""Importerar register från Visma eEkonomi-exporter (semikolonseparerad CSV, UTF-8).

Användning:
    python manage.py importera_visma            # läser från mappen import/
    python manage.py importera_visma --mapp X   # annan mapp

Kör gärna om när du har nya exporter: befintliga poster uppdateras på nummer,
nya läggs till, inget raderas.
"""

import csv
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from register.models import Artikel, Betalningsvillkor, Foretag, Konto, Kostnadsstalle, Kund, Leverantor

# Vismas konteringskod på artiklar -> intäktskonto här. Justera vid behov.
ARTIKEL_KONTERING = {
    "70": 3041,   # Tjänster 25 % (alla timartiklar i exporten)
    "110": 3041,  # Tjänst, fast pris
    "30": 3540,   # Faktureringsavgift (Expeditionsavgift)
}
STANDARD_INTAKTSKONTO = 3041

ENHET = {"HUR": "HUR", "PCE": "PCE", "DAY": "DAY", "MON": "MON"}


def las(fil):
    with open(fil, encoding="utf-8-sig", newline="") as f:
        rader = list(csv.DictReader(f, delimiter=";"))
    # Hoppa över tomma rader (t.ex. ';;;')
    return [r for r in rader if any((v or "").strip() for v in r.values())]


def b(v):
    return (v or "").strip().lower() == "true"


def d(v):
    v = (v or "").strip().replace(" ", "").replace(",", ".")
    try:
        return Decimal(v) if v else Decimal(0)
    except InvalidOperation:
        return Decimal(0)


def s(v):
    return (v or "").strip()


class Command(BaseCommand):
    help = "Importerar kontoplan, kostnadsställen, kunder, leverantörer och artiklar från Visma-exporter"

    def add_arguments(self, parser):
        parser.add_argument("--mapp", default=str(settings.IMPORT_DIR))

    def hitta(self, mapp, namn):
        filer = sorted(Path(mapp).glob(f"*{namn}_Export*.csv"))
        return filer[-1] if filer else None

    @transaction.atomic
    def handle(self, *args, **opts):
        mapp = opts["mapp"]
        steg = [
            ("ChartAccounts", self.konton),
            ("CostCenters", self.kostnadsstallen),
            ("Customers", self.kunder),
            ("Suppliers", self.leverantorer),
            ("Articles", self.artiklar),
        ]
        for namn, funktion in steg:
            fil = self.hitta(mapp, namn)
            if not fil:
                self.stdout.write(self.style.WARNING(f"Hittade ingen {namn}-fil i {mapp}"))
                continue
            antal = funktion(las(fil))
            self.stdout.write(f"{fil.name}: {antal} poster")

        foretag = Foretag.hamta()
        if not foretag.kundfordringskonto_id and Konto.objects.filter(pk=1510).exists():
            foretag.kundfordringskonto_id = 1510
            foretag.save()
        from bokforing.avstamning import skapa_standardregler
        skapa_standardregler()
        self.stdout.write(self.style.SUCCESS("Import klar."))

    def villkor(self, kod):
        kod = s(kod)
        if not kod:
            return None
        # Antagande: koden motsvarar antal dagar netto. Kontrollera under Register > Betalningsvillkor.
        dagar = int(kod) if kod.isdigit() else 30
        obj, _ = Betalningsvillkor.objects.get_or_create(
            kod=kod, defaults={"dagar": dagar, "beskrivning": f"{dagar} dagar netto"}
        )
        return obj

    def konton(self, rader):
        for r in rader:
            Konto.objects.update_or_create(
                nummer=int(r["AccountNumber"]),
                defaults=dict(
                    namn=s(r["AccountName"]),
                    aktiv=b(r["IsActive"]),
                    momskod=s(r["VatCodeAndPercent"]),
                    kostnadsstalle_tillatet=b(r["IsCostCenterAllowed"]),
                    projekt_tillatet=b(r["IsProjectAllowed"]),
                    spärrat_manuell_bokning=b(r["IsBlockedForManualBooking"]),
                ),
            )
        return len(rader)

    def kostnadsstallen(self, rader):
        for r in rader:
            if not s(r["CostCenterNumber"]):
                continue
            Kostnadsstalle.objects.update_or_create(
                nummer=s(r["CostCenterNumber"]),
                defaults=dict(
                    namn=s(r["CostCenterItemName"]),
                    kortnamn=s(r["CostCenterItemShortName"]),
                    aktiv=b(r["CostCenterItemIsActive"]),
                ),
            )
        return len(rader)

    def gemensamt(self, r, adressprefix):
        return dict(
            namn=s(r["Name"]),
            aktiv=b(r["IsActive"]),
            orgnr=s(r["CorporateIdentityNumber"]),
            adress1=s(r[f"{adressprefix}Address1"]),
            adress2=s(r[f"{adressprefix}Address2"]),
            postnummer=s(r[f"{adressprefix}PostalCode"]),
            ort=s(r[f"{adressprefix}City"]),
            landkod=s(r[f"{adressprefix}CountryCode"]) or "SE",
            telefon=s(r["Telephone"]),
            mobil=s(r["MobilePhone"]),
            epost=s(r["EmailAddress"]),
            webb=s(r["WwwAddress"]),
            anteckning=s(r["Note"]),
            kontaktperson=s(r["ContactPersonName"]),
            kontakt_epost=s(r["ContactPersonEmail"]),
            kontakt_telefon=s(r["ContactPersonPhone"]),
            kontakt_mobil=s(r["ContactPersonMobile"]),
            valuta=s(r["CurrencyCode"]) or "SEK",
        )

    def kunder(self, rader):
        for r in rader:
            data = self.gemensamt(r, "Invoice")
            data.update(
                privatperson=b(r["IsPrivatePerson"]),
                sprak=s(r["SalesDocumentLanguage"]) or "sv",
                gln=s(r["EdiGlnNumber"]),
                momsnummer=s(r["VatNumber"]),
                kopia_epost=s(r["CcEmailAddresses"]),
                leverans_namn=s(r["DeliveryCustomerName"]),
                leverans_adress1=s(r["DeliveryAddress1"]),
                leverans_adress2=s(r["DeliveryAddress2"]),
                leverans_postnummer=s(r["DeliveryPostalCode"]),
                leverans_ort=s(r["DeliveryCity"]),
                rabatt_procent=d(r["DiscountPercentage"]),
                etiketter=s(r["CustomerLabels"]),
                betalningsvillkor=self.villkor(r["TermsOfPaymentCode"]),
            )
            Kund.objects.update_or_create(nummer=int(r["CustomerNumber"]), defaults=data)
        return len(rader)

    def leverantorer(self, rader):
        for r in rader:
            data = self.gemensamt(r, "")
            data.update(
                bankgiro=s(r["BankgiroNumber"]).lstrip("0") and s(r["BankgiroNumber"]),
                plusgiro=s(r["PlusgiroNumber"]),
                ocr=b(r["Ocr"]),
                autogiro=b(r["AutomaticPaymentService"]),
                bank=s(r["BankName"]),
                bic=s(r["BankBic"]),
                iban=s(r["BankIban"]),
                bban=s(r["BankBban"]),
                betalningsvillkor=self.villkor(r["TermsOfPaymentCode"]),
            )
            Leverantor.objects.update_or_create(nummer=int(r["SupplierNumber"]), defaults=data)
        return len(rader)

    def artiklar(self, rader):
        for r in rader:
            kodning = s(r["ArticleAccountCoding"])
            kontonr = ARTIKEL_KONTERING.get(kodning, STANDARD_INTAKTSKONTO)
            konto = Konto.objects.filter(pk=kontonr).first()
            Artikel.objects.update_or_create(
                nummer=s(r["Number"]),
                defaults=dict(
                    namn=s(r["Name"]),
                    namn_engelska=s(r["NameEnglish"]),
                    anteckning=s(r["Notes"]),
                    aktiv=b(r["IsActive"]),
                    enhet=ENHET.get(s(r["Unit"]), "PCE"),
                    pris=d(r["NetPrice"]),
                    inkopspris=d(r["PurchasePrice"]),
                    visma_kontering=kodning,
                    intaktskonto=konto,
                    momssats=(konto.momssats if konto and konto.momssats is not None else Decimal(25)),
                ),
            )
        return len(rader)
