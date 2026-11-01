# Ändringslogg

Varje version registreras automatiskt i behandlingshistoriken när den startas första gången, med
fingeravtryck och lista över ändrade filer. Här beskrivs vad ändringarna innebär.

## 2026.11.01-3

- Sidan Skatt och utdelning och påminnelseavgiften på fakturor använder Django-formulär i stället för egen
  tolkning av indata. Felaktiga värden ger ett meddelande och sparas inte.
- Räkenskapsår i adressen (`?ar=`) kontrolleras på ett ställe (`config.formular.valt_ar`). Moms, budget,
  uppdragsuppföljning och bokslut visar standardåret i stället för ett fel när året är ogiltigt.

## 2026.11.01-2

- **Lön eller utdelning** (Lön → Lön eller utdelning): räknar fram fördelningen mellan lön och utdelning som
  ger störst nettobelopp enligt 3:12-reglerna och 2026 års inkomstskatt, med valfritt maxbelopp för lön +
  utdelning, lägsta lön och fritt eget kapital. Jämförelsetabell och diagram.
- Nya företagsuppgifter: kommunalskatt, begravnings- och kyrkoavgift och maxbelopp för uttag.
- Claude kan räkna på samma sak med verktyget `lon_eller_utdelning`.

## 2026.11.01-1

- **Claude-kopplingen utökad** med tid (rapportera tid, ofakturerad tid), fakturautkast av tid,
  avstämning av bankhändelser, manuella verifikationer med underlag, rättelser och läsning av huvudbok,
  resultat och balans, likviditet och viktiga datum. Varje skrivning godkänns i Claude-appen.
- Logiken för att fakturera tid och hantera bankhändelser är flyttad från webbsidorna till tjänstefunktioner
  (`verksamhet/tjanster.py`, `bokforing/avstamning.hantera`) som både webben och Claude använder.
- En bankhändelse som redan är kopplad till en verifikation kan inte bokföras en gång till.

## 2026.10.31-7

- Bilagor lämnades öppna efter att kontrollsumman räknats, vilket gör att Windows låser filen. Rättat.
- Testerna fungerar nu på Windows (fyra tester förutsatte Linux eller att git var installerat), och GitHub kör
  testerna på både Windows och Linux.
- Läsbara namn för transaktioner, ingående balanser, budget- och lönerader i behandlingshistoriken.

## 2026.10.31-6

- **Krypterade säkerhetskopior** (AES-256-GCM) av databas, bilagor och årsarkiv. Befintliga okrypterade
  kopior i OneDrive krypteras och tas bort vid första start. Nyckeln visas en gång och sparas i
  lösenordshanteraren. Nya kommandon: `backupnyckel` och `dekryptera`.
- **Extern kopia** till USB-disk med `extern-kopia.bat`, med påminnelse efter 35 dagar.
- **Kontroller vid varje start:** databasens integritet, hashkedjan, balans per verifikation, att
  säkerhetskopian går att läsa, extern kopia och lokala ändringar i programfilerna. Fel visas även i webbläsaren.
- Felloggen omfattar nu även varningar, säkerhetshändelser och webbservern.
- Inloggningen gäller i sju dagar (tidigare 14).
- GitHub kör statisk kodanalys (ruff) och kräver minst 75 % testtäckning. Några små fel som ruff hittade är
  rättade (oanvända variabler och importer, explicit fältlista i formuläret för anställda).

## 2026.10.31-5

- **Claude-koppling (MCP):** Claude i skrivbordsappen kan läsa kvitton i inkorgen, söka leverantörer och
  tidigare konteringar, och registrera kvitton och leverantörsfakturor med underlaget som bilaga.
  Varje bokning godkänns i appen och loggas som användaren Claude. Installeras med `installera-claude.bat`.
- Ny inställning `BITERO_INKORG_DIR` för inkorgsmappen.

## 2026.10.31-4

Nio nya funktioner:
- **Årsarkiv** (Bokföring → Årsarkiv): zip med SIE 4, PDF-rapporter, bilagor, behandlingshistorik och
  systemdokumentation. Kontrollsummor per fil. Kopia sparas i OneDrive.
- **Filer till Skatteverket:** momsdeklaration (eSKDUpload 6.0) och arbetsgivardeklaration (XML, schema 1.1).
- **Likviditetsprognos** (Planering → Likviditet), vecka för vecka i upp till 52 veckor.
- **Bokslut** (Bokföring → Bokslut):
  - checklista,
  - periodiseringar med återföring,
  - inventarier och avskrivningar,
  - bolagsskatt och årets resultat,
  - underlag till INK2R och INK2S.
- **Fakturor som PDF och e-postutkast** (.eml för Outlook), OCR i samma format som Visma, påminnelser med
  dröjsmålsränta.
- **Avläsning av leverantörsfakturor och kvitton i PDF.**
- **Viktiga datum** (Planering → Viktiga datum) med kalenderfil (.ics).
- **Skatt och utdelning** (Planering → Skatt och utdelning): skatteprognos mot F-skatt och gränsbelopp enligt
  3:12-reglerna från 2026.
- **Uppdragsuppföljning** (Försäljning → Uppdragsuppföljning): beläggning, intäkt per kund och varning när
  ett uppdrag tar slut utan att något annat tar vid. Varningen visas även på översikten.

Övrigt:
- Menyn "Budget och prognos" heter nu "Planering".
- Företagsuppgifter har nya fält för F-skatt, ägarandel och sparat utdelningsutrymme.
- 146 automatiska tester.

## 2026.10.31-3

- **Webbserver:** programmet körs med waitress i stället för Djangos utvecklingsserver. Felsidor med
  programkod är avstängda, och fel skrivs till `bitero-erp-data\fel.log`. Utvecklingsläge: `BITERO_DEBUG=1`.
- **Behandlingshistoriken** fångar massuppdateringar med en egen QuerySet på varje modell (`LoggadQuerySet`)
  i stället för att ändra i Django. Ett test kontrollerar att ingen modell saknar den.
- **Skydd:** massändring och massradering av verifikationer och transaktioner stoppas också på databasnivå
  i programmet. Tidigare skyddades bara ändringar av en enskild verifikation.
- **Formulär:** allt som skickas från bank-, moms-, budget-, tidrapport- och importsidorna valideras med
  Django-formulär. Svenska tal ("1 234,50") godtas överallt. Felaktiga värden ger ett felmeddelande i stället
  för att tyst bli 0.
- **Säkerhet:** "nästa sida"-länkar kan inte längre leda ut ur systemet. Originalfakturor kan visas i
  fakturasidans ram (`X-Frame-Options: SAMEORIGIN`).
- 89 automatiska tester.

## 2026.10.31-2

- Koden versionshanteras med git i ett privat GitHub-repo. Varje systemversion visar sin git-commit
  ("+ändringar" om det fanns ändringar som inte var incheckade).
- GitHub Actions kör de automatiska testerna vid varje uppladdning.
- Säkerhetskopiorna sparas i OneDrive (`OneDrive\bitero-erp-backups`), oberoende av var koden ligger.
- Tre nya tester för var säkerhetskopiorna hamnar (67 totalt).
- Skriptet `flytta-till-git.bat` hämtar koden från GitHub till `C:\kod\bitero-erp`, utanför OneDrive.

## 2026.10.31

- **Behandlingshistorik:**
  - alla registreringar, ändringar och raderingar loggas med före- och eftervärden,
  - bokförda verifikationer loggas med alla rader,
  - inloggningar och programversioner loggas,
  - hashkedja med kontroll av att loggen är orörd, och export till CSV.
- **Automatiska tester:** 64 tester för bokföring, moms, lön, import och export, bank, betalfil, logg och
  alla sidor. De körs vid start när programmet har uppdaterats, och med `test.bat`.
- Systemdokumentation och ändringslogg visas under Inställningar.
- Rättat: ändringar av lösenord syns nu i historiken (utan värdet).

## 2026-10-30 och tidigare (före versionsnumrering)

Systemet byggdes upp i oktober 2026. Funktionerna, i den ordning de kom:

- Register importerade från Visma: kunder, leverantörer, artiklar, kontoplan och kostnadsställen.
  Uppdrag, tidrapport och kundfakturor.
- Parallell bokföring:
  - verifikationer med rättelser och periodlåsning,
  - automatisk bokföring av kund- och leverantörsfakturor och kvitton med bilagor,
  - bankimport och avstämning,
  - moms och skattekonto,
  - SIE-export till Visma.
- Ny layout med sidomeny. Lönehantering med arbetsgivaravgifter 2026, skattetabeller och AGI-underlag.
- Import och export av alla registertyper. Budget och prognos med uppföljning mot tid och fakturor.
- Automatisk avstämning mot Handelsbanken: camt.053/054, matchningsregler och betalfil pain.001.
- Import av kund- och leverantörsfakturor och betalningar från Visma (SIE).
- Koppling av originalfakturor (PDF) till importerade kundfakturor.
