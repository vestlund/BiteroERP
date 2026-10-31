"""Filerna till Skatteverket: momsdeklaration (eSKD) och arbetsgivardeklaration (AGI)."""

import xml.etree.ElementTree as ET

from django.test import TestCase
from django.urls import reverse

from bokforing import skatteverket
from bokforing import tjanster as bok
from bokforing.models import Leverantorsfaktura
from lon import agifil
from lon import tjanster as lon
from lon.models import Anstalld, Lonekorning, Lonerad, Lonespec
from register.models import Foretag

from .grund import anvandare, d, datum, faktura, grunddata

NS = {"i": agifil.INSTANS, "agd": agifil.KOMPONENT}


class Momsfil(TestCase):
    def setUp(self):
        kund, lev = grunddata()
        f = faktura(kund, timmar="1", pris="10000.99", fakturadatum="2026-02-10")
        f.slutfor()
        bok.bokfor_kundfaktura(f)
        lf = Leverantorsfaktura.objects.create(leverantor=lev, belopp=d("1250.75"), moms=d("250.15"),
                                               konto_id=5410, fakturadatum=datum("2026-02-15"))
        bok.bokfor_levfaktura(lf)

    def test_fil(self):
        namn, data = skatteverket.momsfil(datum("2026-01-01"), datum("2026-03-31"), Foretag.hamta())
        self.assertEqual(namn, "moms_5560000000_202603.xml")
        text = data.decode("iso-8859-1")
        self.assertTrue(text.startswith('<?xml version="1.0" encoding="ISO-8859-1"?>\n<eSKDUpload Version="6.0">'))
        rot = ET.fromstring(data)
        self.assertEqual(rot.findtext("OrgNr"), "556000-0000")
        moms = rot.find("Moms")
        self.assertEqual(moms.findtext("Period"), "202603")
        # 10000,99 -> 10000; moms 2500,25 -> 2500; ingående 250,15 -> 250; 49 räknas på heltalen
        self.assertEqual([(e.tag, e.text) for e in moms], [
            ("Period", "202603"), ("ForsMomsEjAnnan", "10000"), ("MomsUtgHog", "2500"), ("MomsIngAvdr", "250"),
            ("MomsBetala", "2250")])

    def test_tom_period_ger_noll(self):
        _, data = skatteverket.momsfil(datum("2026-04-01"), datum("2026-06-30"), Foretag.hamta())
        self.assertIn(b"<MomsBetala>0</MomsBetala>", data)

    def test_aterbetalning_har_minustecken(self):
        rutor = {"48": 500}
        self.assertEqual(sum(rutor.get(r, 0) for r in skatteverket.UTGAENDE) - rutor["48"], -500)

    def test_orgnr_kravs(self):
        f = Foretag.hamta()
        f.orgnr = ""
        with self.assertRaises(skatteverket.DeklarationsFel):
            skatteverket.momsfil(datum("2026-01-01"), datum("2026-03-31"), f)

    def test_nedladdning(self):
        self.client.force_login(anvandare())
        r = self.client.get(reverse("moms_fil") + "?fran=2026-01-01&till=2026-03-31")
        self.assertEqual(r["Content-Type"], "application/xml; charset=iso-8859-1")
        self.assertEqual(self.client.get(reverse("moms")).status_code, 200)


class Agifil(TestCase):
    def setUp(self):
        grunddata()
        f = Foretag.hamta()
        f.var_referens, f.telefon, f.epost = "Anna Test", "070-1234567", "anna@example.com"
        f.save()
        self.anst = Anstalld.objects.create(nummer=7, fornamn="Anna", efternamn="Test", personnummer="800101-1234",
                                            manadslon=d("40000.50"), skattemetod="procent", skatteprocent=d("30"))
        k = Lonekorning.objects.create(period="2026-05", utbetalningsdatum=datum("2026-05-25"))
        spec = Lonespec.objects.create(korning=k, anstalld=self.anst)
        lon.standardrader(spec)
        Lonerad.objects.create(spec=spec, typ="formansbil", antal=1, a_pris=d("3000"))
        lon.uppdatera_skatt(spec)
        lon.bokfor_lonekorning(k)

    def test_fil(self):
        namn, data, info = agifil.agi_xml("2026-05", Foretag.hamta())
        self.assertEqual(namn, "agi_165560000000_202605.xml")
        rot = ET.fromstring(data)
        self.assertEqual(rot.tag, f"{{{agifil.INSTANS}}}Skatteverket")
        self.assertEqual(rot.get("omrade"), "Arbetsgivardeklaration")
        self.assertEqual([c.tag.split("}")[1] for c in rot], ["Avsandare", "Blankettgemensamt", "Blankett", "Blankett"])
        hu = rot.find(".//agd:HU", NS)
        self.assertEqual(hu.find("agd:ArbetsgivareHUGROUP/agd:AgRegistreradId", NS).text, "165560000000")
        self.assertEqual(hu.find("agd:RedovisningsPeriod", NS).get("faltkod"), "006")
        iu = rot.find(".//agd:IU", NS)
        varden = {e.tag.split("}")[1]: (e.get("faltkod"), e.text) for e in iu if e.text and e.text.strip()}
        self.assertEqual(varden["Specifikationsnummer"], ("570", "7"))
        self.assertEqual(varden["KontantErsattningUlagAG"], ("011", "40000"))
        self.assertEqual(varden["SkatteplBilformanUlagAG"], ("013", "3000"))
        self.assertEqual(varden["AvdrPrelSkatt"], ("001", "12900"))
        self.assertEqual(iu.find(".//agd:BetalningsmottagarId", NS).text, "198001011234")
        # fältordningen enligt Skatteverkets struktur
        ordning = [e.tag.split("}")[1] for e in iu]
        self.assertEqual(ordning, ["ArbetsgivareIUGROUP", "BetalningsmottagareIUGROUP", "RedovisningsPeriod",
                                   "Specifikationsnummer", "KontantErsattningUlagAG", "SkatteplBilformanUlagAG",
                                   "AvdrPrelSkatt"])
        self.assertEqual(info["skatt"], 12900)

    def test_kontaktperson_kravs(self):
        f = Foretag.hamta()
        f.var_referens = ""
        with self.assertRaisesMessage(agifil.AgiFel, "kontaktperson"):
            agifil.agi_xml("2026-05", f)

    def test_ingen_lon(self):
        with self.assertRaises(agifil.AgiFel):
            agifil.agi_xml("2026-06", Foretag.hamta())

    def test_nedladdning(self):
        self.client.force_login(anvandare())
        r = self.client.get(reverse("agi_fil") + "?period=2026-05")
        self.assertEqual(r["Content-Type"], "application/xml; charset=utf-8")
        r = self.client.get(reverse("agi_fil") + "?period=2026-06")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.client.get(reverse("agi") + "?period=2026-05").status_code, 200)
