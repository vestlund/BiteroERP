from django.apps import AppConfig


class LoggConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "logg"
    verbose_name = "Behandlingshistorik"

    def ready(self):
        from . import sparning
        sparning.koppla_in()
