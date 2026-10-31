"""MCP-server som låter Claude läsa och bokföra i Bitero ERP. Startas av Claude-appen med `manage.py mcp`."""

import functools

from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations

from logg.sparning import aterstall_kontext, satt_kontext

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

Bokförda verifikationer kan inte ändras; fel rättas av användaren i Bitero ERP. Allt du gör loggas."""

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
