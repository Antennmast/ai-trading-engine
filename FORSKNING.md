# Hva forskningen sier, og hva det betyr for oppsettet

Gjennomgang 11. november 2026. Formålet er å finne hull i opplegget, ikke å love resultater.
Ingenting her er investeringsråd.

## 1. Regelbaserte strategier (faktorer)

- **De fleste kjente faktorene holder ved etterprøving.** Jensen, Kelly og Pedersen testet
  153 faktorer i 93 land og fant at flertallet lar seg gjenskape, også utenfor USA og
  utenfor opprinnelig periode. Momentum er blant dem.
  [NBER w28432](https://nber.org/system/files/working_papers/w28432/w28432.pdf)
- **Avkastningen krymper etter at en strategi blir kjent.** McLean og Pontiff fant at
  avkastningen for 97 publiserte signaler i snitt var godt under halvparten etter
  publisering, og en senere studie fant at nesten ingenting var igjen etter handelskostnader
  i perioden etter 2005.
  [CXO-sammendrag](https://www.cxoadvisory.com/big-ideas/effects-of-market-adaptation),
  [Zeroing in on the expected returns of anomalies](https://core-prod.cambridgecore.org/core/services/aop-cambridge-core/content/view/945133D5A3ECEEAF466AEE91551FD225/S0022109022000874a.pdf/zeroing-in-on-the-expected-returns-of-anomalies.pdf)
- **Fallet er tydeligst i USA.** Jacobs og Müller fant ingen pålitelig nedgang etter
  publisering i 38 andre aksjemarkeder.
  [Jacobs og Müller](https://www.wiwi.hu-berlin.de/en/Professorships/bwl/finance/seminars/papers-fin-acc-res-seminars/17s/jacobs.pdf)
- **Momentum kan krasje.** Tapene kommer særlig når markedet snur opp etter et kraftig
  fall og svingningene er høye. Rene kjøpsstrategier rammes mindre enn strategier som
  også shorter, og nedskalering når svingningene øker har dempet krasjene historisk.
  [Alpha Architect](https://alphaarchitect.com/risk-of-momentum-crashes/),
  [Swedroe om Daniel og Moskowitz](https://www.etf.com/sections/index-investor-corner/swedroe-downside-momentum)

**For oss:** regn med klart mindre enn det en tilbakeprøving viser. Aksjescore-testens
+8 % per år mot lik vekt er et tak, ikke en forventning.

## 2. Dagshandel

- I Brasil tapte 97 % av dem som dagshandlet i mer enn 300 dager, etter gebyrer.
- På børsen i Taiwan var under 1 % av dagshandlerne lønnsomme på en måte som gjentok seg.

[Sammenstilling av studiene](https://thortradecopier.com/blog/what-percentage-of-day-traders-lose-money),
[Barber, Lee, Liu og Odean](https://faculty.haas.berkeley.edu/odean/papers/day%20traders/Day%20Trading%20Skill%20110523.pdf)

**For oss:** Dagshandel-modellen er en kontroll som viser kostnaden ved høy omsetning.
Den er ikke en kandidat for ekte penger.

## 3. Språkmodeller som handler

- **Tilbakeprøving med språkmodeller er upålitelig.** Modellene har lest historien de
  testes på. Én studie anslår at rundt 37 % av den tilsynelatende treffsikkerheten på
  nyhetsoverskrifter forsterkes av hukommelse, og effekten forsvinner i ekte fremtidstester.
  [Gao, Jiang og Yan](https://arxiv.org/html/2512.23847v1)
- **Resultater i sanntid er blandede.** Én to måneders test fant agenter som slo
  kjøp-og-hold på enkeltaksjer. En løpende kryptotest fant at under halvparten av
  modellsesongene endte i pluss. I en studie av agenter med ekte penger var 15 % i pluss
  over en uke, mot 53 % av vanlige kontoer.
  [Agent Market Arena](https://arxiv.org/pdf/2510.11695),
  [TradeRank Arena](https://www.traderank.ai/llm-trading-benchmark),
  [What LLM Trading Agents Actually Do in Production](https://arxiv.org/pdf/2609.05663)
- **Smartere modell betyr ikke bedre handel.** En 50-dagers test av 21 modeller fant
  ingen positiv sammenheng mellom generell dyktighet og handelsresultat.
  [LiveTradeBench](https://arxiv.org/html/2511.03628)
- **Samme spørsmål gir ulike svar.** Språkmodeller kan handle svært ulikt fra kjøring til
  kjøring, så én enkelt kjøring sier lite.
  [AlphaForgeBench](https://arxiv.org/pdf/2602.18481)
- **Kostnader mangler ofte.** En gjennomgang fant at bare én av nitten sentrale studier
  hadde en eksplisitt kostnadsmodell.
  [CLQT](https://arxiv.org/pdf/2606.29771)

**For oss:** oppsettet tester bare fremover i tid og har kostnader, som er det forskningen
etterlyser. Hullet var at agenten svarte én gang per dag.

## 4. Hull vi fant, og hva som er gjort

| Hull | Tiltak |
|---|---|
| Momentum brukte siste halvår inkludert siste måned, og bare 4 aksjer | Endret til standarden «12-1» (siste år uten siste måned) og 10 aksjer |
| Språkmodellen svarte én gang per dag | Spørres 3 ganger, medianen brukes, og stort sprik noteres i dagboken |
| Ingen statistisk målestokk | t-verdi mot referansen vises etter 60 børsdager |
| Ingen forhåndsbestemte kriterier | Skrevet inn i `ENDRINGSLOGG.md` |

## 5. Hull som står åpne

- **Ingen markedsbrems.** Modellene kjøper like gjerne i et marked i fritt fall. Et filter
  som stopper nye kjøp når indeksen ligger under sitt langsiktige snitt, eller som
  halverer posisjonene når svingningene er høye, er den best dokumenterte forbedringen.
  Den bør testes som egen modell.
- **Inntjeningsmeldinger.** Kursreaksjonen etter kvartalsrapporter er et godt dokumentert
  mønster, men krever rapportdatoer vi ikke henter i dag.
- **Bare overlevende selskaper.** Aksjelisten er dagens. Det påvirker ikke fremtidstesten,
  men alle tilbakeprøvinger ser for gode ut.
- **Kort test og mange modeller.** Tre til seks måneder er lite. Forskjeller under en
  t-verdi på 2 er støy, og med åtte modeller vil én passere av og til ved ren flaks.
