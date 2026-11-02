"""Datum visas som ÅÅÅÅ-MM-DD i hela programmet."""

import datetime as dt

from django.template import Context, Template
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from bokforing.models import Bankpost

from .grund import anvandare, d, grunddata


class Datumformat(TestCase):
    def test_mallar(self):
        tid = timezone.make_aware(dt.datetime(2026, 3, 5, 14, 30))
        t = Template('{{ d }}|{{ d|date }}|{{ d|date:"SHORT_DATE_FORMAT" }}|{{ t }}|{{ t|date:"DATETIME_FORMAT" }}')
        self.assertEqual(t.render(Context({"d": dt.date(2026, 3, 5), "t": tid})),
                         "2026-03-05|2026-03-05|2026-03-05|2026-03-05 14:30|2026-03-05 14:30")

    def test_sidor(self):
        grunddata()
        self.client.force_login(anvandare())
        Bankpost.objects.create(datum=dt.date(2026, 4, 2), text="AVGIFT", belopp=d("-25"), konto_id=1930, nyckel="x")
        # även med en engelskspråkig webbläsare
        sida = self.client.get(reverse("bank"), HTTP_ACCEPT_LANGUAGE="en-US,en;q=0.9").content.decode()
        self.assertIn("2026-04-02", sida)
        self.assertNotIn("April", sida)
        self.assertIn("Bokför", sida)
        self.assertIn("datum.js", sida)
