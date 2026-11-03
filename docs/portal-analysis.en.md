# Aigües Vic virtual office analysis

[Català](portal-analysis.md)

## New Virtual Office (October 2026 - JSON REST API)

In late October 2026, Aigües de Vic completely renewed its virtual office (`https://oficina.aiguesvic.com`), replacing the legacy server-rendered Laravel web app with a modern SPA interacting with a JSON REST API at `https://api-oficina.aiguesvic.com/`.

### Technology and JWT Authentication

- **API Host**: `https://api-oficina.aiguesvic.com`
- **Authentication**: JWT Bearer token authentication via `Authorization: Bearer <access_token>`.
  - `POST /accounts/auth/login/`: accepts `{"email": "...", "password": "..."}` and returns `{"access": "<jwt>", "refresh": "<jwt>"}`.
  - `POST /accounts/refresh-token/`: refreshes the access token using `{"refresh": "<jwt>"}`. Returns a new access token.
  - `POST /accounts/logout/`: revokes the session with `{"refresh": "<jwt>"}`.
  - No session cookies are required for REST API calls.

### API Endpoints

- **Contracts**: `GET /contracts/`
  Returns the list of user contracts with key metadata:
  - `uuid`: internal API identifier used in query parameters.
  - `contract_number`: public commercial contract number (e.g. `100000001`), preserved as primary entity and long-term statistics identifier in Home Assistant to maintain 1:1 backwards compatibility.
  - `contract_status`: status object with code (`ACTIVE`, etc.).
  - `alias`, `address`, `cups`.
- **Billing & Technical Data**: `GET /contracts/billing-data/{uuid}/`
  Returns the contract holder, meter serial number (`meter_serial_number`), and meter caliber (`caliber`).
- **Daily Consumptions**: `GET /consumptions/daily-consumptions/?contract_uuid={uuid}&initial_date=YYYY-MM-DD&end_date=YYYY-MM-DD`
  Returns daily readings and volume:
  - `timestamp`: formatted as `YYYY-MM-DDT23:00:00`.
  - `consumption`: day volume in litres.
  - `reading`: meter index reading in m³ (supports decimal values, e.g. `803.76`).
- **Hourly Consumptions**: `GET /consumptions/hourly-consumptions/?contract_uuid={uuid}&initial_date=YYYY-MM-DD&end_date=YYYY-MM-DD`
  Returns hourly consumption points:
  - `timestamp`: local naive ISO timestamp (`YYYY-MM-DDTHH:MM:SS`).
  - `consumption`: hourly volume in litres.
  - Handles the 25-hour October DST fallback day.
- **Billed Consumptions**: `GET /consumptions/billed-consumptions/?contract_uuid={uuid}`
  Returns invoice history:
  - `initial_date` / `end_date`: billing window.
  - `consumption`: billed volume in m³.
  - `reading`: meter reading in m³.

---

## Legacy Virtual Office (Historical Archive)

Based on seven browser HAR captures (October 2026), anonymized with
[`scripts/anonymize_har.py`](../scripts/anonymize_har.py). Example values in
this document are synthetic.

## Technology

- `https://oficina.aiguesvic.com`, Apache 2.4 (Ubuntu) and Laravel
  (`laravel_session` and `XSRF-TOKEN` cookies).
- Server-rendered HTML with jQuery, DataTables, Chart.js and daterangepicker.
- There is no JSON API: data lives in HTML tables and inline scripts. The only
  JSON is the `/datatables/ca.json` language file.
- Responses use `cache-control: no-cache, private`.
- The HAR has no `Set-Cookie` headers and 0 ms timings, a limitation of the
  browser export.

## Flow

```text
GET  /login                         form with _token and entorn=avsa
POST /login                         302 → /  → 302 → /abonat
POST /login (invalid credentials)   302 → /login → 200 with form and error
GET  /abonat                        dashboard for the active contract
GET  /abonat/canviar/{contract}     302 → /abonat; switches the session contract
GET  /abonat/consums                daily readings and billed consumption
POST /abonat/consums                same, for a date range
GET  /abonat/consums/horaris        hourly consumption
POST /abonat/consums/horaris        same, for a date range
GET  /logout                        302 → /  → 302 → /login
```

### Authentication

- `POST /login` as `application/x-www-form-urlencoded`: `_token` (form CSRF),
  `entorn=avsa`, `email`, `password` and an optional `remember`.
- The login `_token` is in `form#login input[name=_token]`; the login page
  has no `meta[name=csrf-token]`. Data POSTs also send the token embedded in
  the authenticated page.
- **Portal risk:** when "Recorda'm" is ticked, the JavaScript `recordem()`
  stores the email and password **in clear text** in the `username` and
  `password` cookies for 365 days. The integration must not use or mimic it.
  Avoid ticking that option in the browser.
- The 16 October 2026 capture confirms failed login: `POST /login` returns
  `302` to `/login`; the next `GET` returns `200`, keeps `form#login` with
  the hidden `_token` and `entorn` fields, and shows a `span.help-block`
  containing "Dades d'accés incorrectes.". A final HTTP 200 therefore does
  not mean success: the client must detect that it is still on the login form.
- The same capture confirms successful login after failure:
  `POST /login → 302 / → 302 /abonat → 200`, without a login form.
- The 16 October 2026 HAR from 16:29, from a Firefox container, confirms
  the initial `GET / → 302 /login → 200`, before the first POST.
  The form contains `_token` and `entorn=avsa`, without a CSRF meta tag. The first
  request already sent `laravel_session` and `XSRF-TOKEN` from an earlier
  visit.
- The form calls `recordem()` on `onsubmit`. With "Recorda'm" unticked, the
  function "resets" the `username`, `password` and `remember` cookies, but
  actually creates them with the literal text `null`. That is why they appear
  from the POST onwards with that value; they contain no credentials.
- This capture also confirms successful login and logout:
  `GET /logout → 302 / → 302 /login → 200` with the form, matching the
  15 October 2026 HAR from 23:40.
- Session expiry and attempt limits remain unconfirmed.

### Cookie consent

- The login page loads Cookiebot from `consent.cookiebot.com/uc.js`, with
  `data-blockingmode="auto"`. The browser downloads the configuration from
  `consentcdn.cookiebot.com` and, on approval, records consent with
  `GET consent.cookiebot.com/logconsent.ashx`. Everything happens between the
  browser and Cookiebot: the portal receives no consent request and sends no
  `Set-Cookie` for `CookieConsent`.
- After approval, portal requests include the `CookieConsent` cookie, created
  by JavaScript; the login POST still contains only `_token`, `entorn`,
  `email` and `password`, without a consent field.
- After logout, the login page loads Cookiebot again, but there is no new
  `logconsent.ashx`: consent is kept.
- No portal-specific consent endpoint is observed. The anonymizer drops
  third-party requests, so anonymized copies cannot reconstruct the
  exchanges with Cookiebot.
- The HTTP client does not execute JavaScript, load Cookiebot or create
  `CookieConsent` or "Remember me" cookies. Synthetic tests cover this
  behaviour from an empty session. The capture has consent before the POST:
  it does not prove that the server accepts login without this cookie;
  this must be confirmed with the real integration.

### Active contract

- The active contract is server-side session state. All data pages return the
  contract selected with `GET /abonat/canviar/{contract}`.
- Reading every contract requires a sequential switch + fetch per contract,
  with no concurrent requests within one session.
- Every page has `form#form_contrato` with `select#contrato_activo`:
  - `option[value]`: contract number (7 digits with leading zeros; treat it as
    text).
  - Text: `number` or `number (alias)`. The alias is optional.
  - `selected`: the active contract.
- Contract card, from `label.control-label-contracte` and the following `div`:
  - "Comptador": `15 mm - SERIAL` (calibre and serial number).
  - "Adreça subministrament" (supply address).
  - "Titular del contracte" (holder).
- A contract that is not active shows: "Aquest contracte no està donat d'alta
  actualment i no pot gestionar aquesta funcionalitat."
- After login, the portal selects the first contract in the selector, even if
  it is inactive (20 October 2026 capture).

### Contract list `/abonat/contractes`

Confirmed with the 20 October 2026 HAR. The page has no contract selector or
CSRF token, and does not depend on the selected contract.

`table#table-registros`, one row per contract, rendered server-side (no AJAX
pagination):

| Column | Content |
| --- | --- |
| Núm. Contracte | Contract number |
| Adreça subministrament | Address |
| Alias | Alias, or empty |
| Estat | `td[data-order]` and `span.label`: `ALTA` (`label-success`) or `BAIXA` (`label-danger`) |
| (untitled) | "Editar" link to `/abonat/contracte/{contract}` |

The state matches the inactive-contract warning: `BAIXA` contracts show the
warning on `/abonat`, and the `ALTA` contract does not. The integration uses
this table to query only `ALTA` contracts, without switching to inactive ones.

## Pages

### Dashboard `/abonat`

- `canvas#chartConsumos`: billed consumption per period, with `dd/mm/yy`
  labels and m³ values in the inline script.
- Invoice block: latest invoice amount (`NN,NN €`), issue date, comparison
  with the previous invoice, PDF link and approximate next invoice date.
- Menu: `atencio`, `avisos`, `consums`, `consums/horaris`, `contractes`,
  `dades`, `factures`, `lectura`, `pagaments`, `tramits` (under `/abonat/`),
  `/usuari`, `/usuari/avisos/{uuid}`, `/idioma/es` and `/avsaxat`.

### Consumption `/abonat/consums`

`POST` form with `_token`, `fecha_inicio` and `fecha_fin` (`YYYY-MM-DD`). The
date picker allows at most 1 year per query (`maxSpan: {years: 1}`), from
2016-01-01 to today. The default view shows about 30 days.

`table#table-lecturas`, "Darrers consums diaris" (latest daily consumption):

| Column | Text | `data-order` |
| --- | --- | --- |
| Data de lectura | `16/09/2026` | `2026-09-16` |
| Comptador | serial | — |
| Període (dies) | `1` | — |
| Lectura | `123` (whole m³) | **consumption in m³, not the reading** |
| Consum (m3) | `0,25` | `0.250` |
| Consum (l/dia) | `250` | `250` |

- The "Lectura" `data-order` is a portal bug: it holds the consumption.
- The cumulative reading only has m³ precision. Daily consumption has litre
  precision (`0.250` m³ = 250 l).
- A 6-month query returned 183 consecutive rows, including today (probably
  partial).
- Some days had zero consumption. According to the portal, when a reading is
  missing that day shows 0 and the volume is added to the next day.
- The sum of daily consumption matches the reading delta within integer
  precision.

`table#table-consumos`, "Llistat de consums facturats" (billed consumption):

| Column | Example |
| --- | --- |
| Data de lectura | `01/10/26`, ISO `data-order` |
| Període (dies) | `91` (empty for the initial reading) |
| Lectura | whole m³ |
| Consum (m3) | whole m³ |
| Consum (l/dia) | average |
| Incidència | e.g. "Lectura inicial" |

Billing periods are roughly quarterly (90–92 days). Charts `chartLecturas`
and `chartConsumos` repeat the table data.

#### Historical queries on 20 October 2026, 23:04 capture

All these POSTs return HTTP 200 with complete HTML tables, and the current
parser can read every row:

| Submitted range | Daily rows | First date | Last date |
| --- | --- | --- | --- |
| 2026-04-20–2026-10-20 | 181 | 2026-04-21 | 2026-10-18 |
| 2025-01-01–2025-12-31 | 283 | 2025-01-02 | 2025-12-01 |
| 2024-01-01–2024-12-31 | 357 | 2024-01-02 | 2024-12-31 |
| 2022-01-01–2022-12-31 | 361 | 2022-01-02 | 2022-12-31 |

- The 2022 query is repeated with the same result. There is no 2017 query
  in this capture: availability from January 2022 is confirmed for this
  contract, but not the earliest history boundary.
- All these queries exclude the start date. The client already requests a
  one-day margin on either side and filters to the requested range.
- A yearly range is accepted, but does not imply one row per day. There is
  an 8-day gap in 2024, a 3-day gap in 2022 and several gaps in 2025, as
  well as no rows after 1 December.
- The "Període (dies)" column is not always `1`: it reaches `16` in 2025.
  Some readings cover several days. Missing dates must not be filled with
  zeros or have consumption redistributed without more information; the
  integration keeps consumption at the date reported by the portal.
- On 20 October the latest daily data is from 18 October. Availability may
  lag; fewer returned days do not prove a row limit.
- The four queries keep the same six billed-consumption rows, dated in
  2025 and 2026: this second list is not filtered by the daily range.

### Hourly consumption `/abonat/consums/horaris`

Same form as the consumption page; the default view covers 2 days.

`table#table-lecturas`:

| Column | Text | `data-order` |
| --- | --- | --- |
| Comptador | serial | — |
| Data de lectura | `2026-10-14 13:00:00` | same |
| Consum (Litres) | `1,50` | `1.50` |

- The default view had 97 consecutive hours, with no gaps.
- Timestamps mark the **start** of the hour: the sum from 00:00 to 23:00
  matches that day's daily consumption (difference below 0.5 l). The
  end-of-hour convention does not match (differences of several litres).
- Presumably local `Europe/Madrid` time. DST transitions need verification.
- Portal notice: the same value repeated in consecutive hours means an
  estimated value.
- `chartHourly` repeats the data in an inline script after the table.

#### CSV export

- The "Descàrrega fitxer CSV" button is the DataTables Buttons 1.7.0
  `csvHtml5` extension (`dom: 'Bfrtip'`, `title: 'Consumshoraris'`, UTF-8
  with BOM). It builds the file **in the browser** from the rows of the
  already loaded HTML table; it makes no server request.
- None of the seven captures contains an export request or a `text/csv` or
  `Content-Disposition: attachment` response. No CSV URL has been observed.
  The extension code captured in the HAR uses DataTables data export and
  creates a `text/csv` `Blob` in the browser.
- The daily consumption page has no such button.

Exported files supplied on 20 October 2026:

| Local file | Bytes | Data rows | First hour | Last hour |
| --- | --- | --- | --- | --- |
| `Consumshoraris.csv` | 4,337 | 97 | 2026-10-11 02:00 | 2026-10-15 02:00 |
| `Consumshoraris(1).csv` | 192,607 | 4,345 | 2026-04-20 02:00 | 2026-10-18 02:00 |

- Both are UTF-8 with BOM, comma-separated, with quoted fields and the
  headers `Comptador`, `Data de lectura` and `Consum (Litres)`. Dates are
  ISO and consumption uses a decimal comma; a proper CSV reader is needed,
  rather than splitting each line with `split(',')`.
- Rows are ordered newest first. Neither file has duplicate hours or gaps.
  All rows in the range are exported, not just the 25 displayed on the
  current DataTables page.
- The small CSV's 97 rows match the hourly table in the 15 October 23:40
  HAR. The 2,218 readable rows in the long hourly POST from 20 October match
  a subset of the large CSV: same dates, numeric values and meter, despite
  the reversed order.
- The large CSV confirms data beyond July and a six-month range loaded in
  the browser. This points to a HAR capture or export issue, rather than a
  server data limit.

- Conclusion: for an HTTP client without JavaScript, CSV is not an
  alternative remote source through the observed mechanism. Automating this
  button would mean downloading the same HTML and also running JavaScript.
  The compared data is the same as the table's.
  Parsing `table#table-lecturas` stays, using `data-order` (ISO date and
  decimal point) and the column headers. CSV files are useful for checking
  data and for a possible manual import, not as a replacement for the
  current automatic queries.
- The `chartHourly` script duplicates the data in JavaScript arrays. It is
  not a better source: it is more fragile, lacks the meter and comes at the
  end of the document, so it is lost first if a response is truncated.
- Size: each hourly row is about 440 HTML characters. One month is about
  0.3 MB, and one year about 8,760 rows, close to 4 MB plus the chart.
  CSV is smaller on disk, but does not reduce the button's HTTP traffic:
  HTML has already been downloaded and the response uses gzip. Shorter
  chunks (e.g. 31 days) are a possible size optimization, not a limit
  demonstrated by the HAR; the client was not changed in this analysis.
- Original CSV files contain the meter number. They remain in `local/`,
  outside Git; the HAR anonymizer does not process these files.

#### Long hourly query on 20 October 2026

- The POST sends `fecha_inicio=2026-04-20` and `fecha_fin=2026-10-20`,
  using the same form as the client, and returns HTTP 200.
- The body saved in the original HAR is incomplete: it ends with **974,140
  NUL characters** and contains neither the closing table tag nor the
  closing HTML tag. The anonymized copy preserves this defect; the
  anonymizer did not cause it.
- There are 2,218 readable consecutive records, from 20 April at 02:00 to
  21 July at 11:00, but they do not necessarily represent the entire
  response. They prove neither a row limit nor that history ends in July.
- The six-month CSV contains 4,345 hours through 18 October and matches all
  readable rows from this POST. The browser could therefore export more
  data than the HAR preserves. The exact cause of the NULs is unconfirmed,
  but a repeat is no longer needed to demonstrate data beyond July. A
  one-month capture with no NULs and a closed table would still help
  investigate the HAR defect.
- The default hourly GET is complete: it has 49 consecutive records, from
  16 October at 02:00 to 18 October at 02:00.

### Meter reading `/abonat/lectura`

A form for submitting manual readings (`POST` with `_token` and
`lectura_actual`). It shows the previous and current readings. **Out of
scope:** the integration must never submit it.

## Formats

- Visible text uses a decimal comma and `dd/mm/yyyy` or `dd/mm/yy` dates.
- `data-order` attributes use a decimal point and ISO dates; prefer them,
  except for the daily table "Lectura" column.
- Units: m³ (readings, daily and billed consumption) and litres (hourly).

## Integration implications

- HTTP client with its own session (`aiohttp.ClientSession` with an isolated
  cookie jar), one request stream per account, and re-authentication when the
  login form or a redirect to `/login` appears.
- Discover contracts and their state from `/abonat/contractes` and query only
  `ALTA` ones. If the table is missing, fall back to `select#contrato_activo`
  and the inactive-contract warning.
- Per-contract sensors:
  - Cumulative reading (m³, `total_increasing`) with the reading date.
  - Last complete day's consumption.
  - Last billed period's consumption.
- External statistics with hourly granularity when available, otherwise daily.
  Always discard the current day or hour. Re-import a few days back, because
  late readings are added to the next day.
- Historical import in chunks of at most one year, throttled, and not earlier
  than 2016-01-01.
- Parsers keyed on selectors and column headers, tested with synthetic
  fixtures; do not depend on menu order or decorative text.

## Open questions

- Session expiry.
- CAPTCHA, rate limits or lockouts.
- Hourly availability for all meters (non-telemetry meters).
- Behaviour on meter replacement, when two serials are present.
- Earliest daily history boundary (confirmed from January 2022 for the
  captured contract) and the cause of the incomplete hourly HAR body,
  despite the complete six-month CSV.
- Timezone and DST transitions (23- or 25-hour days).

Useful additional samples (always anonymized): an expired session,
`POST /abonat/consums/horaris` with one month and a complete body, a daily
query before 2022, and a contract without a telemetry meter.
