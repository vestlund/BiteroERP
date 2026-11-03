"""Fler verktyg för Claude: tid, fakturautkast, bankhändelser, verifikationer, rättelser och rapporter.

Samma principer som i verktyg.py: affärslogiken ligger i apparnas tjänstefunktioner (samma som webbsidorna
använder), skrivande anrop godkänns av användaren i Claude-appen och loggas som användaren "Claude".
Lön, momsavräkning, bokslut, låsning av perioder, makulering och betalfiler görs bara i programmet.
"""

import datetime as dt
from decimal import Decimal

from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from bokforing import avstamning
from bokforing import tjanster as bok
from bokforing.models import Bankpost, Bilaga, Transaktion, Verifikation
from register.models import Foretag, Konto, Kund
from verksamhet import tjanster as verksamhet
from verksamhet.models import Tidpost, Uppdrag

from .verktyg import ANVANDARE, WEBB, VerktygsFel, _belopp, _datum, _fil, _flytta_till_bokforda, _konto, _sha


def _s(v):
    return str(v) if v is not None else None


def _idag():
    return timezone.localdate()


def _kontering(ver):
    return [f"{t.konto_id} {t.konto.namn}: {'debet' if t.belopp > 0 else 'kredit'} {abs(t.belopp)}"
            + (f" ({t.text})" if t.text and t.text != ver.text else "")
            for t in ver.transaktioner.select_related("konto")]


def _ver_svar(ver, **extra):
    return {"verifikation": ver.vernr, "datum": str(ver.datum), "text": ver.text, "kontering": _kontering(ver),
            "lank": f"{WEBB}/bokforing/verifikationer/{ver.pk}/", **extra}


def _hitta_ver(vernr, ar=None):
    vernr = str(vernr or "").strip().upper()
    serie, nummer = vernr[:1], vernr[1:]
    if not serie.isalpha() or not nummer.isdigit():
        raise VerktygsFel(f"Ogiltigt verifikationsnummer: {vernr} (t.ex. A12 eller L5).")
    qs = Verifikation.objects.filter(serie=serie, nummer=int(nummer))
    if ar:
        qs = qs.filter(ar=int(ar))
    ver = qs.order_by("-ar").first()
    if not ver:
        raise VerktygsFel(f"Verifikation {vernr}{f' ({ar})' if ar else ''} finns inte.")
    return ver


def _kund(nummer):
    try:
        return Kund.objects.get(nummer=int(nummer))
    except (Kund.DoesNotExist, ValueError, TypeError):
        raise VerktygsFel(f"Kund nummer {nummer} finns inte. Använd verktyget uppdrag för att se kunderna.")


# ------------------------------------------------------------------ läsverktyg

def uppdrag():
    """Aktiva uppdrag med kund, timpris och ofakturerad tid."""
    res = []
    for u in Uppdrag.objects.filter(aktiv=True).select_related("kund", "artikel").order_by("kund__namn", "namn"):
        ofakt = Tidpost.objects.filter(uppdrag=u, fakturerbar=True, fakturarad__isnull=True).aggregate(
            t=Sum("timmar"))["t"] or Decimal(0)
        res.append({"uppdrag_id": u.pk, "uppdrag": u.namn, "kund_nummer": u.kund.nummer, "kund": u.kund.namn,
                    "timpris": str(u.pris), "ofakturerade_timmar": str(Decimal(ofakt).quantize(Decimal("0.01")))})
    return res


def tid(fran, till):
    """Rapporterad tid mellan två datum."""
    fran, till = _datum(fran, "fran"), _datum(till, "till")
    qs = Tidpost.objects.filter(datum__range=(fran, till)).select_related("uppdrag__kund").order_by("datum")
    poster = [{"datum": str(p.datum), "uppdrag_id": p.uppdrag_id, "uppdrag": p.uppdrag.namn,
               "kund": p.uppdrag.kund.namn, "timmar": str(p.timmar), "beskrivning": p.beskrivning,
               "fakturerad": p.fakturarad_id is not None} for p in qs[:300]]
    return {"poster": poster, "summa_timmar": str(sum((p.timmar for p in qs), Decimal(0)))}


def ofakturerad_tid(till_och_med=None):
    """Ofakturerad tid per kund till och med ett datum (standard: i dag)."""
    till = _datum(till_och_med, "till_och_med") if till_och_med else _idag()
    res = []
    for kund in Kund.objects.filter(uppdrag__tidposter__fakturarad__isnull=True).distinct():
        poster = verksamhet.ofakturerad_tid(kund, till)
        if not poster:
            continue
        per_uppdrag, netto = verksamhet.sammanstall(poster)
        res.append({"kund_nummer": kund.nummer, "kund": kund.namn, "netto_exkl_moms": str(netto),
                    "per_uppdrag": [{"uppdrag": u.namn, "timmar": str(s["timmar"]), "belopp": str(s["belopp"])}
                                    for u, s in per_uppdrag.items()],
                    "fran": str(poster[0].datum), "till": str(poster[-1].datum)})
    return res


def bankhandelser():
    """Bankhändelser som inte är avstämda, med förslag på vad de hör ihop med."""
    res = []
    for p in bok.ofakturerade_bankposter().select_related("konto").order_by("datum")[:50]:
        forslag = [{"atgard": f["typ"], "id": f["obj"].pk, "beskrivning": f["text"]} for f in bok.bankpost_forslag(p)]
        forslag += [{"atgard": "regel", "id": r.pk, "beskrivning": f"Regel {r.namn} → {r.motkonto}"}
                    for r in avstamning.regler_for(p, bara_auto=False)]
        res.append({"bankhandelse_id": p.pk, "datum": str(p.datum), "text": p.text, "belopp": str(p.belopp),
                    "bankkonto": p.konto_id, "motpart": p.motpart, "referens": p.referens, "forslag": forslag})
    return res


def visa_verifikation(vernr, ar=None):
    ver = _hitta_ver(vernr, ar)
    return _ver_svar(ver, rattad=ver.ar_rattad, rattar=ver.rattar.vernr if ver.rattar_id else None,
                     bilagor=[b.namn for b in Bilaga.objects.filter(verifikation=ver)])


def huvudbok(konto, fran, till):
    """Ingående saldo, alla transaktioner och utgående saldo för ett konto."""
    k = _konto(konto, (1000, 8999))
    fran, till = _datum(fran, "fran"), _datum(till, "till")
    s = bok.saldon(fran, till, [k.nummer]).get(k.nummer, {"ib": Decimal(0), "ub": Decimal(0)})
    tr = Transaktion.objects.filter(konto=k, verifikation__datum__range=(fran, till)).select_related(
        "verifikation").order_by("verifikation__datum", "verifikation__serie", "verifikation__nummer")
    rader = [{"datum": str(t.verifikation.datum), "ver": t.verifikation.vernr, "text": t.text or t.verifikation.text,
              "belopp": str(t.belopp)} for t in tr[:300]]
    return {"konto": str(k), "ingaende_saldo": str(s["ib"]), "transaktioner": rader, "utgaende_saldo": str(s["ub"]),
            "obs": "Positivt = debet, negativt = kredit." + (" Visar de första 300." if tr.count() > 300 else "")}


def resultat_och_balans(fran, till):
    """Resultaträkning för perioden och balansräkning vid periodens slut, per konto."""
    fran, till = _datum(fran, "fran"), _datum(till, "till")
    s = bok.saldon(fran, till)
    namn = dict(Konto.objects.values_list("nummer", "namn"))
    resultat = [{"konto": k, "namn": namn.get(k, ""), "belopp": str(-v["period"])}
                for k, v in s.items() if k >= 3000 and v["period"]]
    balans = [{"konto": k, "namn": namn.get(k, ""), "saldo": str(v["ub"])} for k, v in s.items() if k < 3000 and v["ub"]]
    intakter = -sum((v["period"] for k, v in s.items() if 3000 <= k < 4000), Decimal(0))
    return {"period": f"{fran} – {till}", "intakter": str(intakter),
            "resultat_fore_skatt": str(-sum((v["period"] for k, v in s.items() if 3000 <= k < 8900), Decimal(0))),
            "resultatrakning": resultat, "balansrakning": balans,
            "obs": "Resultaträkning: positivt = intäkt/vinst. Balansräkning: positivt = tillgång, negativt = skuld/eget kapital."}


def likviditet(veckor=13):
    from planering import likviditet as lik
    lp = lik.prognos(_idag(), veckor=max(1, min(int(veckor), 52)))
    return {"startsaldo": str(lp["start"]),
            "veckor": [{"vecka": v["vecka"], "saldo": str(v["saldo"])} for v in lp["veckor"]],
            "lagsta": {"vecka": lp["lagsta"]["vecka"], "saldo": str(lp["lagsta"]["saldo"])} if lp["lagsta"] else None}


def viktiga_datum(dagar=90):
    from lon.models import Anstalld
    from planering.frister import frister
    idag = _idag()
    lista = frister(idag, idag + dt.timedelta(days=int(dagar)), Foretag.hamta(),
                    med_lon=Anstalld.objects.filter(aktiv=True).exists())
    return [{"datum": str(f["datum"]), "rubrik": f["rubrik"], "vad": f["text"]} for f in lista]


# ------------------------------------------------------------------ skrivverktyg

def rapportera_tid(uppdrag_id, datum, timmar, beskrivning="", fakturerbar=True):
    from verksamhet.forms import TidpostForm

    form = TidpostForm(data={"uppdrag": uppdrag_id, "datum": str(_datum(datum, "datum")), "timmar": str(_belopp(timmar, "timmar")),
                             "beskrivning": beskrivning or "", "fakturerbar": "on" if fakturerbar else ""})
    if not form.is_valid():
        fel = "; ".join(f"{f}: {' '.join(e)}" for f, e in form.errors.items())
        raise VerktygsFel(f"Tiden kunde inte sparas: {fel}. Använd verktyget uppdrag för att se aktiva uppdrag.")
    d = form.cleaned_data
    if d["timmar"] <= 0 or d["timmar"] > 24:
        raise VerktygsFel("Timmar ska vara mer än 0 och högst 24.")
    dag_innan = Tidpost.objects.filter(datum=d["datum"]).aggregate(t=Sum("timmar"))["t"] or Decimal(0)
    if dag_innan + d["timmar"] > 24:
        raise VerktygsFel(f"Det finns redan {dag_innan} timmar rapporterade {d['datum']}; totalt blir det över 24.")
    tp = form.save()
    samma = Tidpost.objects.filter(datum=tp.datum, uppdrag=tp.uppdrag).exclude(pk=tp.pk)
    return {"sparad": f"{tp.timmar} h {tp.datum} på {tp.uppdrag.namn} ({tp.uppdrag.kund.namn})",
            "totalt_samma_dag": str(dag_innan + tp.timmar),
            "obs": f"Det fanns redan {sum(p.timmar for p in samma)} h på samma uppdrag och dag." if samma else None,
            "lank": f"{WEBB}/tid/?manad={tp.datum:%Y-%m}"}


def skapa_fakturautkast(kund_nummer, till_och_med, fakturadatum=None, en_rad_per_post=False):
    kund = _kund(kund_nummer)
    till = _datum(till_och_med, "till_och_med")
    fdatum = _datum(fakturadatum, "fakturadatum") if fakturadatum else _idag()
    f = verksamhet.skapa_fakturautkast(kund, till, fdatum, bool(en_rad_per_post))
    if not f:
        raise VerktygsFel(f"{kund.namn} har ingen ofakturerad tid till och med {till}.")
    s = f.summering()
    return {"utkast": f"Fakturautkast till {kund.namn}", "fakturadatum": str(f.fakturadatum), "period": f.period,
            "rader": [{"text": r.beskrivning, "antal": str(r.antal), "pris": str(r.a_pris)} for r in f.rader.all()],
            "netto": str(s["netto"]), "att_betala": str(s["att_betala"]), "lank": f"{WEBB}{f.get_absolute_url()}",
            "nasta_steg": "Utkastet är inte numrerat, bokfört eller skickat. Användaren kontrollerar det och klickar "
                          "Slutför och ge nummer i Bitero ERP."}


def hantera_bankhandelse(bankhandelse_id, atgard, id=None, motkonto=None, text="", spara_regel=False, regeltext=""):
    try:
        post = Bankpost.objects.select_related("konto").get(pk=int(bankhandelse_id))
    except (Bankpost.DoesNotExist, ValueError, TypeError):
        raise VerktygsFel(f"Bankhändelse {bankhandelse_id} finns inte. Använd verktyget bankhandelser.")
    if atgard not in ("kundfaktura", "levfaktura", "ver", "konto", "regel", "ignorera"):
        raise VerktygsFel("atgard ska vara kundfaktura, levfaktura, ver, konto, regel eller ignorera.")
    mk = None
    if atgard == "konto":
        if motkonto is None:
            raise VerktygsFel("Ange motkonto, t.ex. 1630 skattekonto eller 6570 bankkostnader.")
        mk = _konto(motkonto)
        if mk.nummer == post.konto_id:
            raise VerktygsFel("Motkontot kan inte vara samma som bankkontot.")
    post = avstamning.hantera(post, atgard, id, mk, text or "", bool(spara_regel), regeltext or "", ANVANDARE)
    if atgard == "ignorera":
        return {"bankhandelse": f"{post.datum} {post.text} {post.belopp}", "status": "ignorerad"}
    return _ver_svar(post.verifikation, bankhandelse=f"{post.datum} {post.text} {post.belopp}",
                     regel_sparad=bool(spara_regel and atgard == "konto"))


def _rader(rader):
    if not isinstance(rader, list) or len(rader) < 2:
        raise VerktygsFel("Ange minst två rader: [{konto, debet} , {konto, kredit}].")
    ut = []
    for i, r in enumerate(rader, 1):
        if not isinstance(r, dict):
            raise VerktygsFel(f"Rad {i} ska vara ett objekt med konto och debet eller kredit.")
        debet = _belopp(r.get("debet") or 0, f"debet rad {i}")
        kredit = _belopp(r.get("kredit") or 0, f"kredit rad {i}")
        if debet < 0 or kredit < 0 or (debet and kredit) or not (debet or kredit):
            raise VerktygsFel(f"Rad {i}: ange antingen debet eller kredit, större än noll.")
        k = _konto(r.get("konto"))
        ut.append({"konto": k.nummer, "belopp": debet - kredit, "text": str(r.get("text") or "")[:300]})
    summa = sum(r["belopp"] for r in ut)
    if summa:
        raise VerktygsFel(f"Verifikationen balanserar inte: debet minus kredit är {summa}.")
    return ut


def ny_verifikation(datum, text, rader, underlag=None, tillat_dubblett=False):
    datum, text = _datum(datum, "datum"), (text or "").strip()
    if not text:
        raise VerktygsFel("Verifikationen behöver en text som beskriver händelsen.")
    r = _rader(rader)
    oms = sum(x["belopp"] for x in r if x["belopp"] > 0)
    for v in Verifikation.objects.filter(datum=datum, serie="A").prefetch_related("transaktioner"):
        if v.omsattning == oms and not tillat_dubblett:
            raise VerktygsFel(f"Det finns redan {v.vernr} samma dag med samma belopp ({v.text}). "
                              "Ange tillat_dubblett=true om det är en annan händelse.")
    p = _fil(underlag) if underlag else None
    data = p.read_bytes() if p else None
    if data and Bilaga.objects.filter(sha256=_sha(data)).exists() and not tillat_dubblett:
        raise VerktygsFel("Underlaget är redan bifogat en annan verifikation.")
    with transaction.atomic():
        ver = bok.bokfor(datum, text, r, serie="A", anvandare=ANVANDARE)
        if p:
            b = Bilaga(verifikation=ver, namn=p.name)
            b.fil.save(p.name, ContentFile(data), save=True)
    flyttad = _flytta_till_bokforda(p, datum) if p else None
    return _ver_svar(ver, underlag_flyttat_till=_s(flyttad))


def ratta_verifikation(vernr, orsak, ar=None, datum=None):
    ver = _hitta_ver(vernr, ar)
    if ver.rattar_id:
        raise VerktygsFel(f"{ver.vernr} är själv en rättelse och kan inte rättas igen.")
    if not (orsak or "").strip():
        raise VerktygsFel("Ange orsaken till rättelsen. Den sparas i verifikationstexten.")
    if ver.kundfaktura_id and ver.serie == "F":
        raise VerktygsFel("En kundfaktura rättas genom att makulera fakturan i Bitero ERP.")
    if ver.lonekorning_id:
        raise VerktygsFel("En lönekörning rättas i Bitero ERP under Lön.")
    datum = _datum(datum, "datum") if datum else _idag()
    ratt = bok.rattelse(ver, datum, ANVANDARE, text=f"Rättelse av {ver.vernr}: {orsak.strip()}"[:300])
    return _ver_svar(ratt, rattar=ver.vernr,
                     nasta_steg="Bokför nu det som är rätt, t.ex. med registrera_kvitto eller ny_verifikation.")


def lon_eller_utdelning(resultat_fore_lon=None, max_uttag=None, max_inkl_avgifter=False, min_lon=0,
                        fritt_eget_kapital=0, bara_inom_gransbelopp=False):
    """Bästa fördelning mellan lön och utdelning enligt 3:12-reglerna, med företagets sparade skatteuppgifter."""
    from planering import lon_utdelning as lu
    f = Foretag.hamta()
    idag = _idag()
    res = _belopp(resultat_fore_lon, "resultat_fore_lon") if resultat_fore_lon not in (None, "") \
        else lu.resultat_fore_lon(idag)["resultat"]
    tak = max_uttag if max_uttag not in (None, "") else f.max_uttag
    forut = lu.Forutsattningar(
        ar=idag.year, resultat_fore_agarlon=res, kommunalskatt=f.kommunalskatt, avgift=f.begravningsavgift,
        arbetsgivaravgift=lu.agarens_arbetsgivaravgift(idag.year), agarandel=f.agarandel,
        sparat=f.sparat_utdelningsutrymme, fritt_eget_kapital=_belopp(fritt_eget_kapital or 0, "fritt_eget_kapital"),
        max_uttag=_belopp(tak, "max_uttag") if tak is not None else None,
        max_inklusive_avgifter=bool(max_inkl_avgifter or (max_uttag in (None, "") and f.max_uttag_inkl_avgifter)),
        min_lon=_belopp(min_lon or 0, "min_lon"), bara_inom_gransbelopp=bool(bara_inom_gransbelopp))
    bast, alla = lu.optimera(forut)
    if not bast:
        raise VerktygsFel("Ingen möjlig fördelning med de villkoren. Kontrollera resultat, maxbelopp och lägsta lön.")

    def kort(a):
        return {"lon": str(a["lon"]), "utdelning": str(a["utdelning"]), "inom_gransbelopp": str(a["utdelning_inom"]),
                "netto_till_agaren": str(a["netto"]), "kvar_i_bolaget": str(a["kvar_i_bolaget"]),
                "skatter_och_avgifter_procent": str(a["skattekvot"])}
    rek = lu.rekommenderad_lon(idag.year, forut.arbetsgivaravgift, forut.kommunalskatt, forut.avgift)
    rekommenderad = {n["namn"]: {"lon_per_ar": str(n["lon"]), "lon_per_manad": str(n["lon_manad"]),
                                 "kostnad_for_bolaget_per_ar": str(n["kostnad"])} for n in rek["nivaer"]}
    return {"rekommenderad_lagsta_lon": {"rekommenderad": rek["rekommenderad"]["namn"], **rekommenderad},
            "forutsattningar": {"resultat_fore_lon": str(res), "max_uttag": _s(forut.max_uttag),
                                "kommunalskatt": str(forut.kommunalskatt), "gransbelopp": str(bast["gransbelopp"]["summa"])},
            "bast": kort(bast), "jamforelse": [kort(a) for a in lu.jamforelse(forut, bast, alla)],
            "lank": f"{WEBB}/lon/lon-och-utdelning/",
            "obs": "Beslutsunderlag enligt 2026 års regler, samma lön varje år, under 66 år. Inte skatterådgivning."}
