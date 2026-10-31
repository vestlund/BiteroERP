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
| Säkerhetskopior | Tas automatiskt vid varje start till `OneDrive\bitero-erp-backups\`. |

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
Fakturanummer tilldelas när fakturan slutförs, i obruten följd. Makulering skapar en rättelse.

### Inbetalning av kundfaktura (serie I)
Debet bankkonto (normalt 1930), kredit 1510, med fakturans belopp att betala.

### Leverantörsfaktura (serie L)
Debet kostnadskonto (nettobelopp), debet 2641 Ingående moms, kredit 2440 Leverantörsskulder.
**Kvitto:** samma, men kredit på betalkontot (1930 företagskonto eller 2893 eget utlägg). Kvittot markeras betalt.

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
  sparas). Bilagor speglas till `bitero-erp-backups\bilagor\` och raderas aldrig där. Mappen synkas via OneDrive,
  så en kopia finns utanför datorn. Källkoden finns dessutom i GitHub.
  Prova att återläsa en kopia minst en gång per år.
- **Arkivering:** räkenskapsinformationen ska bevaras i sju år efter utgången av kalenderåret då räkenskapsåret
  avslutades. Efter varje bokslut sparas:
  - SIE 4-export för året,
  - PDF av huvudbok, verifikationslista och momsrapporter,
  - CSV-export av behandlingshistoriken,
  - utskrift av denna systemdokumentation.
- **Behörighet:** endast inloggade användare. Lösenord sparas som hash och visas aldrig i historiken.

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
- **Löpande avstämning:** jämför varje månad saldobalans och momsrapport med Visma. Stäm av 1930 mot
  kontoutdraget och 1630 mot skattekontot.
- **Årliga uppdateringar:** skattetabeller (importeras under Inställningar), arbetsgivaravgifter och momsregler.
  Ändringar i satserna görs i programmet, testerna uppdateras och en ny version registreras.
