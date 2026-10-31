from django.contrib import admin

from .models import Artikel, Betalningsvillkor, Foretag, Konto, Kostnadsstalle, Kund, Leverantor

admin.site.site_header = "Konsultsystem – register"
admin.site.site_title = "Konsultsystem"
admin.site.index_title = "Register och inställningar"
admin.site.site_url = "/"


@admin.register(Konto)
class KontoAdmin(admin.ModelAdmin):
    list_display = ["nummer", "namn", "aktiv", "momskod", "kostnadsstalle_tillatet", "projekt_tillatet"]
    list_filter = ["aktiv", "momskod"]
    search_fields = ["nummer", "namn"]
    list_editable = ["aktiv"]
    list_per_page = 100


@admin.register(Kostnadsstalle)
class KostnadsstalleAdmin(admin.ModelAdmin):
    list_display = ["nummer", "namn", "kortnamn", "aktiv"]


@admin.register(Betalningsvillkor)
class BetalningsvillkorAdmin(admin.ModelAdmin):
    list_display = ["kod", "dagar", "beskrivning"]
    list_editable = ["dagar", "beskrivning"]


PART_FIELDSETS = [
    (None, {"fields": [("nummer", "aktiv"), "namn", "orgnr"]}),
    ("Adress", {"fields": ["adress1", "adress2", ("postnummer", "ort", "landkod")]}),
    ("Kontakt", {"fields": [("telefon", "mobil"), ("epost", "webb"),
                            "kontaktperson", ("kontakt_epost", "kontakt_telefon", "kontakt_mobil")]}),
]


@admin.register(Kund)
class KundAdmin(admin.ModelAdmin):
    list_display = ["nummer", "namn", "orgnr", "ort", "kontaktperson", "betalningsvillkor", "aktiv"]
    list_filter = ["aktiv", "betalningsvillkor"]
    search_fields = ["nummer", "namn", "orgnr", "kontaktperson", "epost"]
    fieldsets = PART_FIELDSETS + [
        ("Fakturering", {"fields": [("betalningsvillkor", "valuta", "rabatt_procent"), ("sprak", "privatperson"),
                                    ("momsnummer", "gln"), "kopia_epost"]}),
        ("Leveransadress", {"classes": ["collapse"], "fields": [
            "leverans_namn", "leverans_adress1", "leverans_adress2", ("leverans_postnummer", "leverans_ort")]}),
        ("Övrigt", {"fields": ["etiketter", "anteckning"]}),
    ]


@admin.register(Leverantor)
class LeverantorAdmin(admin.ModelAdmin):
    list_display = ["nummer", "namn", "orgnr", "bankgiro", "plusgiro", "betalningsvillkor", "aktiv"]
    list_filter = ["aktiv", "betalningsvillkor"]
    search_fields = ["nummer", "namn", "orgnr", "bankgiro"]
    fieldsets = PART_FIELDSETS + [
        ("Betalning", {"fields": [("betalningsvillkor", "valuta"), ("bankgiro", "plusgiro"), ("ocr", "autogiro"),
                                  ("bank", "bic"), ("iban", "bban")]}),
        ("Övrigt", {"fields": ["anteckning"]}),
    ]


@admin.register(Artikel)
class ArtikelAdmin(admin.ModelAdmin):
    list_display = ["nummer", "namn", "enhet", "pris", "intaktskonto", "momssats", "aktiv"]
    list_filter = ["aktiv", "enhet"]
    search_fields = ["nummer", "namn"]
    list_editable = ["pris", "aktiv"]
    autocomplete_fields = ["intaktskonto"]


@admin.register(Foretag)
class ForetagAdmin(admin.ModelAdmin):
    fieldsets = [
        (None, {"fields": ["namn", ("orgnr", "momsnummer"), "f_skatt"]}),
        ("Adress och kontakt", {"fields": ["adress", ("postnummer", "ort"), ("telefon", "epost", "webb")]}),
        ("Betalning", {"fields": [("bankgiro", "plusgiro"), ("iban", "bic"), "drojsmalsranta"]}),
        ("Fakturering", {"fields": ["var_referens", "nasta_fakturanummer", "kundfordringskonto", "standardartikel"]}),
    ]
    autocomplete_fields = ["kundfordringskonto"]

    def has_add_permission(self, request):
        return not Foretag.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False
