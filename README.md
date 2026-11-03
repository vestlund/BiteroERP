# Konsultsystem

Litet affärssystem för konsultbolaget: kunder, leverantörer, artiklar, kontoplan, uppdrag, tidrapport och fakturor.
Django + SQLite, körs lokalt på din laptop och används i webbläsaren.

## Starta

**Mac/Linux:** dubbelklicka eller kör `./start.sh` i Terminal
**Windows:** dubbelklicka på `start.bat`

Första gången:
1. `uv` (Python-verktyget) installeras om det saknas, och Python + Django laddas ner automatiskt.
2. Databasen skapas och dina Visma-exporter i mappen `import/` läses in.
3. Du får välja användarnamn och lösenord i terminalen.
4. Webbläsaren öppnas på http://127.0.0.1:8000

**Stoppa:** Ctrl+C i terminalfönstret (eller stäng det).
Programmet körs med webbservern waitress. Om något går fel visas en enkel felsida och detaljerna skrivs till
`bitero-erp-data\fel.log`. Utvecklingsläge med Djangos felsidor: `set BITERO_DEBUG=1` innan `start.bat`.
Systemet lyssnar bara på din egen dator (127.0.0.1) – ingen annan kommer åt det.

## Första inställningar

1. **Översikt → "dina företagsuppgifter"**: fyll i namn, org.nr, momsnummer, bankgiro och vår referens. Det står på fakturorna.
2. **Register → Betalningsvillkor**: Visma-koderna är tolkade som antal dagar netto (kod 30 = 30 dagar). Kontrollera dem, särskilt kod 200 (Webhallen).
3. **Register → Artiklar**: tim-artiklarna är kopplade till konto 3041 (tjänst 25 %), Expeditionsavgift till 3540.

## Arbetsflöde

1. **Uppdrag → Nytt uppdrag**: välj kund och artikel (styr pris, konto och moms). Du kan ange ett eget timpris.
2. **Tidrapport**: hela månaden med en rad per dag och en kolumn per uppdrag. Skriv timmar direkt i rutorna.
   Klicka **+** bredvid en ruta för att lägga till en text på fakturan för den dagen eller markera tiden som ej fakturerbar.
3. **Fakturor → Fakturera tid**: välj kund och "tid till och med". Ett utkast skapas med en rad per uppdrag
   (eller en per tidpost) och tiden markeras som fakturerad.
4. Kontrollera utkastet, lägg till rader vid behov och klicka **Slutför och ge nummer**. Då låses fakturan.
5. **Visa / skriv ut PDF** → skriv ut eller spara som PDF och skicka till kunden.
6. **Markera betald** när pengarna kommit.

Raderar du ett utkast eller makulerar en faktura blir tiden ofakturerad igen.

## Bokföring

Visma är fortfarande huvudbok. Bitero ERP bokför parallellt och skickar verifikationerna till Visma via SIE.

- **Kundfakturor** bokförs automatiskt när du slutför dem (serie F). När de betalas bokförs en inbetalning (serie I).
  Makulering skapar en rättelse.
- **Inköp → + Leverantörsfaktura / + Kvitto**: välj leverantör och ange belopp. Momsen räknas ut och kontot föreslås.
  Bifoga PDF eller foto. Bokförs i serie L, och betalningen i serie U.
- **Bank**: importera kontoutdrag (CSV) från banken. Varje post matchas mot kundfakturor, leverantörsfakturor
  eller befintliga verifikationer, eller bokförs mot ett valfritt konto (serie B).
- **Bokföring → Ny verifikation**: manuella verifikationer (serie A) med mallar för skattekontot (1630),
  F-skatt, moms med mera. Summan visas medan du skriver.
- **Moms**: momsdeklarationens rutor per månad, kvartal eller år, utifrån kontoplanens momskoder.
  "Bokför momsavräkning" nollar momskontona mot 2650 (serie M).
- **Resultat och balans**, **Huvudbok** och **Verifikationer** med sök.
- **Visma (SIE)**: exportera nya verifikationer till Visma. Importera ingående balanser (IB) från Vismas SIE-fil.

Bokförda verifikationer kan inte ändras eller raderas. Fel rättas med **Rätta (vänd)**, som skapar en motverifikation.
Under **Register → Företagsuppgifter** kan du låsa bokföringen till och med ett datum.

**Undvik dubbelbokning:** det du bokför här och exporterar ska inte också registreras direkt i Visma.

Övriga exporter: **Fakturor → Exportera CSV** och **Tid → Exportera CSV**.

## Backup

Varje start tar en **krypterad** kopia av databasen till `OneDrive\bitero-erp-backups\` (de 30 senaste sparas).
Nya bilagor krypteras till `bitero-erp-backups\bilagor-krypterade\`. Själva bilagorna ligger i
`bitero-erp-data\bilagor\`. Manuellt: `uv run python manage.py backup`. Annan plats: sätt `BITERO_BACKUP_DIR`.

**Backupnyckeln** visas första gången du startar. Spara den i din lösenordshanterare. Visa den igen med
`uv run python manage.py backupnyckel`. På en ny dator anger du den med `... backupnyckel --ange <nyckel>`.

**Extern kopia en gång i månaden:** anslut en USB-disk och kör `extern-kopia.bat`. Förvara disken skilt från
datorn. Programmet påminner när det gått mer än 35 dagar.

**Databasen ligger utanför OneDrive**, i `C:\Users\<du>\bitero-erp-data\konsultsystem.sqlite3`,
eftersom SQLite kan skadas om OneDrive synkar filen medan programmet kör. Kopiorna är stängda filer och
kan synkas utan risk. Vill du ha databasen någon annanstans: sätt `BITERO_DATA_DIR`.

**Återställa en backup:** stäng programmet och kör
`uv run python manage.py dekryptera "%OneDrive%\bitero-erp-backups\konsultsystem_<datum>.sqlite3.krypterad"`.
Kopiera sedan den dekrypterade filen från `bitero-erp-data\dekrypterat\` till
`bitero-erp-data\konsultsystem.sqlite3` och ta bort eventuella `-wal`/`-shm`-filer bredvid den.
Bilagorna: `... dekryptera "%OneDrive%\bitero-erp-backups\bilagor-krypterade" --till "%USERPROFILE%\bitero-erp-data\bilagor"`.

**Kontroller vid start:** programmet kontrollerar databasen, behandlingshistoriken, att alla verifikationer
balanserar, säkerhetskopian, den externa kopian och att programfilerna inte ändrats lokalt. Problem visas i
fönstret och överst på varje sida. Har du ändrat i koden lokalt: `git checkout .` återgår till GitHub-versionen.

## Versionshantering (git och GitHub)

Koden ligger i `C:\kod\bitero-erp` (inte i OneDrive) och i ett privat GitHub-repo.
Första gången: kör `flytta-till-git.bat` från den gamla programmappen i OneDrive. Det hämtar koden från
GitHub till `C:\kod\bitero-erp` och tar med Visma-filerna och säkerhetskopiorna.

- **Hämta uppdateringar:** `git pull` i programmappen, sedan starta som vanligt. Testerna körs automatiskt.
- **Se historiken:** `git log` eller på GitHub. Där körs testerna också vid varje uppladdning (fliken Actions).
- Databaser, säkerhetskopior, Visma-filer och PDF:er checkas aldrig in (se `.gitignore`).
- **Säkerhetsuppdateringar:** GitHub söker varje vecka efter kända sårbarheter i beroendena (pip-audit) och
  Dependabot föreslår uppdateringar som pull requests. Om testerna är gröna: tryck **Merge** på GitHub och
  kör sedan `git pull` här. Kontroll lokalt: `uvx pip-audit`.

## Bokföra kvitton med Claude

Claude i skrivbordsappen kan bokföra kvitton direkt i systemet. Allt körs lokalt; inget öppnas mot internet.

1. Kör `installera-claude.bat` en gång. Starta sedan om Claude-appen helt (avsluta i systemfältet).
2. Lägg kvitton (JPG, PNG eller PDF) i `OneDrive - Bitero\Kvitton inkorg`. Från mobilen: spara eller dela
   bilden till den mappen i OneDrive-appen. I en chatt som är kopplad till datorn kan du också ladda upp
   bilden och be Claude lägga den i inkorgen.
3. Skriv till exempel *"bokför kvittona i inkorgen"*. Claude läser underlaget, föreslår konto och moms och
   ber om ditt godkännande för varje bokning.

Claude kan också:
- **rapportera tid** ("8 timmar på Nordhamn i dag") och **skapa fakturautkast** av ofakturerad tid
  (du slutför och skickar själv),
- **stämma av bankhändelser** ("gå igenom bankhändelserna"): koppla till fakturor eller bokföra mot motkonto,
- **bokföra manuella verifikationer**, t.ex. skattekontot, och **rätta** en felaktig verifikation,
- **svara på frågor** om huvudbok, resultat och balans, likviditet, obetalda fakturor och viktiga datum.

Lön, momsavräkning, bokslut, låsning, att slutföra och makulera fakturor och betalfiler gör du själv.

Datorn måste vara på och Claude-appen öppen. Webbservern behöver inte köra. HEIC-bilder (iPhone) stöds inte;
ställ in kameran på "Mest kompatibel" eller exportera som JPG. Ta bort kopplingen med
`uv run python manage.py installera_claude --ta-bort`.

## Importera nya exporter

Lägg nya Visma-filer i `import/` och kör `uv run python manage.py importera_visma`.
Befintliga poster uppdateras på nummer och nya läggs till. Inget raderas.

## Lön

**Lön → Anställda**: lägg upp den anställda med personnummer, månads- eller timlön och skattetabell.
**Lön → Lönekörningar → + Ny lönekörning**: lönerna skapas för alla aktiva anställda. Lägg till förmåner,
frånvaro, traktamente eller utlägg per lönespec. Klicka sedan **Bokför och lås** (serie S: 7210/7385/7321 mot 2710, 1930,
7510/2730 och 7399).

- **Skatteavdrag**: importera Skatteverkets skattetabeller (CSV) under *Inställningar → Skattetabeller*, så räknas
  avdraget ut automatiskt. Annars skriver du in beloppet från tabellen. Samma underlag som förra månaden ger samma förslag.
- **Arbetsgivaravgifter** räknas efter ålder: 31,42 %, 10,21 % (fyllt 67 vid årets ingång), 0 % (född 1937 eller tidigare)
  samt tillfälligt 20,81 % upp till 25 000 kr/mån för 19–23-åringar (1 april 2026 – 30 september 2027).
- **Arbetsgivardeklaration**: ruta 011, 012, 013 och 001 per anställd, plus summor att föra in i Skatteverkets e-tjänst.
- **Lönespecifikationer** skrivs ut eller sparas som PDF från lönekörningen.

## Automatisk bankavstämning

Importera kontoutdraget under **Kassa- och bankhändelser**, helst som **camt.053** (ISO 20022) från internetbanken.
CSV fungerar också. Direkt efter importen stäms allt säkert av automatiskt:

1. Betalningar från betalfilen känns igen på referensen (LF…) och markerar leverantörsfakturan som betald.
2. Inbetalningar med rätt belopp och fakturanummer eller OCR markerar kundfakturan som betald.
3. Utbetalningar med rätt belopp och OCR eller fakturanummer betalar leverantörsfakturan.
4. Poster som redan bokförts på 1930, till exempel löner och kvitton, kopplas om det finns exakt en verifikation med samma belopp inom ±3 dagar.
5. **Matchningsregler** (Inställningar → Matchningsregler) med "bokför automatiskt" bokförs direkt.
   Övriga regler visas som förslag. Bocka i "spara som regel" när du bokför en post för hand, så sköts den automatiskt nästa gång.

Det som inte är säkert hamnar under "Att hantera", med förslag. Automatiskt avstämda poster syns under fliken med samma namn.
Helt automatisk hämtning utan fil kräver Handelsbankens Premium API (Kontoinformation), som kan kopplas in senare.

Lönedelen är ett stöd för enkla löner (t.ex. ägarlön). Semesterlöneskuld, sjuklöneregler och kollektivavtal räknas inte ut automatiskt.

## Import och export (Inställningar → Import och export)

- **Kund- och leverantörsfakturor från Visma** (SIE-fil, Excel eller CSV). Från SIE läses fakturorna ut ur verifikationerna:
  1510 ger kundfakturor och 2440 leverantörsfakturor. Betalningar mot bankkonto kopplas till fakturorna, och fakturanummer
  och namn hämtas ur verifikationstexten. Förfallodatum räknas fram från betalningsvillkoren. För Excel och CSV gäller följande: kolumnerna känns igen automatiskt och kan justeras,
  och du ser en förhandsgranskning innan importen. Fakturorna får status, förfallodatum och OCR men **ingen bokföring**,
  eftersom den kommer från Visma via SIE. Nummerserien för kundfakturor fortsätter efter Vismas högsta nummer.
  Samma fil kan läsas in igen för att uppdatera status. Obetalda fakturor matchas sedan automatiskt i bankavstämningen.
- **Betalningar från Visma (SIE)**: in- och utbetalningar läses ur SIE-filen. Inbetalningar känns igen på 1510 mot bank och
  utbetalningar på 2440 mot bank. De kopplas till öppna kund- och leverantörsfakturor, först på fakturanummer och sedan på belopp och namn.
  Du ser en förhandsgranskning innan något ändras. Matchade fakturor markeras som betalda, men ingen bokföring skapas.
- **Kunder, artiklar, leverantörer, resultatenheter (kostnadsställen) och kontoplan**: export och import i Vismas
  CSV-format, med samma kolumner som Vismas egna exporter. Vid import matchas posterna på nummer.
- **Export av företagsuppgifter** som CSV eller JSON.
- **Betalfil till banken**: välj obetalda leverantörsfakturor och få en ISO 20022-fil (pain.001) som laddas upp i
  internetbanken. Bankgirot tar inte längre emot det gamla LB-formatet. Kräver företagets IBAN/BIC under Företagsuppgifter.
  Testa första gången med en betalning.
- **SIE**: export av kundfakturor och inbetalningar, export av all bokföring, samt import av Vismas verifikationer
  (serie V) och ingående balanser. Det som skapats här och exporterats till Visma känns igen och importeras inte igen.
- **One Stop Shop**: underlag per land och momssats för försäljning till privatpersoner i andra EU-länder (CSV).
  Skatteverkets exakta inläsningsformat är inte verifierat, så filen är ett underlag och inte en färdig deklarationsfil.

## Budget och prognos

- **Budget och prognos → Budgetar → + Ny budget**: välj år. Förslaget räknar timmar för aktiva uppdrag
  (arbetsdagar × timmar per dag × beläggning, med svenska helgdagar) och kostnader enligt snittet för de senaste 12 månaderna.
  Du kan också kopiera en tidigare budget.
- I redigeringen skriver du timmar per månad för uppdrag, belopp för andra intäkter och kostnader per konto. Skriver du ett
  årsbelopp i summakolumnen fördelas det jämnt.
- **Uppföljning**: budget mot utfall per månad, med upparbetat (tidrapporter × pris), fakturerat/bokfört och kostnader.
  Avslutade månader räknas med utfall och resten med budget, vilket ger en prognos för helåret.
  Ackumulerat-diagrammet visar hur du taktar mot budgeten. Huvudbudgeten för året visas också på översikten.
- Importerar du Vismas verifikationer via SIE blir utfallet komplett, även för det som bara bokförts i Visma.

## Struktur

- `register/`: kontoplan, kunder, leverantörer, artiklar, företagsuppgifter, Visma-import
- `bokforing/`: verifikationer, inköp, bank, moms, rapporter, SIE
- `lon/`: anställda, lönekörningar, skattetabeller, arbetsgivardeklaration
- `budget/`: budgetar, prognos och uppföljning
- `verksamhet/`: uppdrag, tid, fakturor, SIE-export, sidorna
- `templates/`: HTML-sidorna, inklusive fakturalayouten `verksamhet/faktura_utskrift.html`
- `config/datadir.py`: styr var databasen ligger

## Planering, bokslut och deklarationer

- **Planering → Likviditet:** saldot på företagskontot vecka för vecka, med fakturor, löner, skatter och moms.
- **Planering → Skatt och utdelning:** skatteprognos mot F-skatt och gränsbelopp enligt 3:12-reglerna.
- **Planering → Lön eller utdelning:** räknar fram hur mycket du ska ta i lön och utdelning för att få ut mest efter
  skatt, med ett maxbelopp för lön + utdelning om du vill. Claude kan räkna på samma sak ("hur ska jag ta ut
  pengar i år?").
  Överst visas rekommenderad lägsta lön för full SGI och allmän pension och vad den kostar bolaget inklusive
  arbetsgivaravgift.
- **Planering → Viktiga datum:** Skatteverkets och Bolagsverkets datum, även som kalenderfil.
- **Försäljning → Uppdragsuppföljning:** beläggning, intäkt per kund och uppdrag som tar slut.
- **Fakturor:** "Visa PDF" och "Skicka med e-post" (ett utkast som öppnas i Outlook). Förfallna fakturor får
  knappar för påminnelse.
- **Inköp:** bifoga en PDF så läses leverantör, datum, belopp och moms av.
- **Moms och arbetsgivardeklaration:** "Ladda ner fil till Skatteverket" och ladda upp i deras e-tjänst.
- **Bokföring → Bokslut:** checklista, periodiseringar, avskrivningar (Bokföring → Inventarier), skatt, årets
  resultat och underlag till inkomstdeklarationen.
- **Bokföring → Årsarkiv:** allt för ett år i en zip-fil, som sparas i sju år.

## Behandlingshistorik, tester och systemdokumentation

- **Inställningar → Behandlingshistorik** visar alla registreringar, ändringar och raderingar: vem som gjorde vad,
  när och vad värdet var före och efter. Bokförda verifikationer loggas med alla rader, och inloggningar loggas också.
  Loggen kan inte ändras inifrån systemet. Varje händelse är kedjad till den förra med en kontrollsumma, och
  **Kontrollera att loggen är orörd** visar om någon har ändrat i databasfilen direkt. Loggen kan exporteras till CSV.
- **Automatiska tester:** kör `test.bat`. Efter varje uppdatering körs testerna automatiskt vid start. Misslyckas de
  får du en varning, och detaljerna sparas i `bitero-erp-data\senaste_testfel.txt`.
- **Systemversioner:** varje ny programversion registreras vid start, med vilka filer som ändrats.
- **Inställningar → Systemdokumentation** beskriver kontering, verifieringskedjor, arkivering och kontroller.
  Skriv ut den och spara den med bokslutet varje år. Ändringar beskrivs i `docs/andringslogg.md`.
