"""QuerySet som loggar massuppdateringar (QuerySet.update) i behandlingshistoriken.

Djangos update() skickar inga signaler, så ändringar gjorda med den skulle annars inte synas i
historiken. Varje spårad modell använder därför `objects = LoggadQuerySet.as_manager()`.
Testet tester.test_logg.Managers kontrollerar att ingen spårad modell saknar den.
"""

from django.db import models


class LoggadQuerySet(models.QuerySet):
    def update(self, **kwargs):
        from .sparning import uppdatera_med_logg
        return uppdatera_med_logg(self, super().update, kwargs)
