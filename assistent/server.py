"""MCP-server som låter Claude läsa och bokföra i Bitero ERP. Startas av Claude-appen med `manage.py mcp`."""

import functools

from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations

from logg.sparning import aterstall_kontext, satt_kontext

from . import arbete as a
from . import verktyg as v

INSTRUKTIONER = """Bitero ERP är bokföringssystemet för Bitero AB (svenskt aktiebolag, BAS-kontoplan, kalenderår).

Så bokför du ett kvitto eller en leverantörsfaktura:
1. Underlaget ska ligga i inkorgen. Använd `inkorg` för att se vilka filer som finns. Om användaren har laddat upp
   en bild i chatten och den inte finns i inkorgen: be användaren spara den i inkorgsmappen.
2. Läs underlaget med `visa_underlag`. Ta fram datum, säljare, totalbelopp inkl. moms och momsbelopp. På svenska
   kvitton står moms ofta som "Moms 25 %" eller "Varav moms". Restaurang/livsmedel har ofta 12 %, böcker och
   persontransporter 6 %.
3. Hitta leverantören med `sok_leverantor` (för fakturor krävs leverantör; skapa med `ny_leverantor` om den saknas).
   För kvitton från butiker och restauranger räcker `motpart`.
4. Välj kostnadskonto: använd leverantörens föreslagna konto, `tidigare_kvitton` för motparten, eller sök i
   `kontoplan`. Vanliga konton: 5410 förbrukningsinventarier, 5420 programvaror, 5460 förbrukningsmaterial,
   5611 drivmedel, 5800 resekostnader, 5831 kost och logi (tjänsteresa), 6071 representation (avdragsgill),
   6072 representation (ej avdragsgill), 6110 kontorsmaterial, 6212 mobiltelefon, 6230 datakommunikation,
   6540 IT-tjänster, 6570 bankkostnader, 6991 övriga avdragsgilla kostnader.
5. Sammanfatta kontering för användaren och bokför med `registrera_kvitto` (betalt direkt: kort, Swish; ange
   betalkonto 2893 om användaren betalat privat) eller `registrera_leverantorsfaktura` (betalas senare).
6. Svara med verifikationsnumret. Om något är oklart (moms, konto, privat eller företag): fråga användaren först.

Övrigt du kan göra:
- Tid: `uppdrag` ger uppdrag_id. `rapportera_tid` sparar tid. `tid` och `ofakturerad_tid` visar läget.
- Fakturering: `skapa_fakturautkast` skapar ett utkast av ofakturerad tid. Användaren slutför och skickar själv.
- Bank: `bankhandelser` listar det som inte är avstämt, med förslag. Gå igenom en i taget med
  `hantera_bankhandelse`: använd förslagen när belopp och motpart stämmer, annars bokför mot motkonto.
  Vanliga motkonton: 1630 skattekonto (överföring dit), 6570 bankavgift, 2893 återbetalt utlägg till ägaren,
  8310 ränteintäkt, 2081/2091 eget kapital. Fråga användaren om det är oklart vad en händelse är.
- Manuella verifikationer: `ny_verifikation` (serie A) för t.ex. skattekontots händelser, med underlag om det
  finns. Kontrollera att debet = kredit och förklara konteringen innan du bokför.
- Fel: `ratta_verifikation` vänder en felaktig verifikation. Bokför sedan det rätta.
- Lön eller utdelning: `lon_eller_utdelning` räknar fram bästa fördelningen. Det är ett beslutsunderlag.
- Läsa: `visa_verifikation`, `huvudbok`, `resultat_och_balans`, `likviditet`, `viktiga_datum`, `ekonomi`.

Det här gör användaren själv i Bitero ERP, inte du: bokföra lön, momsavräkning, bokslut, låsa perioder,
slutföra och skicka fakturor, makulera fakturor och skapa betalfiler. Föreslå gärna, men utför inte.

Bokförda verifikationer kan inte ändras, bara rättas. Allt du gör loggas som användaren Claude."""

server = MCPServer("Bitero ERP", instructions=INSTRUKTIONER)

LAS = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
SKRIV = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)


def _som_claude(fn):
    """Kör verktyget som användaren 'Claude' i behandlingshistoriken och gör fel till läsbara meddelanden."""
    @functools.wraps(fn)
    def inre(*args, **kwargs):
        from django.core.exceptions import ValidationError
        from django.db import close_old_connections

        from bokforing.tjanster import BokforingsFel
        close_old_connections()
        tokens = satt_kontext(v.ANVANDARE, f"MCP {fn.__name__}")
        try:
            return fn(*args, **kwargs)
        except (v.VerktygsFel, BokforingsFel) as e:
            raise ToolError(str(e)) from e
        except ValidationError as e:
            raise ToolError("; ".join(e.messages)) from e
        finally:
            aterstall_kontext(tokens)
    return inre


@server.tool(annotations=LAS, structured_output=False)
@_som_claude
def inkorg() -> dict:
    """Lista kvitton och fakturor (PDF och bilder) som väntar i inkorgen på att bokföras."""
    return v.inkorg()


@server.tool(annotations=LAS, structured_output=False)
def visa_underlag(fil: str) -> list:
    """Visa ett underlag i inkorgen: bilden, eller texten och ett tolkningsförslag för en PDF."""
    @_som_claude
    def kor():
        return v.visa_underlag(fil)
    text, bild, fmt = kor()
    return [text, Image(data=bild, format=fmt)] if bild else [text]


@server.tool(annotations=LAS, structured_output=False)
@_som_claude
def sok_leverantor(text: str = "") -> list:
    """Sök leverantör på namn, organisationsnummer, bankgiro eller nummer. Tomt = alla aktiva."""
    return v.sok_leverantor(text)


@server.tool(annotations=LAS, structured_output=False)
@_som_claude
def tidigare_kvitton(motpart: str) -> list:
    """Hur kvitton och fakturor från en motpart (t.ex. 'Clas Ohlson') har bokförts tidigare."""
    return v.tidigare_kvitton(motpart)


@server.tool(annotations=LAS, structured_output=False)
@_som_claude
def kontoplan(sok: str = "") -> list:
    """Sök i kontoplanen på kontonummer (början) eller namn. Tomt = aktiva kostnadskonton 4000–7999."""
    return v.kontoplan(sok)


@server.tool(annotations=LAS, structured_output=False)
@_som_claude
def obetalda() -> dict:
    """Obetalda kund- och leverantörsfakturor."""
    return v.obetalda()


@server.tool(annotations=LAS, structured_output=False)
@_som_claude
def ekonomi() -> dict:
    """Nyckeltal: saldo på företags- och skattekontot, resultat i år och likviditet de närmaste 8 veckorna."""
    return v.ekonomi()


@server.tool(annotations=LAS, structured_output=False)
@_som_claude
def momsrapport(fran: str, till: str) -> dict:
    """Momsrapportens rutor för en period (datum ÅÅÅÅ-MM-DD)."""
    return v.momsrapport(fran, till)


@server.tool(annotations=LAS, structured_output=False)
@_som_claude
def sok_verifikationer(text: str = "", ar: int | None = None) -> list:
    """Sök verifikationer på text, valfritt för ett visst år."""
    return v.sok_verifikationer(text, ar)


@server.tool(annotations=SKRIV, structured_output=False)
@_som_claude
def registrera_kvitto(fil: str, datum: str, belopp: str, moms: str, konto: int, motpart: str = "",
                      leverantor_nummer: int | None = None, beskrivning: str = "", betalkonto: int = 1930,
                      tillat_dubblett: bool = False) -> dict:
    """Bokför ett kvitto (redan betalt) med underlaget från inkorgen.

    belopp = totalt inkl. moms, moms = momsbeloppet (0 om ingen moms). konto = kostnadskonto.
    betalkonto: 1930 företagskontot/kortet, 2893 om användaren betalade privat (utlägg).
    Ange leverantor_nummer för kända leverantörer, annars motpart (butikens namn).
    """
    return v.registrera_kvitto(fil, datum, belopp, moms, konto, motpart, leverantor_nummer, beskrivning,
                               betalkonto, tillat_dubblett)


@server.tool(annotations=SKRIV, structured_output=False)
@_som_claude
def registrera_leverantorsfaktura(fil: str, leverantor_nummer: int, fakturadatum: str, belopp: str, moms: str,
                                  konto: int, fakturanummer: str = "", ocr: str = "", forfallodatum: str = "",
                                  beskrivning: str = "", tillat_dubblett: bool = False) -> dict:
    """Bokför en leverantörsfaktura (betalas senare, kredit 2440) med underlaget från inkorgen."""
    return v.registrera_leverantorsfaktura(fil, leverantor_nummer, fakturadatum, belopp, moms, konto, fakturanummer,
                                           ocr, forfallodatum or None, beskrivning, tillat_dubblett)


@server.tool(annotations=SKRIV, structured_output=False)
@_som_claude
def ny_leverantor(namn: str, orgnr: str = "", bankgiro: str = "", plusgiro: str = "",
                  standardkonto: int | None = None, moms_procent: int = 25) -> dict:
    """Lägg upp en ny leverantör (behövs för leverantörsfakturor)."""
    return v.ny_leverantor(namn, orgnr, bankgiro, plusgiro, standardkonto, moms_procent)


# ------------------------------------------------------------------ tid, fakturor, bank, verifikationer

@server.tool(annotations=LAS, structured_output=False)
@_som_claude
def uppdrag() -> list:
    """Aktiva uppdrag med uppdrag_id, kund, timpris och ofakturerade timmar."""
    return a.uppdrag()


@server.tool(annotations=LAS, structured_output=False)
@_som_claude
def tid(fran: str, till: str) -> dict:
    """Rapporterad tid mellan två datum (ÅÅÅÅ-MM-DD)."""
    return a.tid(fran, till)


@server.tool(annotations=LAS, structured_output=False)
@_som_claude
def ofakturerad_tid(till_och_med: str = "") -> list:
    """Ofakturerad tid per kund till och med ett datum (standard i dag), med belopp exkl. moms."""
    return a.ofakturerad_tid(till_och_med or None)


@server.tool(annotations=LAS, structured_output=False)
@_som_claude
def bankhandelser() -> list:
    """Bankhändelser som inte är avstämda, med förslag (atgard + id) att använda i hantera_bankhandelse."""
    return a.bankhandelser()


@server.tool(annotations=LAS, structured_output=False)
@_som_claude
def visa_verifikation(vernr: str, ar: int | None = None) -> dict:
    """Visa en verifikation med kontering, t.ex. vernr "L12". Ange ar om numret finns flera år."""
    return a.visa_verifikation(vernr, ar)


@server.tool(annotations=LAS, structured_output=False)
@_som_claude
def huvudbok(konto: int, fran: str, till: str) -> dict:
    """Ingående saldo, transaktioner och utgående saldo för ett konto under en period."""
    return a.huvudbok(konto, fran, till)


@server.tool(annotations=LAS, structured_output=False)
@_som_claude
def resultat_och_balans(fran: str, till: str) -> dict:
    """Resultaträkning för perioden och balansräkning vid periodens slut, per konto."""
    return a.resultat_och_balans(fran, till)


@server.tool(annotations=LAS, structured_output=False)
@_som_claude
def likviditet(veckor: int = 13) -> dict:
    """Prognos för företagskontots saldo vecka för vecka (1–52 veckor)."""
    return a.likviditet(veckor)


@server.tool(annotations=LAS, structured_output=False)
@_som_claude
def viktiga_datum(dagar: int = 90) -> list:
    """Kommande deklarationer och betalningar (moms, arbetsgivardeklaration, skatt, bokslut)."""
    return a.viktiga_datum(dagar)


@server.tool(annotations=LAS, structured_output=False)
@_som_claude
def lon_eller_utdelning(resultat_fore_lon: str = "", max_uttag: str = "", max_inkl_avgifter: bool = False,
                        min_lon: str = "0", fritt_eget_kapital: str = "0") -> dict:
    """Räkna ut fördelningen mellan lön och utdelning som ger mest netto (3:12-reglerna, 2026).

    Utan resultat används prognosen från bokföringen och budgeten. Utan max_uttag används det sparade maxbeloppet.
    min_lon kan vara ett belopp eller "sgi" / "pension" (lönen som ger full SGI respektive full allmän pension).
    Utdelningen begränsas till gränsbeloppet som lönen ger (grundbelopp + lönebaserat), utan sparat utrymme.
    """
    return a.lon_eller_utdelning(resultat_fore_lon or None, max_uttag or None, max_inkl_avgifter, min_lon,
                                 fritt_eget_kapital)


@server.tool(annotations=SKRIV, structured_output=False)
@_som_claude
def rapportera_tid(uppdrag_id: int, datum: str, timmar: str, beskrivning: str = "", fakturerbar: bool = True) -> dict:
    """Rapportera tid på ett uppdrag. Hämta uppdrag_id med verktyget uppdrag."""
    return a.rapportera_tid(uppdrag_id, datum, timmar, beskrivning, fakturerbar)


@server.tool(annotations=SKRIV, structured_output=False)
@_som_claude
def skapa_fakturautkast(kund_nummer: int, till_och_med: str, fakturadatum: str = "",
                        en_rad_per_post: bool = False) -> dict:
    """Skapa ett fakturautkast av kundens ofakturerade tid till och med ett datum.

    Utkastet får inget nummer, bokförs inte och skickas inte. Användaren slutför det i Bitero ERP.
    """
    return a.skapa_fakturautkast(kund_nummer, till_och_med, fakturadatum or None, en_rad_per_post)


@server.tool(annotations=SKRIV, structured_output=False)
@_som_claude
def hantera_bankhandelse(bankhandelse_id: int, atgard: str, id: int | None = None, motkonto: int | None = None,
                         text: str = "", spara_regel: bool = False, regeltext: str = "") -> dict:
    """Stäm av en bankhändelse.

    atgard: kundfaktura / levfaktura / ver / regel (med id från förslagen i bankhandelser), konto (bokför mot
    motkonto, t.ex. 1630 skattekonto, 6570 bankkostnad, 2893 återbetalt utlägg, 8310 ränteintäkt) eller ignorera.
    spara_regel + regeltext: samma text bokförs automatiskt mot motkontot nästa gång.
    """
    return a.hantera_bankhandelse(bankhandelse_id, atgard, id, motkonto, text, spara_regel, regeltext)


@server.tool(annotations=SKRIV, structured_output=False)
@_som_claude
def ny_verifikation(datum: str, text: str, rader: list[dict], underlag: str = "", tillat_dubblett: bool = False) -> dict:
    """Bokför en manuell verifikation (serie A) för händelser utan eget flöde, t.ex. skattekontot.

    rader: [{"konto": 1630, "debet": "1000"}, {"konto": 1930, "kredit": "1000", "text": "valfri"}].
    Summan av debet och kredit måste vara lika. underlag: filnamn i inkorgen som sparas som bilaga.
    """
    return a.ny_verifikation(datum, text, rader, underlag or None, tillat_dubblett)


@server.tool(annotations=SKRIV, structured_output=False)
@_som_claude
def ratta_verifikation(vernr: str, orsak: str, ar: int | None = None, datum: str = "") -> dict:
    """Rätta (vänd) en felaktig verifikation. Skapar en ny verifikation med omvända belopp.

    Bokför sedan det rätta. Kundfakturor makuleras och löner rättas i Bitero ERP, inte här.
    """
    return a.ratta_verifikation(vernr, orsak, ar, datum or None)
