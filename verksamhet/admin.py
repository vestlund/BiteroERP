from django.contrib import admin

from .models import Faktura, Fakturarad, Tidpost, Uppdrag


@admin.register(Uppdrag)
class UppdragAdmin(admin.ModelAdmin):
    list_display = ["namn", "kund", "artikel", "timpris", "startdatum", "slutdatum", "aktiv"]
    list_filter = ["aktiv", "kund"]
    search_fields = ["namn", "kund__namn"]


@admin.register(Tidpost)
class TidpostAdmin(admin.ModelAdmin):
    list_display = ["datum", "uppdrag", "timmar", "beskrivning", "fakturerbar", "fakturerad"]
    list_filter = ["uppdrag__kund", "fakturerbar"]
    date_hierarchy = "datum"


class FakturaradInline(admin.TabularInline):
    model = Fakturarad
    extra = 0


@admin.register(Faktura)
class FakturaAdmin(admin.ModelAdmin):
    list_display = ["nummer", "kund", "fakturadatum", "forfallodatum", "status"]
    list_filter = ["status", "kund"]
    inlines = [FakturaradInline]
