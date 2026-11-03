# Systemdokumentation – Bitero ERP

Dokumentationen följer kraven på systemdokumentation i 5 kap. 11 § bokföringslagen och BFNAR 2013:2 kapitel 9.
Den beskriver hur bokföringssystemet är uppbyggt, vilka regler som styr de automatiska konteringarna och hur
verifieringskedjorna hänger ihop. Den är räkenskapsinformation: skriv ut den (eller spara som PDF) och arkivera
den med bokslutet varje år.

| | |
|---|---|
| Företag | Bitero AB, org.nr 556974-4302 |
| Räkenskapsår | Kalenderår |
| Ansvarig för systemet och bokföringen | Företagets VD |
| Huvudbok | Visma eEkonomi/Spiris (Bitero ERP bokför parallellt och skickar verifikationer dit) |

## 1. Översikt

Bitero ERP är ett lokalt affärssystem som körs på företagets bärbara dator. Det används i webbläsaren
(http://127.0.0.1:8000) och lyssnar bara på den egna datorn. Inloggning krävs för alla sidor.

| Del | Innehåll |
|---|---|
| Program | Python och Django, med webbservern waitress. Fel loggas i `bitero-erp-data\fel.log`. Källkoden ligger i `C:\kod\bitero-erp` och versionshanteras med git i ett privat GitHub-repo. |
| Databas | SQLite-fil `konsultsystem.sqlite3` i `%USERPROFILE%\bitero-erp-data`, utanför OneDrive. |
| Bilagor | Underlag (PDF, bilder) i `bitero-erp-data\bilagor\ÅÅÅÅ\MM\`. |
| Säkerhetskopior | Krypterade, tas automatiskt vid varje start till `OneDrive\bitero-erp-backups\`. Månadsvis kopia till extern disk. |

### Delsystem

- **Register:** kontoplan, kunder, leverantörer, artiklar, kostnadsställen, betalningsvillkor, företagsuppgifter,
  matchningsregler för bank.
- **Försäljning:** uppdrag, tidrapportering, kundfakturor.
- **Inköp:** leverantörsfakturor och kvitton med bilagor, betalfil till banken.
- **Bank:** import av kontoutdrag (CSV, camt.053/054) och avstämning.
- **Bokföring:** verifikationer, rättelser, huvudbok, resultat- och balansrapport, momsrapport, SIE.
- **Lön:** anställda, lönekörningar, lönespecifikationer, underlag till arbetsgivardeklaration.
- **Budget och prognos.**
- **Behandlingshistorik** och systemversioner (avsnitt 7).

### Samverkan med Visma

| Flöde | Format | Beskrivning |
|---|---|---|
| Bitero ERP → Visma | SIE 4I | Verifikationer som skapats här exporteras. Vismas eget verifikationsnummer sätts av Visma; vårt nummer står sist i texten, t.ex. `(F12)`. |
| Visma → Bitero ERP | SIE 4 | Verifikationer bokförda i Visma läses in som serie V. Verifikationer med vår markering hoppas över så att inget dubbelbokförs. Ingående balanser (#IB) läses också in. |
| Visma → Bitero ERP | SIE 4 | Kund- och leverantörsfakturor och deras betalningar läses ur verifikationstexterna. Sådana fakturor bokförs **inte** här, eftersom bokföringen redan finns i Visma. |
| Visma → Bitero ERP | PDF | Originalfakturor kopplas till importerade kundfakturor (fakturanummer i PDF:en). |
| Register | CSV/Excel | Import och export av kunder, leverantörer, artiklar, kontoplan och kostnadsställen. |

## 2. Kontoplan

Kontoplanen bygger på BAS och har importerats från Visma (fil `ChartAccounts_Export`). Momskoden på varje konto
(t.ex. `05-25%`, `10`, `48`) styr i vilken ruta i momsdeklarationen kontots belopp hamnar. Konton som används av
de automatiska konteringarna aktiveras automatiskt. Den aktuella kontoplanen visas under **Bokföring → Kontoplan**
och kan exporteras.

## 3. Verifikationer och nummerserier

En verifikation sparas med serie, löpnummer, bokföringsdatum, text, registreringstidpunkt, användare och minst
två konteringsrader som balanserar (debet = kredit). Numreringen är löpande utan luckor inom serie och
räkenskapsår.

| Serie | Används för |
|---|---|
| A | Manuella verifikationer |
| F | Kundfakturor |
| I | Inbetalningar av kundfakturor |
| L | Leverantörsfakturor och kvitton |
| U | Utbetalningar av leverantörsfakturor |
| B | Bankhändelser som bokförs från bankavstämningen |
| M | Momsredovisning (avräkning mot 2650) |
| S | Löner |
| V | Verifikationer importerade från Visma |

**Oföränderlighet.** En bokförd verifikation och dess rader kan inte ändras eller raderas. Fel rättas med en
rättelseverifikation som vänder samtliga rader och hänvisar till den rättade verifikationen.
Det enda som uppdateras i efterhand är tidpunkten för SIE-export och Visma-id:t vid import.
Skyddet gäller både enskilda poster och massoperationer: försök att ändra eller radera många verifikationer
eller transaktioner på en gång stoppas också.

**Periodlåsning.** Under Företagsuppgifter anges "bokföring låst t.o.m.". Inga verifikationer kan skapas med
datum till och med det datumet. Lås perioden när momsdeklarationen är inlämnad.

## 4. Behandlingsregler (automatiska konteringar)

Positivt belopp = debet, negativt = kredit.

### Kundfaktura (serie F), när fakturan slutförs
| Konto | Belopp |
|---|---|
| 1510 Kundfordringar (eller kundfordringskonto i Företagsuppgifter) | Att betala inkl. moms |
| Fakturaradens intäktskonto (artikelns konto, normalt 3041) | − Nettobelopp per konto och kostnadsställe |
| 2611 / 2621 / 2631 Utgående moms 25 / 12 / 6 % | − Moms per sats |
| 3740 Öres- och kronutjämning | − Öresavrundning |

Moms räknas per momssats på summan av raderna och avrundas till hela ören. Att betala avrundas till hela kronor.
Fakturanummer tilldelas när fakturan slutförs, i obruten följd. Samtidigt sätts OCR-numret: fakturanumret plus
längdsiffra plus kontrollsiffra (Luhn), samma format som Visma. Makulering skapar en rättelse.

Fakturan skapas som PDF. "Skicka med e-post" ger ett e-postutkast (.eml) med PDF:en bifogad, som öppnas i
Outlook och skickas därifrån. För förfallna fakturor kan en påminnelse skapas. Den visar upplupen
dröjsmålsränta enligt räntelagen (referensräntan + 8 procentenheter per kalenderhalvår) och en valfri
påminnelseavgift. Påminnelseavgiften är högst 60 kr och får bara tas ut om den är avtalad i förväg.

### Inbetalning av kundfaktura (serie I)
Debet bankkonto (normalt 1930), kredit 1510, med fakturans belopp att betala.

### Leverantörsfaktura (serie L)
Debet kostnadskonto (nettobelopp), debet 2641 Ingående moms, kredit 2440 Leverantörsskulder.
**Kvitto:** samma, men kredit på betalkontot (1930 företagskonto eller 2893 eget utlägg). Kvittot markeras betalt.

**Avläsning:** när en PDF bifogas en ny leverantörsfaktura eller ett kvitto läses texten av. Leverantör,
datum, förfallodatum, fakturanummer, OCR, belopp och moms föreslås i formuläret och ska kontrolleras innan
du bokför. Leverantören känns igen på organisationsnummer, bankgiro/plusgiro eller namn. Inskannade kvitton
och foton saknar text och fylls i för hand.

### Utbetalning av leverantörsfaktura (serie U)
Debet 2440, kredit bankkontot.

### Momsredovisning (serie M)
Saldot för perioden på momskontona 26xx (utom 2650) nollställs mot 2650 Redovisningskonto för moms.
Momsrapporten summerar per ruta med hjälp av kontonas momskoder. Visma-verifikationer som redan för över moms
till 2650 räknas inte med. En period kan bara avräknas en gång.

### Lön (serie S)
| Konto | Innehåll |
|---|---|
| Lönekonto (7210 eller den anställdes konto) | Bruttolön |
| 7281, 7285 m.fl. enligt löneart | Sjuklön, semesterlön |
| 7385 / 7389 och 7399 | Förmåner (bokas mot motkonto 7399, påverkar bara skatt och avgifter) |
| 7321, 7331 | Skattefria traktamenten och bilersättning |
| 2710 Personalskatt | − Avdragen skatt |
| 7510 / 2730 | Arbetsgivaravgifter (kostnad / skuld) |
| 1930 | − Nettolön |

Arbetsgivaravgifter 2026: 31,42 %. 10,21 % för den som fyllt 67 vid årets ingång. 0 % för den som är född
1937 eller tidigare. 20,81 % på lön upp till 25 000 kr/mån för den som fyllt 18 men inte 23 vid årets ingång,
för utbetalningar 1 april 2026 – 30 september 2027. Skatteavdrag enligt Skatteverkets skattetabeller (importeras
per år) eller procentsats enligt beslut. Underlaget till arbetsgivardeklarationen (ruta 011, 012, 013, 001)
summeras per utbetalningsmånad.

### Deklarationer till Skatteverket
- **Moms:** filen följer Skatteverkets format eSKDUpload 6.0 och laddas upp i e-tjänsten Lämna momsdeklaration.
  Beloppen anges i hela kronor; ören stryks. Ruta 49 räknas på de avrundade beloppen.
- **Arbetsgivardeklaration:** XML-fil enligt Skatteverkets schema version 1.1, med en huvuduppgift och en
  individuppgift per anställd. Den innehåller ruta 011, 012, 013 och 001, och specifikationsnumret är den
  anställdas nummer. Beloppen anges i hela kronor.

### Bankavstämning (serie B och kopplingar)
En importerad bankpost matchas automatiskt i denna ordning. Första säkra träffen gäller.
1. Utbetalning med end-to-end-id `LF<id>` från betalfilen → leverantörsfakturan bokförs som betald (serie U).
2. Inbetalning med samma belopp och fakturanumret i texten eller referensen → kundfakturan bokförs som betald (serie I).
3. Utbetalning med samma belopp och leverantörens OCR eller fakturanummer → serie U.
4. Exakt en befintlig verifikation på bankkontot med samma belopp inom ±3 dagar → posten kopplas till den.
5. Matchningsregel markerad "bokför automatiskt" → debet/kredit regelns konto mot bankkontot (serie B), med
   moms enligt regelns satser.

Allt annat hanteras manuellt. Bankposter känns igen med en kontrollsumma, så samma fil kan inte läsas in två gånger.

### Betalfil
Betalfilen följer ISO 20022 pain.001.001.03: bankgiro via SESBA 9900/BGNR, plusgiro via SESBA 9960/BBAN.
OCR skickas som strukturerad referens när kontrollsiffran stämmer.

### Bokslut (Bokföring → Bokslut)
Bokslutsverifikationerna bokförs i serie A per den 31 december med texten "Bokslut <år>: …". Varje steg kan
bara bokföras en gång.
- **Periodiseringar:** upplupen/förutbetald intäkt eller kostnad mot 1790 eller 2990, med automatisk återföring
  den 1 januari. Upparbetad men ofakturerad tid föreslås som upplupen intäkt. Det gäller tid till och med 31/12
  som var ofakturerad eller fakturerades efter årsskiftet.
- **Avskrivningar:** inventarier i registret skrivs av linjärt per påbörjad månad från anskaffningen, under
  avskrivningstiden: debet 7832, kredit 1229 (konton per inventarie).
- **Skatt:** 20,6 % på resultat före skatt plus ej avdragsgilla kostnader (6072, 6342, 6982, 6992, 7622, 7623,
  7632, 8423). Underlaget avrundas nedåt till hela hundratal. Bokförs 8910/2512. Förenklad beräkning, utan
  periodiseringsfond och underskottsavdrag.
- **Årets resultat:** 8999 mot 2099.
- **Underlag till inkomstdeklaration 2:** räkenskapsschemat (INK2R), summerat per fält enligt BAS
  kopplingstabell, och de vanligaste skattemässiga justeringarna (INK2S). Underlaget används för avstämning.
  Någon SRU-fil skapas inte, eftersom Skatteverkets fältkoder för 2026 års blankett ännu inte är publicerade.

### Planering
- **Likviditetsprognos** (Planering → Likviditet): saldot på företagskontot vecka för vecka.
  - Startsaldot är bankens senaste saldo från importerade kontoutdrag, annars bokfört saldo på 1930.
  - In: kundfakturor på förfallodagen, ofakturerad tid som faktureras vid månadens slut och budgeterade intäkter.
  - Ut: leverantörsfakturor, nettolöner, skatter och avgifter samt F-skatt på skattedagarna, moms på momsdagen
    och budgeterade kostnader.
  - Löner tas från den senaste lönekörningen eller uppskattas från den senast bokförda lönen.
  - Momsen räknas på momsrapporten per period, inte på kontosaldon.
- **Viktiga datum** (Planering → Viktiga datum): deklarations- och betalningsdagar enligt Skatteverket för företag
  med omsättning upp till 40 miljoner kr (den 12:e, i januari och augusti den 17:e, flyttat till närmaste vardag),
  samt årsstämma, årsredovisning och inkomstdeklaration. Listan kan hämtas som kalenderfil (.ics).

- **Skatt och utdelning** (Planering → Skatt och utdelning):
  - Bolagsskatt 20,6 % på prognostiserat resultat (bokfört hittills plus huvudbudgeten för resten av året),
    jämfört med debiterad F-skatt. Vid kvarskatt över 30 000 kr visas en påminnelse om fyllnadsinbetalning
    senast 12 februari.
  - Gränsbeloppet för utdelning enligt 3:12-reglerna från 2026:
    - grundbelopp: 4 inkomstbasbelopp för året före, efter ägarandel,
    - lönebaserat utrymme: 50 % av (bolagets löner året före × ägarandel − 8 inkomstbasbelopp), med tak
      50 × egen lön,
    - sparat utdelningsutrymme, utan ränteuppräkning.
  - Fritt eget kapital beräknas från nästa års ingående balanser. Beräkningen är ett planeringsstöd och
    ersätter inte K10.

- **Lön eller utdelning** (Planering → Lön eller utdelning, `planering/lon_utdelning.py`): räknar fram den lön
  som ger störst nettobelopp till ägaren av lön plus utdelning, givet ett prognostiserat resultat före ägarens lön.
  - Modell: samma lön varje år. Lönen kostar lön × (1 + arbetsgivaravgift). Resten beskattas med 20,6 %
    bolagsskatt och kan delas ut året efter, tillsammans med eventuellt fritt eget kapital från tidigare år.
  - Utdelningen begränsas alltid till gränsbeloppet som lönen i kalkylen ger: grundbelopp (4 IBB × ägarandel)
    plus lönebaserat utrymme med lönen som löneunderlag. Sparat utrymme från tidigare år räknas inte med, så
    det är lönen som avgör utdelningsutrymmet. Utdelningen beskattas därmed med 20 %. (Funktionen kan fortfarande
    räkna på en angiven utdelning över gränsbeloppet: som tjänst upp till 90 IBB och därefter 30 %.)
  - Inkomstskatt enligt Skatteverkets tekniska beskrivning SKV 433 för 2026: grundavdrag, jobbskatteavdrag,
    kommunalskatt, statlig skatt 20 % över skiktgränsen 643 000 kr, skattereduktion för förvärvsinkomst,
    allmän pensionsavgift 7 % med motsvarande skattereduktion, samt begravnings- och kyrkoavgift.
  - Villkor: max uttag (lön + utdelning, valfritt inklusive arbetsgivaravgifter), och lägsta lön (eget belopp
    eller vald nivå: full SGI eller full allmän pension). Lönen provas i steg om 1 000 kr plus brytpunkter (statlig skatt,
    8 inkomstbasbelopp, pensionstak). Vid lika netto väljs lägst uttag.
  - Rekommenderad lägsta lön: den högre av full SGI (10 prisbasbelopp, Försäkringskassans tak) och full
    allmän pension (8,07 inkomstbasbelopp, som ger högsta pensionsgrundande inkomst 7,5 inkomstbasbelopp),
    med kostnad för bolaget inklusive arbetsgivaravgift.
  - Förenklingar: under 66 år, inga andra inkomster, inget räntebaserat utrymme, ingen tjänstepension.
    Kommunalskatt, avgift och maxbelopp sparas under Företagsuppgifter. Skatteparametrar finns per år i
    `PARAMETRAR`; saknas ett år används det senast kända och ska uppdateras när Skatteverket publicerar nya belopp.

- **Uppdragsuppföljning** (Försäljning → Uppdragsuppföljning):
  - Beläggning är fakturerbara timmar mot arbetsdagar × 8 timmar per månad.
  - Intäkt per kund är fakturerat exklusive moms.
  - Aktiva uppdrag visas med slutdatum. Systemet varnar 60 dagar innan ett uppdrag tar slut om inget annat
    aktivt uppdrag tar vid, och när slutdatumet har passerat.

## 5. Verifieringskedjor

Varje affärshändelse kan följas från underlag till rapport och tillbaka.

- **Kundfaktura:** tidrapport → fakturarad → faktura (nummer) → verifikation F → inbetalning I
  ← bankpost → huvudbok/rapporter → SIE-export till Visma (markering `(F<nr>)`).
- **Leverantörsfaktura/kvitto:** bilaga (PDF/foto med SHA-256) → leverantörsfaktura → verifikation L →
  betalfil (end-to-end `LF<id>`) → bankpost → verifikation U.
- **Lön:** anställd → lönespecifikation → lönekörning → verifikation S → AGI-underlag.
- **Visma-data:** SIE-fil → verifikation V med Vismas serie och nummer i texten och som externt id.

Från en verifikation finns länkar till faktura, bilaga, bankpost och rättelser, och tvärtom.

## 6. Räkenskapsinformation, arkivering och säkerhet

- **Lagring:** databasen och bilagorna finns på datorns disk utanför OneDrive. Disken ska vara krypterad
  (BitLocker), eftersom den innehåller lönedata och personnummer.
- **Säkerhetskopior:** vid varje start kopieras databasen till `OneDrive\bitero-erp-backups\` (de 30 senaste
  sparas). Bilagor speglas till `bitero-erp-backups\bilagor-krypterade\` och raderas aldrig där. Mappen synkas
  via OneDrive, så en kopia finns utanför datorn. Källkoden finns dessutom i GitHub.
  - **Kryptering:** alla kopior (databas, bilagor och årsarkiv) krypteras med AES-256-GCM. Nyckeln ligger i
    `bitero-erp-data\backupnyckel.txt` (inte i OneDrive) och ska också finnas i ägarens lösenordshanterare.
    Utan nyckeln går kopiorna inte att läsa. Ändras en krypterad fil upptäcks det när den läses.
  - **Extern kopia:** en gång i månaden körs `extern-kopia.bat`, som kopierar de krypterade kopiorna till en
    USB-disk som förvaras skilt från datorn (skydd mot ransomware och misstag som synkas via OneDrive).
    Kopian kontrolleras direkt efter kopieringen.
  - **Återläsning:** `uv run python manage.py dekryptera <fil eller mapp>`. Varje start kontrollerar att
    senaste kopian går att dekryptera och är en hel databas. Prova en fullständig återställning minst en gång
    per år.
- **Arkivering:** räkenskapsinformationen ska bevaras i sju år efter utgången av kalenderåret då räkenskapsåret
  avslutades. Efter varje bokslut skapas ett årsarkiv (**Bokföring → Årsarkiv**). Det är en zip-fil med:
  - SIE 4 med balanser och alla verifikationer,
  - PDF av verifikationslista, huvudbok, resultat- och balansrapport och momsrapporter,
  - alla bilagor,
  - behandlingshistoriken med kontroll av hashkedjan,
  - denna systemdokumentation,
  - en innehållsförteckning med SHA-256 per fil.

  En krypterad kopia sparas automatiskt i `bitero-erp-backups\arsarkiv\`. Det nedladdade arkivet är läsbart
  utan programmet; kopian i OneDrive läses med `dekryptera` och backupnyckeln.
- **Uppdateringar och sårbarheter:** beroendenas versioner är låsta i `uv.lock`. GitHub kontrollerar varje
  vecka och vid varje ändring att inget beroende har en känd sårbarhet (pip-audit), och Dependabot föreslår
  uppdateringar. En uppdatering tas in först när alla automatiska tester gått igenom, och registreras som ny
  version i behandlingshistoriken vid nästa start.
- **Behörighet:** endast inloggade användare. Lösenord sparas som hash och visas aldrig i historiken.
- **Claude-koppling (MCP):** Claude i Claudes skrivbordsapp kan registrera kvitton och leverantörsfakturor,
  rapportera tid, skapa fakturautkast, stämma av bankhändelser, bokföra manuella verifikationer (serie A) och
  rätta verifikationer, via en lokal MCP-server (`manage.py mcp`). Den startas av appen på samma dator och
  lyssnar inte på nätverket.
  - Underlag läses bara från inkorgen (`OneDrive\Kvitton inkorg`, ändras med `BITERO_INKORG_DIR`).
    Filnamn utanför inkorgen avvisas.
  - Varje skrivande anrop måste godkännas av användaren i appen. Läsande anrop (inkorg, kontoplan, sök,
    huvudbok, rapporter) är markerade som skrivskyddade.
  - Claude kan inte bokföra lön, göra momsavräkning eller bokslut, låsa perioder, slutföra, skicka eller
    makulera fakturor eller skapa betalfiler. Fakturautkast får nummer och bokförs först när användaren
    slutför dem. Kundfakturor och löner kan inte rättas via Claude.
  - Bokföringen går genom samma tjänstefunktioner och behandlingsregler som webbsidorna (avsnitt 4), med samma
    kontroller: balans, låst period, aktiva konton och att en bankhändelse bara kan hanteras en gång. Underlaget sparas som
    bilaga till verifikationen och filen flyttas till `Bokförda\ÅÅÅÅ-MM\` i inkorgen.
  - Dubblettkontroll: samma fil (SHA-256) stoppas alltid. Samma datum, motpart och belopp stoppas om inte
    användaren uttryckligen godkänt en dubblett.
  - Alla ändringar loggas i behandlingshistoriken med användaren **Claude** och källan `MCP <verktyg>`.
  - Fel bokningar rättas som vanligt med rättelseverifikation; verifikationer kan inte ändras.

## 7. Behandlingshistorik

Under **Inställningar → Behandlingshistorik** loggas automatiskt:

- varje registrering, ändring och radering i register, fakturor, tid, leverantörsfakturor, bank, lön och budget,
  med värde före och efter ändringen,
- varje bokförd verifikation med samtliga rader,
- inloggningar, också misslyckade,
- varje ny programversion (se nedan).

Även massuppdateringar loggas, eftersom varje modell använder en loggande QuerySet. Varje händelse sparar
tidpunkt, användare och källa (sida eller kommando). Händelserna kan inte ändras eller
raderas i systemet. Varje händelse innehåller också en SHA-256-kontrollsumma som räknas på innehållet och på
föregående händelses summa (hashkedja). Ändras eller tas en händelse bort direkt i databasfilen bryts kedjan.
Det visas när du klickar **Kontrollera att loggen är orörd**.

**Systemförändringar.** All källkod versionshanteras med git. Varje ändring checkas in med en beskrivning och
laddas upp till ett privat GitHub-repo, där testerna körs automatiskt. Det ger en fullständig historik över vad
som ändrats i programmet, när och varför. Vid varje start räknas en kontrollsumma ut för alla programfiler. Har något ändrats sedan
förra starten körs först de automatiska testerna (avsnitt 8). Sedan registreras en ny systemversion med
versionsnummer, git-commit, tidpunkt, testresultat och vilka filer som har lagts till, ändrats eller tagits bort.
Vad ändringarna innebär beskrivs i ändringsloggen nedan.

## 8. Kontroller och tester

- **Automatiska tester** (mappen `tester\`, körs med `test.bat` och vid varje start efter en uppdatering)
  kontrollerar bl.a.:
  - balans, nummerserier, låsning, oföränderlighet och rättelser,
  - konteringen av kund- och leverantörsfakturor, kvitton och löner,
  - momsrutor och momsavräkning,
  - arbetsgivaravgifter per åldersgrupp,
  - SIE-export och -import, bankfiler, avstämningsregler och betalfil,
  - behandlingshistorikens hashkedja,
  - att alla sidor går att öppna.

  Testerna körs mot en tillfällig databas och rör inte bokföringen. Misslyckas de vid start varnar programmet,
  och den nya versionen registreras inte.
- **Kontroller vid varje start:** innan programmet startar kontrolleras att
  - databasfilen är hel (SQLite `integrity_check`),
  - behandlingshistorikens hashkedja är obruten,
  - varje verifikation balanserar,
  - säkerhetskopian togs och går att dekryptera och läsa,
  - en extern kopia tagits de senaste 35 dagarna,
  - programfilerna är oförändrade jämfört med versionen i git (annars går versionen inte att spåra).

  Vid fel frågar programmet om det ska startas ändå, och felet visas överst på varje sida tills det är åtgärdat.
- **Kodkvalitet:** GitHub kör vid varje ändring också ruff (statisk kodanalys) och mäter hur stor del av koden
  testerna täcker. Täckningen får inte understiga 75 %.
- **Felloggning:** fel och varningar skrivs till `bitero-erp-data\fel.log` (roterar vid 2 MB, fem filer sparas).
- **Inloggning:** sessionen gäller i sju dagar. Datorn ska låsas automatiskt efter några minuters inaktivitet.
- **Löpande avstämning:** jämför varje månad saldobalans och momsrapport med Visma. Stäm av 1930 mot
  kontoutdraget och 1630 mot skattekontot.
- **Årliga uppdateringar:** skattetabeller (importeras under Inställningar), arbetsgivaravgifter och momsregler.
  Ändringar i satserna görs i programmet, testerna uppdateras och en ny version registreras.
