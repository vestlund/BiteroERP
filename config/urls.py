from django.contrib import admin
from django.urls import path

from bokforing import io_views as io
from bokforing import views as b
from budget import views as bu
from logg import views as lg
from lon import views as l
from planering import views as pl
from register import views as r
from verksamhet import views as v


def kund_lista(request):
    return r.lista(request, "kunder")


urlpatterns = [
    path("", v.oversikt, name="oversikt"),
    path("sok/", v.sok, name="sok"),
    # planering
    path("planering/likviditet/", pl.likviditetssida, name="likviditet"),
    path("planering/viktiga-datum/", pl.fristsida, name="frister"),
    path("planering/skatt/", pl.skattesida, name="skatt"),
    path("planering/viktiga-datum/frister.ics", pl.frister_ics, name="frister_ics"),
    # budget
    path("budget/", bu.uppfoljning, name="budget_aktuell"),
    path("budget/budgetar/", bu.lista, name="budgetar"),
    path("budget/ny/", bu.ny, name="budget_ny"),
    path("budget/<int:pk>/", bu.uppfoljning, name="budget_uppfoljning"),
    path("budget/<int:pk>/redigera/", bu.redigera, name="budget_redigera"),
    path("budget/<int:pk>/radera/", bu.radera, name="budget_radera"),
    # import och export
    path("installningar/import-export/", io.lista, name="io_lista"),
    path("installningar/import-export/<slug:slug>/", io.post, name="io_post"),
    # lön
    path("lon/korningar/", l.korningar, name="lonekorningar"),
    path("lon/korningar/ny/", l.korning_ny, name="lonekorning_ny"),
    path("lon/korningar/<int:pk>/", l.korning, name="lonekorning"),
    path("lon/korningar/<int:pk>/utskrift/", l.lonespec_utskrift, name="lonespec_utskrift"),
    path("lon/korningar/<int:pk>/<str:atgard>/", l.korning_atgard, name="lonekorning_atgard"),
    path("lon/spec/<int:pk>/", l.lonespec, name="lonespec"),
    path("lon/anstallda/", l.anstallda, name="anstallda"),
    path("lon/anstallda/ny/", l.anstalld_form, name="anstalld_ny"),
    path("lon/anstallda/<int:pk>/", l.anstalld, name="anstalld"),
    path("lon/anstallda/<int:pk>/andra/", l.anstalld_form, name="anstalld_andra"),
    path("lon/agi/", l.arbetsgivardeklaration, name="agi"),
    path("lon/agi/fil/", l.agi_fil, name="agi_fil"),
    path("installningar/behandlingshistorik/", lg.lista, name="logg"),
    path("installningar/behandlingshistorik/export.csv", lg.export_csv, name="logg_csv"),
    path("installningar/behandlingshistorik/<int:pk>/", lg.handelse, name="logg_handelse"),
    path("installningar/systemversion/<int:pk>/", lg.systemversion, name="logg_systemversion"),
    path("installningar/systemdokumentation/", lg.systemdokumentation, name="systemdokumentation"),
    path("installningar/skattetabeller/", l.skattetabeller, name="skattetabeller"),
    # tid och uppdrag
    path("tid/", v.tidrapport, name="tidrapport"),
    path("tid/cell/", v.tid_cell, name="tid_cell"),
    path("tid/<int:pk>/", v.tidpost_andra, name="tidpost_andra"),
    path("tid/export.csv", v.tid_export_csv, name="tid_export_csv"),
    path("uppdrag/", v.uppdrag_lista, name="uppdrag_lista"),
    path("uppdrag/uppfoljning/", v.uppdragsuppfoljning, name="uppdragsuppfoljning"),
    path("uppdrag/nytt/", v.uppdrag_form, name="uppdrag_nytt"),
    path("uppdrag/<int:pk>/", v.uppdrag_detalj, name="uppdrag_detalj"),
    path("uppdrag/<int:pk>/andra/", v.uppdrag_form, name="uppdrag_andra"),
    # kunder och fakturor
    path("kunder/", kund_lista, name="kund_lista"),
    path("kunder/<int:pk>/", v.kund_detalj, name="kund_detalj"),
    path("fakturor/", v.faktura_lista, name="faktura_lista"),
    path("fakturor/fakturera-tid/", v.fakturera_tid, name="fakturera_tid"),
    path("fakturor/ny/", v.faktura_ny, name="faktura_ny"),
    path("fakturor/export.csv", v.fakturor_csv, name="fakturor_csv"),
    path("fakturor/<int:pk>/", v.faktura_detalj, name="faktura_detalj"),
    path("fakturor/<int:pk>/utskrift/", v.faktura_utskrift, name="faktura_utskrift"),
    path("fakturor/<int:pk>/epost/", v.faktura_epost, name="faktura_epost"),
    path("fakturor/<int:pk>/paminnelse/", v.faktura_paminnelse, name="faktura_paminnelse"),
    path("fakturor/<int:pk>/<str:atgard>/", v.faktura_atgard, name="faktura_atgard"),
    # bokföring
    path("bokforing/", b.bokforing_start, name="bokforing"),
    path("bokforing/verifikationer/", b.verifikationer, name="verifikationer"),
    path("bokforing/verifikationer/ny/", b.verifikation_ny, name="verifikation_ny"),
    path("bokforing/verifikationer/<int:pk>/", b.verifikation, name="verifikation"),
    path("bokforing/verifikationer/<int:pk>/ratta/", b.verifikation_ratta, name="verifikation_ratta"),
    path("bokforing/bilaga/<int:pk>/", b.bilaga, name="bilaga"),
    path("inkop/", b.levfakturor, name="levfakturor"),
    path("inkop/ny/", b.levfaktura_ny, name="levfaktura_ny"),
    path("inkop/tolka/", b.levfaktura_tolka, name="levfaktura_tolka"),
    path("inkop/<int:pk>/", b.levfaktura, name="levfaktura"),
    path("inkop/<int:pk>/betala/", b.levfaktura_betala, name="levfaktura_betala"),
    path("inkop/<int:pk>/makulera/", b.levfaktura_makulera, name="levfaktura_makulera"),
    path("bank/", b.bank, name="bank"),
    path("bank/<int:pk>/", b.bankpost_hantera, name="bankpost_hantera"),
    path("bokforing/moms/", b.moms, name="moms"),
    path("bokforing/moms/avrakna/", b.moms_avrakna, name="moms_avrakna"),
    path("bokforing/moms/fil/", b.moms_fil, name="moms_fil"),
    path("bokforing/rapporter/", b.rapporter, name="rapporter"),
    path("bokforing/huvudbok/", b.huvudbok, name="huvudbok"),
    path("bokforing/sie/", b.sie_sida, name="sie"),
    path("bokforing/arsarkiv/", b.arsarkiv, name="arsarkiv"),
    path("bokforing/bokslut/", b.bokslutssida, name="bokslut"),
    # register
    path("register/foretag/", r.foretag, name="foretag"),
    path("register/<str:typ>/", r.lista, name="register_lista"),
    path("register/<str:typ>/ny/", r.form, name="register_ny"),
    path("register/<str:typ>/<int:pk>/", r.form, name="register_andra"),
    path("admin/", admin.site.urls),
]
