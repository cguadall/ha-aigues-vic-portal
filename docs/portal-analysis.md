# Anàlisi de l'oficina virtual d'Aigües Vic

[English](portal-analysis.en.md)

## Nova Oficina Virtual (Octubre 2026 - API REST JSON)

A finals d'octubre de 2026, Aigües de Vic va renovar completament la seva oficina virtual (`https://oficina.aiguesvic.com`), substituint l'aplicació Laravel clàssica per una aplicació SPA moderna que interactua amb una API REST JSON a `https://api-oficina.aiguesvic.com/`.

### Tecnologia i Autenticació JWT

- **Host de l'API**: `https://api-oficina.aiguesvic.com`
- **Autenticació**: basada en tokens JWT amb capçalera `Authorization: Bearer <access_token>`.
  - `POST /accounts/auth/login/`: rep `{"email": "...", "password": "..."}` i retorna `{"access": "<jwt>", "refresh": "<jwt>"}`.
  - `POST /accounts/refresh-token/`: renova el token d'accés enviant `{"refresh": "<jwt>"}`. Retorna un nou `access` token.
  - `POST /accounts/logout/`: invalida la sessió amb `{"refresh": "<jwt>"}`.
  - El portal no requereix cookies de sessió per a les consultes d'API.

### Endpoints de l'API

- **Contractes**: `GET /contracts/`
  Retorna la llista de contractes associats a l'usuari amb metadades clau:
  - `uuid`: identificador intern de l'API per a les consultes.
  - `contract_number`: número de contracte comercial (p. ex. `100000001`), que s'utilitza com a identificador públic d'entitats i d'estadístiques a llarg termini a Home Assistant per garantir compatibilitat 1:1.
  - `contract_status`: objecte amb el codi (`ACTIVE`, etc.).
  - `alias`, `address`, `cups`.
- **Dades tècniques i facturació**: `GET /contracts/billing-data/{uuid}/`
  Retorna el titular del contracte, el número de sèrie del comptador (`meter_serial_number`) i el calibre (`caliber`).
- **Consums diaris**: `GET /consumptions/daily-consumptions/?contract_uuid={uuid}&initial_date=YYYY-MM-DD&end_date=YYYY-MM-DD`
  Retorna una llista de lectures i consums:
  - `timestamp`: data en format `YYYY-MM-DDT23:00:00`.
  - `consumption`: consum del dia en litres.
  - `reading`: lectura de l'índex del comptador en m³ (pot incloure decimals, p. ex. `803.76`).
- **Consums horaris**: `GET /consumptions/hourly-consumptions/?contract_uuid={uuid}&initial_date=YYYY-MM-DD&end_date=YYYY-MM-DD`
  Retorna el detall horari de consum:
  - `timestamp`: data i hora local en format ISO (`YYYY-MM-DDTHH:MM:SS`).
  - `consumption`: litres consumits durant l'hora.
  - Suporta dies de 25 hores pel canvi d'horari d'hivern d'octubre.
- **Consums facturats**: `GET /consumptions/billed-consumptions/?contract_uuid={uuid}`
  Retorna l'històric de factures emeses:
  - `initial_date` / `end_date`: període de facturació.
  - `consumption`: consum total facturat en m³.
  - `reading`: lectura en m³.

---

## Oficina Virtual Clàssica (Arxiu històric)

Anàlisi feta a partir de set captures HAR del navegador (octubre de 2026),
anonimitzades amb [`scripts/anonymize_har.py`](../scripts/anonymize_har.py).
Els valors d'exemple d'aquest document són sintètics.

## Tecnologia

- `https://oficina.aiguesvic.com`, Apache 2.4 (Ubuntu) i Laravel (cookies
  `laravel_session` i `XSRF-TOKEN`).
- HTML generat al servidor amb jQuery, DataTables, Chart.js i daterangepicker.
- No hi ha API JSON: les dades són a taules HTML i a scripts en línia. L'únic
  JSON és el fitxer d'idioma `/datatables/ca.json`.
- Respostes amb `cache-control: no-cache, private`.
- El HAR no conté capçaleres `Set-Cookie` i els temps són 0 ms, una limitació
  de l'exportació del navegador.

## Flux

```text
GET  /login                         formulari amb _token i entorn=avsa
POST /login                         302 → /  → 302 → /abonat
POST /login (credencials errònies)   302 → /login → 200 amb formulari i error
GET  /abonat                        tauler del contracte actiu
GET  /abonat/canviar/{contracte}    302 → /abonat; canvia el contracte de la sessió
GET  /abonat/consums                lectures diàries i consums facturats
POST /abonat/consums                el mateix amb un interval de dates
GET  /abonat/consums/horaris        consum horari
POST /abonat/consums/horaris        el mateix amb un interval de dates
GET  /logout                        302 → /  → 302 → /login
```

### Autenticació

- `POST /login` amb `application/x-www-form-urlencoded`: `_token` (CSRF del
  formulari), `entorn=avsa`, `email`, `password` i, opcionalment, `remember`.
- El `_token` del login es troba a `form#login input[name=_token]`; la pàgina
  de login no té `meta[name=csrf-token]`. Els POST de dades també envien el
  token inclòs a la pàgina autenticada.
- **Risc del portal:** si es marca «Recorda'm», la funció JavaScript
  `recordem()` desa el correu i la contrasenya **en clar** a les cookies
  `username` i `password` durant 365 dies. La integració no ho ha de fer servir
  ni imitar. Es recomana no marcar aquesta opció al navegador.
- La captura del 16/10/2026 confirma el login fallit: `POST /login` retorna
  `302` cap a `/login`; el `GET` següent retorna `200`, manté `form#login`
  amb els camps ocults `_token` i `entorn`, i mostra un `span.help-block` amb
  «Dades d'accés incorrectes.». Per tant, un HTTP 200 final no implica èxit:
  el client ha de detectar que continua al formulari de login.
- La mateixa captura confirma el login correcte després del fallit:
  `POST /login → 302 / → 302 /abonat → 200`, sense formulari de login.
- El HAR del 16/10/2026 de les 16:29, d'un contenidor Firefox, confirma
  l'accés inicial `GET / → 302 /login → 200`, abans del primer POST.
  El formulari conté `_token` i `entorn=avsa`, sense meta CSRF. La primera
  petició ja enviava `laravel_session` i `XSRF-TOKEN` d'una visita anterior.
- El formulari crida `recordem()` a `onsubmit`. Sense «Recorda'm» marcat, la
  funció «reinicia» les cookies `username`, `password` i `remember`, però en
  realitat les crea amb el text literal `null`. Per això apareixen a partir
  del POST amb aquest valor; no contenen credencials.
- Aquesta captura confirma també el login correcte i el logout:
  `GET /logout → 302 / → 302 /login → 200` amb el formulari, igual que el
  HAR del 15/10/2026 de les 23:40.
- La caducitat de la sessió i els límits d'intents continuen sense confirmar.

### Consentiment de cookies

- La pàgina de login carrega Cookiebot des de `consent.cookiebot.com/uc.js`,
  amb `data-blockingmode="auto"`. El navegador descarrega la configuració de
  `consentcdn.cookiebot.com` i, en aprovar, registra el consentiment amb
  `GET consent.cookiebot.com/logconsent.ashx`. Tot passa entre el navegador i
  Cookiebot: el portal no rep cap petició de consentiment ni envia cap
  `Set-Cookie` per a `CookieConsent`.
- Després de l'aprovació, les peticions al portal inclouen la cookie
  `CookieConsent`, creada per JavaScript; el POST de login manté només
  `_token`, `entorn`, `email` i `password`, sense camp de consentiment.
- Després del logout, la pàgina de login torna a carregar Cookiebot, però no
  hi ha cap nou `logconsent.ashx`: el consentiment es conserva.
- No s'observa cap endpoint de consentiment propi del portal. L'anonimitzador
  descarta les peticions a tercers, de manera que les còpies anonimitzades no
  permeten reconstruir els intercanvis amb Cookiebot.
- El client HTTP no executa JavaScript, no carrega Cookiebot i no crea
  `CookieConsent` ni les cookies de «Recorda'm». Les proves sintètiques
  cobreixen aquest comportament des d'una sessió buida. La captura té
  consentiment abans del POST: no demostra que el servidor accepti un login
  sense aquesta cookie; cal confirmar-ho amb la integració real.

### Contracte actiu

- El contracte actiu és estat de la sessió del servidor. Totes les pàgines de
  dades retornen informació del contracte seleccionat amb
  `GET /abonat/canviar/{contracte}`.
- Per llegir tots els contractes cal seqüenciar `canviar` + consultes per a
  cada contracte, sense peticions concurrents dins la mateixa sessió.
- Totes les pàgines contenen `form#form_contrato` amb `select#contrato_activo`:
  - `option[value]`: número de contracte (7 xifres amb zeros inicials; s'ha de
    tractar com a text).
  - Text: `número` o `número (àlies)`. L'àlies és opcional.
  - `selected`: contracte actiu.
- Fitxa del contracte, a partir de `label.control-label-contracte` i el `div`
  següent:
  - «Comptador»: `15 mm - SÈRIE` (calibre i número de sèrie).
  - «Adreça subministrament».
  - «Titular del contracte».
- Un contracte que no està d'alta mostra: «Aquest contracte no està donat
  d'alta actualment i no pot gestionar aquesta funcionalitat.»
- Després del login, el portal selecciona el primer contracte del selector,
  encara que estigui de baixa (captura del 20/10/2026).

### Llista de contractes `/abonat/contractes`

Confirmada amb el HAR del 20/10/2026. La pàgina no té selector de contracte ni
token CSRF, i no depèn del contracte seleccionat.

`table#table-registros`, una fila per contracte, renderitzada al servidor (sense
paginació per AJAX):

| Columna | Contingut |
| --- | --- |
| Núm. Contracte | Número de contracte |
| Adreça subministrament | Adreça |
| Alias | Àlies, o buit |
| Estat | `td[data-order]` i `span.label`: `ALTA` (`label-success`) o `BAIXA` (`label-danger`) |
| (sense títol) | Enllaç «Editar» a `/abonat/contracte/{contracte}` |

L'estat coincideix amb l'avís de contracte inactiu: els contractes `BAIXA`
mostren l'avís a `/abonat`, i el contracte `ALTA` no. La integració usa
aquesta taula per consultar només els contractes `ALTA`, sense fer
`canviar` als de baixa.

## Pàgines

### Tauler `/abonat`

- Gràfic `canvas#chartConsumos`: consums facturats per període, amb etiquetes
  `dd/mm/aa` i valors en m³ a l'script en línia.
- Bloc de factures: import de la darrera factura (`NN,NN €`), data d'emissió,
  comparació amb l'anterior, enllaç al PDF i data aproximada de la propera
  factura.
- Menú: `atencio`, `avisos`, `consums`, `consums/horaris`, `contractes`,
  `dades`, `factures`, `lectura`, `pagaments`, `tramits` (sota `/abonat/`),
  `/usuari`, `/usuari/avisos/{uuid}`, `/idioma/es` i `/avsaxat`.

### Consums `/abonat/consums`

Formulari `POST` amb `_token`, `fecha_inicio` i `fecha_fin` (`AAAA-MM-DD`).
El selector de dates permet com a màxim 1 any per consulta
(`maxSpan: {years: 1}`), des de l'1/1/2016 fins avui. Per defecte mostra uns
30 dies.

`table#table-lecturas`, «Darrers consums diaris»:

| Columna | Text | `data-order` |
| --- | --- | --- |
| Data de lectura | `16/09/2026` | `2026-09-16` |
| Comptador | sèrie | — |
| Període (dies) | `1` | — |
| Lectura | `123` (m³ enters) | **consum en m³, no la lectura** |
| Consum (m3) | `0,25` | `0.250` |
| Consum (l/dia) | `250` | `250` |

- El `data-order` de «Lectura» és un error del portal: conté el consum.
- La lectura acumulada només té precisió de m³. El consum diari té precisió de
  litre (`0.250` m³ = 250 l).
- Una consulta de 6 mesos va retornar 183 files consecutives, incloent el dia
  actual (probablement parcial).
- Hi havia dies amb consum 0. Segons el portal, quan no arriba la lectura el
  consum del dia és 0 i s'acumula al dia següent.
- La suma dels consums diaris quadra amb la diferència de lectures dins la
  precisió entera.

`table#table-consumos`, «Llistat de consums facturats»:

| Columna | Exemple |
| --- | --- |
| Data de lectura | `01/10/26`, `data-order` ISO |
| Període (dies) | `91` (buit a la lectura inicial) |
| Lectura | m³ enters |
| Consum (m3) | m³ enters |
| Consum (l/dia) | mitjana |
| Incidència | p. ex. «Lectura inicial» |

Els períodes facturats són aproximadament trimestrals (90–92 dies). Els
gràfics `chartLecturas` i `chartConsumos` repeteixen les dades de les taules.

#### Consultes històriques del 20/10/2026, captura de les 23:04

Tots aquests POST retornen HTTP 200, amb les taules HTML completes, i
l'analitzador actual pot llegir-ne totes les files:

| Interval enviat | Files diàries | Primera data | Darrera data |
| --- | --- | --- | --- |
| 20/04/2026–20/10/2026 | 181 | 21/04/2026 | 18/10/2026 |
| 01/01/2025–31/12/2025 | 283 | 02/01/2025 | 01/12/2025 |
| 01/01/2024–31/12/2024 | 357 | 02/01/2024 | 31/12/2024 |
| 01/01/2022–31/12/2022 | 361 | 02/01/2022 | 31/12/2022 |

- La consulta de 2022 es repeteix amb el mateix resultat. No hi ha cap
  consulta de 2017 en aquesta captura: es confirma disponibilitat des de
  gener de 2022 per a aquest contracte, però no el límit més antic.
- En totes aquestes consultes s'exclou la data inicial. El client ja demana
  un dia de marge a cada costat i filtra al rang sol·licitat.
- Un interval anual és acceptat, però això no implica una fila per dia.
  El 2024 hi ha un buit de 8 dies, el 2022 un de 3 i el 2025 diversos buits,
  a més de no haver-hi files després de l'1 de desembre.
- La columna «Període (dies)» no sempre és `1`: arriba a `16` el 2025.
  Hi ha lectures que cobreixen diversos dies. No s'han d'omplir les dates
  absents amb zeros ni repartir el volum sense més informació; la
  integració conserva el consum a la data indicada pel portal.
- El 20/10 les dades diàries més recents són del 18/10. La disponibilitat
  pot anar endarrerida; una resposta amb menys dies no demostra un límit de
  files.
- Les quatre consultes mantenen les mateixes sis files de consum facturat,
  amb dates del 2025 i 2026: aquest segon llistat no queda filtrat per
  l'interval diari.

### Consum horari `/abonat/consums/horaris`

El formulari és el mateix que el de consums; per defecte mostra 2 dies.

`table#table-lecturas`:

| Columna | Text | `data-order` |
| --- | --- | --- |
| Comptador | sèrie | — |
| Data de lectura | `2026-10-14 13:00:00` | igual |
| Consum (Litres) | `1,50` | `1.50` |

- La vista per defecte tenia 97 hores consecutives sense forats.
- La marca horària és l'**inici** de l'hora: la suma de 00:00 a 23:00 coincideix
  amb el consum diari del mateix dia (diferència inferior a 0,5 l). La
  convenció de final d'hora no quadra (diferències de diversos litres).
- Hora local, presumiblement `Europe/Madrid`. Cal verificar els canvis d'hora.
- Avís del portal: si el mateix valor es repeteix en hores consecutives, és un
  valor estimat.
- El gràfic `chartHourly` repeteix les dades en un script en línia, després
  de la taula.

#### Exportació CSV

- El botó «Descàrrega fitxer CSV» és l'extensió `csvHtml5` de DataTables
  Buttons 1.7.0 (`dom: 'Bfrtip'`, `title: 'Consumshoraris'`, UTF-8 amb BOM).
  Genera el fitxer **al navegador** a partir de les files de la taula HTML ja
  carregada; no fa cap petició al servidor.
- Cap de les set captures conté una petició d'exportació ni una resposta
  `text/csv` o amb `Content-Disposition: attachment`. No s'ha observat cap
  URL de CSV. El codi de l'extensió capturat al HAR usa l'exportació de dades
  de DataTables i crea un `Blob` de tipus `text/csv` al navegador.
- La pàgina de consums diaris no té aquest botó.

Fitxers exportats aportats el 20/10/2026:

| Fitxer local | Bytes | Files de dades | Primera hora | Darrera hora |
| --- | --- | --- | --- | --- |
| `Consumshoraris.csv` | 4.337 | 97 | 11/10/2026 02:00 | 15/10/2026 02:00 |
| `Consumshoraris(1).csv` | 192.607 | 4.345 | 20/04/2026 02:00 | 18/10/2026 02:00 |

- Tots dos són UTF-8 amb BOM, separats per comes, amb els camps entre
  cometes i les capçaleres `Comptador`, `Data de lectura` i `Consum (Litres)`.
  Les dates són ISO i els consums usen coma decimal; cal un lector CSV real,
  no separar cada línia amb `split(',')`.
- Les files estan ordenades de més recent a més antiga. No hi ha hores
  duplicades ni buits en cap dels dos fitxers. S'exporten totes les files de
  l'interval, no només les 25 visibles a la pàgina de DataTables.
- Les 97 files del CSV petit coincideixen amb la taula horària del HAR del
  15/10 de les 23:40. Les 2.218 files llegibles del POST horari llarg del
  20/10 coincideixen amb un subconjunt del CSV gran: mateixes dates, valors
  numèrics i comptador, tot i l'ordre invers.
- El CSV gran confirma dades posteriors al juliol i un interval de sis
  mesos carregat al navegador. Això apunta a un problema de captura o
  exportació del HAR, no a un límit de dades del servidor.

- Conclusió: per a un client HTTP sense JavaScript, el CSV no és una font
  remota alternativa amb el mecanisme observat. Automatitzar aquest botó
  implicaria descarregar el mateix HTML i, a més, executar JavaScript.
  Les dades contrastades són les mateixes que les de la taula.
  Es manté el parseig de `table#table-lecturas`, que usa `data-order`
  (data ISO i punt decimal) i les capçaleres de columna. Els CSV són útils
  per contrastar dades i per a una eventual importació manual, no com a
  substitut de la consulta automàtica actual.
- El script de `chartHourly` duplica les dades en arrays JavaScript. No és
  millor font: és més fràgil, no inclou el comptador i arriba al final del
  document, de manera que es perd primer si la resposta queda tallada.
- Mida: cada fila horària ocupa uns 440 caràcters d'HTML. Un mes són
  uns 0,3 MB, i un any unes 8.760 files, prop de 4 MB més el gràfic.
  El CSV ocupa menys al disc, però no redueix el trànsit HTTP del botó:
  l'HTML ja s'ha descarregat i la resposta usa gzip. Trams més curts
  (p. ex. 31 dies) són una possible optimització de mida, no un límit
  demostrat pel HAR; no s'ha canviat el client en aquesta anàlisi.
- Els CSV originals contenen el número de comptador. Continuen a `local/`,
  fora de Git; l'anonimitzador de HAR no processa aquests fitxers.

#### Consulta horària llarga del 20/10/2026

- El POST envia `fecha_inicio=2026-04-20` i `fecha_fin=2026-10-20`,
  amb el mateix formulari que ja usa el client, i retorna HTTP 200.
- El cos desat al HAR original està incomplet: acaba amb **974.140 caràcters
  nuls** i no conté el tancament de la taula ni de l'HTML. La còpia
  anonimitzada conserva aquest defecte; no l'ha causat l'anonimitzador.
- Es poden llegir 2.218 registres consecutius, del 20/04 a les 02:00 al
  21/07 a les 11:00, però no representen necessàriament tota la resposta.
  No demostren un límit de files ni que l'històric acabi al juliol.
- El CSV de sis mesos conté 4.345 hores fins al 18/10 i coincideix amb totes
  les files llegibles d'aquest POST. Per tant, el navegador va poder
  exportar més dades de les que conserva el HAR. La causa exacta dels nuls
  no està confirmada, però ja no cal repetir per demostrar dades posteriors
  al juliol. Una captura d'un mes, amb el cos sense nuls i la taula tancada,
  encara seria útil per investigar el defecte del HAR.
- El GET horari per defecte sí que és complet: té 49 registres consecutius,
  del 16/10 a les 02:00 al 18/10 a les 02:00.

### Lectura `/abonat/lectura`

És un formulari per comunicar lectures manuals (`POST` amb `_token` i
`lectura_actual`). Mostra la lectura anterior i l'actual. **Queda fora de
l'abast:** la integració no hi ha d'enviar mai res.

## Formats

- El text visible usa coma decimal i dates `dd/mm/aaaa` o `dd/mm/aa`.
- Els atributs `data-order` usen punt decimal i dates ISO; són la font
  preferida, excepte a la columna «Lectura» de la taula diària.
- Unitats: m³ (lectures i consums diaris o facturats) i litres (horari).

## Implicacions per a la integració

- Client HTTP amb sessió pròpia (`aiohttp.ClientSession` amb galetes
  aïllades), un sol fil de peticions per compte i reautenticació quan
  aparegui el formulari de login o una redirecció a `/login`.
- Enumerar contractes i el seu estat des de `/abonat/contractes` i consultar
  només els que estan d'`ALTA`. Si la taula no hi és, usar
  `select#contrato_activo` i l'avís de contracte inactiu com a alternativa.
- Sensors per contracte:
  - Lectura acumulada (m³, `total_increasing`), amb data de lectura.
  - Consum del darrer dia complet.
  - Consum del darrer període facturat.
- Estadístiques externes amb granularitat horària quan n'hi hagi, o diària
  si no, descartant sempre el dia o l'hora en curs. Cal reimportar uns quants
  dies enrere perquè les lectures endarrerides s'acumulen al dia següent.
- Importació històrica en trams d'un any com a màxim, de manera esglaonada i
  sense superar l'1/1/2016.
- Analitzadors basats en selectors i capçaleres de columna, amb fixtures
  sintètiques; no dependre de l'ordre del menú ni del text decoratiu.

## Preguntes obertes

- Caducitat de la sessió.
- Si existeixen CAPTCHA, límits de peticions o bloquejos.
- Disponibilitat horària en tots els comptadors (comptadors no telemàtics).
- Comportament en un canvi de comptador, quan hi ha dues sèries.
- Límit més antic de l'històric diari (confirmat des de gener de 2022 per al
  contracte capturat) i causa del cos horari incomplet al HAR, tot i el CSV
  complet de sis mesos.
- Fus horari i canvis d'hora (dia de 23 o 25 hores).

Mostres addicionals útils (sempre anonimitzades): una sessió caducada,
`POST /abonat/consums/horaris` amb un mes i el cos complet, una consulta diària
anterior a 2022 i un contracte sense comptador telemàtic.
