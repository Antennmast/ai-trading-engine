# Endringslogg

Reglene for hver modell er låst i testperioden. Skal en regel endres, legges det inn som
en ny modell med eget navn og egen portefølje, slik at gamle og nye resultater ikke blandes.
Alt som endres i oppsettet noteres her med dato og begrunnelse.

## 2026-11-11 – start

- Ni modeller startet med 100 000 NOK hver på sluttkursene 10. november 2026.
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
- Niende modell lagt til før første handel: Momentum med brems. Den er lik Momentum, men
  har én brems per hjemmemarked (Oslo, Stockholm, København, Helsinki). Når et markeds
  indeks stenger under 200-dagerssnittet, selges aksjene derfra, og det kjøpes ikke nytt
  der før indeksen er 1 % over snittet igjen. Sammenlignes direkte med Momentum uten brems.

## Kriterier bestemt på forhånd

En modell regnes som bedre enn referansen først når alle tre er oppfylt:

1. minst 120 børsdager i testen
2. høyere avkastning enn referansen etter kostnader
3. t-verdi mot referansen over 2

Største fall skal ikke være mer enn dobbelt så stort som referansens. OSEBX vises som
målestokk for alternativet indeksfond.

## Planlagte vurderingspunkter

- Etter 3 måneder: første gjennomgang. Ingen regelendringer, bare observasjon.
- Etter 6 måneder: vurder hvilke modeller som kobles til IBKR-papirkonto.
