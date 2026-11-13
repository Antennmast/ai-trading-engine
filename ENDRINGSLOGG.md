# Endringslogg

Reglene for hver modell er låst i testperioden. Skal en regel endres, legges det inn som
en ny modell med eget navn og egen portefølje, slik at gamle og nye resultater ikke blandes.
Alt som endres i oppsettet noteres her med dato og begrunnelse.

## 2026-11-11 – start

- Elleve modeller startet med 100 000 NOK hver på sluttkursene 10. november 2026.
- Univers: 278 aksjer på Oslo Børs og 79 store selskaper i Stockholm, København og Helsinki.
  Nye kjøp bare i aksjer med minst 1 MNOK i median dagsomsetning, kurs over 1 kr og ingen
  kurshopp over 50 % på én dag siste år.
- Utbytte krediteres på ex-dato (uten kildeskatt), og signalene beregnes på
  utbyttejusterte kurser. Splitter justerer antall, snittkurs og stop.
- Referanse: alle omsettelige aksjer likt vektet, uten kostnader, med utbyttet reinvestert
  i samme aksje. OSEBX vises i tillegg.
- De andre modellene får utbyttet som kontanter og bruker det i neste kjøp etter egne regler.
- En aksje uten kurser i 10 dager (oppkjøp, avnotering) selges til siste kjente kurs.
- Feiler den daglige kjøringen, opprettes en sak (issue) i repoet.
- De første ordrene ble lagt på dagtid 11. november og bokført til åpningskursen samme
  dag. Beslutningene brukte bare sluttkursene fra 10. november.

- Etter forskningsgjennomgangen (`FORSKNING.md`), før første handel: Momentum endret til
  12-1 med 10 aksjer, og språkmodellen spørres tre ganger per dag med median som score.
- Niende modell lagt til før første handel: Momentum med brems. De ti sterkeste aksjene
  rangeres som i Momentum. En vinner holdes ikke når hjemmemarkedets indeks (OSEBX, OMXS30,
  OMXC25, OMXH25) er under 200-dagerssnittet. Den tidelen står da i kontanter og flyttes
  ikke til andre markeder. Sjekkes ved månedlig bytte, og markedet åpnes igjen når indeksen
  er 1 % over snittet. Oppsettet følger Faber (2007), se `FORSKNING.md`.
- Kjøp under 2 000 kr hoppes over, fordi minstekurtasjen på 10 kr ellers spiser for mye.
- To ASK-varianter lagt til før første handel: Score topp 10 ASK og Momentum ASK. De følger
  samme regler som originalene, men velger bare blant aksjer i selskaper hjemmehørende i
  EØS, bruker kurtasje som Nordnet Mini (0,15 %, minst 29 kr), og får utenlandsk utbytte
  med 15 % kildeskatt trukket. Hjemland er lest fra landkoden i ISIN, som er en tilnærming.

## Kriterier bestemt på forhånd

Revidert 13. november 2026, etter to børsdager og før noe resultat kunne si noe. Bare
vurderingen er endret, ingen handelsregler.

Det opprinnelige kravet (t-verdi over 2 etter 120 børsdager) fanger bare en meravkastning
på rundt 40 % i året, og vil oftere slippe gjennom flaks enn en ekte fordel. En realistisk
fordel på 5–10 % i året tar mange år å bevise i en fremtidstest. Derfor:

1. **Beviset for en fordel kommer fra lang historikk** (forskningen og aksjescore sin
   tilbakeprøving 2018–2026), ikke fra denne testen.
2. **Fremtidstesten brukes til å avkrefte.** Etter minst 120 børsdager tas en modell ut hvis
   - t-verdien mot referansen er under −2 (klart dårligere etter kostnader), eller
   - den har en historisk forventning og t-verdien mot den er under −2 (fordelen er
     trolig borte), eller
   - største fall er mer enn dobbelt så stort som referansens.
3. **Ingen modell erklæres som vinner** før «år før forskjellen er sikker» er under
   tiden testen faktisk har gått.

Historiske forventninger mot lik vekt, fra aksjescore sin tilbakeprøving (topp 30 aksjer,
30 bp kostnad): Momentum +8,5 % i året (t = 2,1), aksjescore +8,3 % i året (t = 2,2).
Våre modeller holder 10 aksjer og har derfor større avvik fra referansen.

## Planlagte vurderingspunkter

- Etter 3 måneder: første gjennomgang. Ingen regelendringer, bare observasjon.
- Etter 6 måneder: vurder hvilke modeller som kobles til IBKR-papirkonto.

## 2026-11-12 – drift (ingen regelendringer)

- Første planlagte kjøring startet seks timer for sent og bokførte ingenting, fordi Yahoo
  leverer nyeste dagsrad uten sluttkurs. Kursene hentes nå direkte fra Yahoo, og
  sluttnoteringen brukes for nyeste dag. 11. november ble bokført i etterkant.
- Kjøretider: fire forsøk hver kveld (17:23–21:07 UTC) og to morgenen etter, fordi GitHub
  startet planlagte kjøringer 6–8 timer for sent de to første dagene.
- Varsel (sak i repoet) hvis en modell ikke er bokført på over tre dager, og hvis mer enn
  10 % av sluttkursene avviker fra aksjescore sine.
