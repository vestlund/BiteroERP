"""Behandlingshistoriken: att allt loggas, att loggen inte kan ändras och att manipulation upptäcks."""

import tempfile
from pathlib import Path

from django.db import connection
from django.test import TestCase

from bokforing import tjanster as bok
from logg import sparning
from logg.models import Handelse, OforanderligLogg, Systemversion
from register.models import Kund
from verksamhet.models import Faktura

from .grund import anvandare, d, datum, faktura, grunddata


class Loggning(TestCase):
    def setUp(self):
        self.kund, self.lev = grunddata()

    def senaste(self, **kw):
        return Handelse.objects.filter(**kw).order_by("-id").first()

    def test_skapa_andra_radera(self):
        k = Kund.objects.create(nummer=2, namn="Ny Kund AB")
        h = self.senaste(modell="register.Kund", objekt_id=str(k.pk))
        self.assertEqual((h.handling, h.andringar["namn"]), ("skapad", "Ny Kund AB"))

        k.namn, k.ort = "Nytt Namn AB", "Göteborg"
        k.save()
        h = self.senaste(modell="register.Kund", objekt_id=str(k.pk))
        self.assertEqual(h.handling, "andrad")
        self.assertEqual(h.andringar, {"namn": ["Ny Kund AB", "Nytt Namn AB"], "ort": ["", "Göteborg"]})

        antal = Handelse.objects.count()
        k.save()
        self.assertEqual(Handelse.objects.count(), antal, "ingen händelse när inget ändrats")

        pk = k.pk
        k.delete()
        h = self.senaste(modell="register.Kund", objekt_id=str(pk))
        self.assertEqual((h.handling, h.andringar["namn"]), ("raderad", "Nytt Namn AB"))

    def test_massuppdatering_loggas(self):
        f = faktura(self.kund)
        f.slutfor()
        Faktura.objects.filter(pk=f.pk).update(status=Faktura.BETALD, betaldatum=datum("2026-04-01"))
        h = self.senaste(modell="verksamhet.Faktura", handling="andrad")
        self.assertEqual(h.andringar, {"status": ["skickad", "betald"], "betaldatum": [None, "2026-04-01"]})

    def test_bokforing_loggas_med_alla_rader(self):
        v = bok.bokfor(datum("2026-02-01"), "Bankavgift", [(6570, d("50")), (1930, d("-50"))], anvandare="test")
        h = self.senaste(modell="bokforing.Verifikation", objekt_id=str(v.pk))
        self.assertEqual(h.handling, "bokford")
        self.assertEqual(h.andringar["vernr"], "A1")
        self.assertEqual(h.andringar["rader"], [[6570, "50.00", ""], [1930, "-50.00", ""]])
        self.assertFalse(Handelse.objects.filter(modell="bokforing.Transaktion").exists())

    def test_misslyckad_bokforing_lamnar_inget_spar(self):
        antal = Handelse.objects.count()
        with self.assertRaises(bok.BokforingsFel):
            bok.bokfor(datum("2026-02-01"), "x", [(6570, 1), (9999, -1)])
        self.assertEqual(Handelse.objects.count(), antal)

    def test_losenord_loggas_inte(self):
        u = anvandare()
        u.set_password("nytt-losen-456")
        u.save()
        h = self.senaste(modell="auth.User", handling="andrad")
        self.assertEqual(h.andringar["password"], ["***", "*** (ändrat)"])
        self.assertNotIn("nytt-losen", str(Handelse.objects.values_list("andringar", flat=True)))

    def test_anvandare_och_sida_fran_webben(self):
        u = anvandare()
        self.client.force_login(u)
        self.client.post("/register/foretag/", {"namn": "Bitero AB", "nasta_fakturanummer": 180,
                                                "momsperiod": "kvartal", "drojsmalsranta": "x",
                                                "agarandel": "100", "sparat_utdelningsutrymme": "0"})
        h = self.senaste(modell="register.Foretag")
        self.assertEqual(h.anvandare, "test")
        self.assertEqual(h.kalla, "POST /register/foretag/")

    def test_inloggning_loggas(self):
        anvandare()
        self.client.login(username="test", password="fel")
        self.client.login(username="test", password="hemligt-123")
        resultat = list(Handelse.objects.filter(handling="inloggning").values_list("andringar__resultat", flat=True))
        self.assertEqual(resultat, ["lyckad", "misslyckad"])


class Manipulationsskydd(TestCase):
    def setUp(self):
        grunddata()
        for i in range(3):
            bok.bokfor(datum("2026-02-01"), f"Händelse {i}", [(6570, 1), (1930, -1)])

    def test_kedjan_ar_hel(self):
        k = sparning.kontrollera_kedjan()
        self.assertTrue(k["ok"])
        self.assertEqual(k["antal"], Handelse.objects.count())

    def test_loggen_kan_inte_andras_eller_raderas_via_systemet(self):
        h = Handelse.objects.first()
        h.objekt = "ändrat"
        with self.assertRaises(OforanderligLogg):
            h.save()
        with self.assertRaises(OforanderligLogg):
            h.delete()

    def test_andring_direkt_i_databasen_upptacks(self):
        h = Handelse.objects.filter(handling="bokford").order_by("id")[1]
        with connection.cursor() as c:
            c.execute("UPDATE logg_handelse SET objekt = 'förfalskad' WHERE id = %s", [h.pk])
        k = sparning.kontrollera_kedjan()
        self.assertFalse(k["ok"])
        self.assertEqual(k["fel"][0][0], h.pk)

    def test_borttagen_rad_upptacks(self):
        h = Handelse.objects.order_by("id")[2]
        with connection.cursor() as c:
            c.execute("DELETE FROM logg_handelse WHERE id = %s", [h.pk])
        self.assertFalse(sparning.kontrollera_kedjan()["ok"])


class Systemversioner(TestCase):
    def test_registreras_bara_vid_andring(self):
        with tempfile.TemporaryDirectory() as tmp:
            bas = Path(tmp)
            (bas / "app.py").write_text("print(1)\n")
            (bas / "templates").mkdir()
            (bas / "templates" / "a.html").write_text("<p>a</p>")
            (bas / ".venv").mkdir()
            (bas / ".venv" / "x.py").write_text("ignoreras")
            forsta = sparning.registrera_systemversion(bas, "1")
            self.assertEqual(sorted(forsta.filer), ["app.py", "templates/a.html"])
            self.assertIsNone(sparning.registrera_systemversion(bas, "1"))
            # Windows-radslut räknas inte som ändring
            (bas / "app.py").write_bytes(b"print(1)\r\n")
            self.assertIsNone(sparning.registrera_systemversion(bas, "1"))
            (bas / "app.py").write_text("print(2)\n")
            (bas / "ny.py").write_text("")
            (bas / "templates" / "a.html").unlink()
            andra = sparning.registrera_systemversion(bas, "2")
            self.assertEqual((andra.andrade, andra.tillagda, andra.borttagna), (["app.py"], ["ny.py"], ["templates/a.html"]))
        self.assertEqual(Systemversion.objects.count(), 2)
        h = Handelse.objects.filter(handling="system").order_by("-id").first()
        self.assertEqual(h.andringar["andrade"], ["app.py"])


class Managers(TestCase):
    def test_alla_sparade_modeller_loggar_massuppdateringar(self):
        from django.apps import apps

        from logg.managers import LoggadQuerySet
        saknas = [sparning.nyckel(m) for m in apps.get_models()
                  if sparning.spåras(m) and m._meta.app_label != "auth"
                  and not issubclass(m._default_manager._queryset_class, LoggadQuerySet)]
        self.assertEqual(saknas, [], "lägg till objects = LoggadQuerySet.as_manager() på modellen")

    def test_inget_ingrepp_i_djangos_queryset(self):
        from django.db.models import QuerySet
        self.assertEqual(QuerySet.update.__module__, "django.db.models.query")


class BokfordaPosterSkyddas(TestCase):
    def setUp(self):
        grunddata()
        self.v = bok.bokfor(datum("2026-02-01"), "x", [(6570, 1), (1930, -1)])

    def test_massandring_och_massradering_stoppas(self):
        from bokforing.models import OforanderligError, Transaktion, Verifikation
        with self.assertRaises(OforanderligError):
            Verifikation.objects.filter(pk=self.v.pk).update(text="ändrad")
        with self.assertRaises(OforanderligError):
            Verifikation.objects.all().delete()
        with self.assertRaises(OforanderligError):
            Transaktion.objects.all().delete()
        with self.assertRaises(OforanderligError):
            self.v.transaktioner.update(belopp=0)
        self.assertEqual(Transaktion.objects.count(), 2)

    def test_exportmarkering_ar_tillaten_och_loggas(self):
        from bokforing.models import Verifikation
        Verifikation.objects.filter(pk=self.v.pk).update(extern_id="A-1-20260201")
        h = Handelse.objects.filter(modell="bokforing.Verifikation", handling="andrad").get()
        self.assertEqual(h.andringar, {"extern_id": [None, "A-1-20260201"]})
