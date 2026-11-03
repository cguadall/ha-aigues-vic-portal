# Aigües Vic for Home Assistant

[Documentació en català](README.md)

An independent, unofficial custom integration for water consumption data from
the Aigües Vic (AVSA) [customer portal](https://oficina.aiguesvic.com).

## Status: version 0.3.1, not yet validated against the real portal

The integration is implemented and tested against a simulated portal built from
captures of the new Aigües de Vic REST API, but it has not yet been validated
against the real portal on a live Home Assistant installation. JWT authentication
flows (sign-in and token refresh), contract listings, and hourly/daily
consumption retrieval have been verified against HAR captures of the new portal.
See [docs/user-steps.en.md](docs/user-steps.en.md) for remaining validation steps.

## Features

- UI configuration with the virtual office email and password, and
  reauthentication when the password stops working.
- One device per active contract (state `ALTA` in the portal's contract
  list); inactive contracts are not queried. New contracts are added
  automatically; contracts that disappear or are deactivated become unavailable
  and their device can be deleted.
- Sensors per contract:
  - **Meter index** (m³): the latest complete daily reading or, if there is
    none, the latest billed reading. It is a cumulative index, not consumption,
    and is not offered as an energy dashboard source.
  - **Reading date** (diagnostic).
  - **Last day consumption** (L): the latest complete day, given in the «Day»
    attribute; the current day is ignored.
  - **Night minimum consumption** (L/h): the lowest hourly consumption between
    00:00 and 06:00 of the latest complete day. If it never drops to zero it
    may point to a leak or an appliance that runs continuously.
  - **Last billed consumption** (m³), with the period end date and days.
  - **Consumption this month** and **Consumption previous month** (m³),
    computed from the statistics; the month is that of the latest day with data
    («Month» attribute). The previous month is unknown when the statistics do
    not cover it completely.
  - **Daily average (30 days)** (L), with the counted days in the «Days»
    attribute.
  - **Data available until** (diagnostic): end of the latest imported hour (or
    day).
  - **Last statistics import** (diagnostic): when statistics were last
    imported.
  - **Statistics import** (diagnostic): *Up to date*, *Importing*, *Importing
    history* or *Error*, with the progress. See
    [Import status](#import-status).
- **Possible leak** binary sensor per contract: on when none of the last 24
  hours with data had zero consumption. See [Leak detection](#leak-detection).
- **Synchronize now** button per contract (in the device configuration
  section) to query the portal without waiting for the periodic update.
- Long-term statistics `aigues_vic:water_<contract>` with hourly consumption,
  or daily consumption when the meter has no hourly data. Daily meters are
  checked weekly for hourly data; once available, it is imported from the
  first complete day and earlier days stay daily.
- Option to import the full available history, from newest to oldest.
- Optional water price to get the `aigues_vic:cost_<contract>` cost statistic
  for the energy dashboard.
- `aigues_vic.reimport` action to import the statistics again from a given
  day or the whole history.
- Diagnostics without credentials, addresses, holders or meter numbers.
- Catalan and English UI and documentation.

Payments, contract changes and submitting readings are out of scope.
The portal may change without notice; AVSA does not guarantee compatibility.

## Installation

Requires Home Assistant **2026.10.0** or later with `recorder` enabled (the
default).

- **HACS (custom repository):** in HACS, go to *Integrations*, click the top right three-dot menu, and select *Custom repositories*. Add
  `https://github.com/cguadall/ha-aigues-vic-portal` as a custom repository of
  type *Integration*, search for «Aigües Vic», install it, and restart Home Assistant.
- **Manual:** copy `custom_components/aigues_vic/` into the
  `custom_components/` folder of your Home Assistant configuration and restart.
  For Docker Compose (e.g. on a Raspberry Pi), see
  [docs/deployment-docker.en.md](docs/deployment-docker.en.md).

## Configuration

1. *Settings → Devices & services → Add integration → Aigües Vic*.
2. Enter the virtual office email and password. Each account can only be
   added once.
3. Optionally, under *Options*, enable "Import the full consumption history".
   The integration reloads and rebuilds the statistics from the oldest
   available data. This can take a few minutes because portal requests are
   sent one at a time and spaced out.
4. Optionally, under *Options*, enter the "Water price" per m³. See
   [Energy dashboard](#energy-dashboard).

The first import covers the previous and current months (at least the last 30
days).

YAML configuration is not supported.

### Data updates

The portal publishes a day's data during the following night (hourly
consumption arrives up to about 02:00). The integration polls the portal daily
between 06:00 and 06:59 in Home Assistant's configured time zone; the minute is
fixed per installation so they do not all query the portal at once. Data can
therefore be up to one day late. Diagnostics show the exact time
(`update_time`).

On setup or reload it reuses the stored data if it was fetched after the last
scheduled refresh; otherwise it queries the portal immediately. Each import
re-reads the last 14 days to pick up corrections and continues the running sum
without duplicates.

If an update or import fails, it is retried after 30 minutes, 1 hour, 2 hours
and then every 4 hours until it succeeds. After 3 consecutive errors a notice
appears under *Settings → Repairs*; it clears itself after the next successful
update. If the portal rejects the password, there is no retry: reauthentication
starts instead.

To force a synchronization, press the **Synchronize now** button of any
contract: it updates every contract of the account and the statistics. This
does not change the automatic daily refresh. If the portal fails, the button
shows an error and stays available to retry. It can also be used in automations
with the `button.press` action. There is no need to press it often: the portal
only publishes data once a day.

#### Import status

The **Statistics import** sensor of each contract shows:

| State | Meaning |
| --- | --- |
| Up to date | The last import finished successfully. |
| Importing | The latest days are being imported. |
| Importing history | More than a year is being imported (history or `aigues_vic.reimport`). |
| Error | The last update or import failed; it will be retried. |

While importing, the «Phase» (checking hourly data, hourly consumption, daily
consumption or cost), «Period» (range being requested from the portal) and
«Progress» (%) attributes show how far it is; they are not stored in the
history. It also has «Started», «Last success», «Last error», «Consecutive
failures», «Next retry», «History complete», «Hourly data since» and «History
since». After a
Home Assistant restart it starts as *Up to date* until the next import.

If it fails, values from the last successful import are preserved. To investigate
the cause, enable `custom_components.aigues_vic: debug` using the
`logger.set_level` action and synchronize again. Debug logging includes the
phase, period and error traceback without saving the portal response.

Example automation to get notified when the import fails:

```yaml
triggers:
  - trigger: state
    entity_id: sensor.<contract>_statistics_import
    to: error
    for: "01:00:00"
actions:
  - action: notify.notify
    data:
      message: >-
        Aigües Vic cannot import the consumption
        ({{ state_attr(trigger.entity_id, 'last_error') }}).
```

According to the portal notice, an identical value repeated over consecutive
hours is an estimate, and a day without data shows 0 with its consumption added
to the next day. The integration imports the values as the portal reports them.

### Leak detection

With hourly data, the integration computes two indicators on each import:

- **Night minimum consumption**: the lowest hourly value between 00:00 and
  06:00 of the latest complete day («Day» attribute).
- **Possible leak**: on when every one of the last 24 hours with data had some
  consumption. Its attributes give the lowest hourly consumption of those hours
  and when they end.

They are hints, not a certain detection: an appliance running all night (water
softener, irrigation) also triggers them, and hours estimated by the portal
(the same value repeated) can hide hours without consumption. Data arrives the
next day, so the warning always refers to the previous day. For a meter without
hourly data, both are unknown.

They can be used in an automation to get notified, for example when **Possible
leak** changes to «Problem».

### Energy dashboard

Under *Energy dashboard → Water consumption*, add the statistic
`aigues_vic:water_<contract>` ("<alias or contract> Consum d'aigua"). The meter
index and this statistic are two representations of the same meter, so the
index has no state class and cannot be added to the dashboard. The statistic
assigns each value to the hour (or day) when it was consumed, which a sensor
cannot do with data that arrives the next day.

If you added the meter reading to the dashboard before version 0.2.0, remove it.
Home Assistant will show a notice under *Repairs* because the sensor no longer
has a state class; you can delete its old statistics from *Developer tools →
Statistics*.

History is imported from the newest to the oldest data: hourly data in 31-day
chunks and daily data in 92-day chunks. Only an older chunk without data is
taken as the start of the history; the **Statistics import** sensor shows its
first day in «History since». If the portal fails or answers an older chunk with
an unexpected page, the import goes to *Error* and is not marked complete: the
newer days already fetched are stored anyway and the retry tries the history
again. If an old period always fails, import from a given date with
`aigues_vic.reimport` and `start_date`.

For an hourly meter, days before the portal's first hourly data are imported
with their daily value, and hourly afterwards. The same applies to a meter that
switched from daily to hourly data.

#### Cost

The energy dashboard can only compute the cost from a price when the source is
an entity, not an external statistic. So, if you enter a price in the options,
the integration creates the `aigues_vic:cost_<contract>` statistic
("<contract> Cost de l'aigua") in Home Assistant's configured currency: each
hour's consumption multiplied by the price. In the dashboard water source,
choose "Use an entity tracking the total costs" and select this statistic.

It is a single price: changing it recalculates the cost of the whole history.
Use the variable part of the bill (water, levy and sewage per m³); fixed fees
and tiers are not included. With price 0 the statistic is neither created nor
updated; an existing one is kept until you delete it from *Developer tools →
Statistics*.

### Importing again

The **Reimport statistics** action (`aigues_vic.reimport`) reads again from the
portal the data of every contract of an account from the given date until
yesterday, and recalculates the sums from that day. It is useful when the portal
corrects data older than the 14 days checked every day. Without `start_date`, it
imports again the whole history the portal has.

```yaml
action: aigues_vic.reimport
data:
  config_entry_id: <integration id>
  start_date: "2026-09-01"
```

## Removal

Delete the integration from *Devices & services*. Its internal import state is
deleted and the portal session is closed. Long-term statistics are kept; they
can be deleted under *Developer tools → Statistics*. For a manual installation,
also delete `custom_components/aigues_vic/` and restart.

## Development

```sh
python3.14 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m mypy
.venv/bin/python -m pytest
```

Direct development dependencies are pinned in `requirements-dev.txt`. The
integration has no external runtime dependencies: it uses standard Home Assistant
libraries (`aiohttp`, etc.).

Tests start Home Assistant with `pytest-homeassistant-custom-component` and a
simulated portal (`tests/fake_portal.py`) that reproduces the REST API with
synthetic data: JWT authentication and token refresh, contract listings with `ALTA`/`BAIXA` states,
inactive contracts, pagination, and JSON responses for hourly and daily readings and consumptions.

GitHub Actions includes equivalent checks, Hassfest and HACS validation.
Remote workflows run after each push. The
[Quality Scale](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/)
is a development guide, not an official certification of this project.

## Required samples and privacy

Credentials are stored in the Home Assistant configuration and only sent to
the official API (`api-oficina.aiguesvic.com`). Analysing portal changes may require new captures,
which can be kept temporarily in gitignored `local/`. Tests only use synthetic
data.

- Never share passwords, JWT tokens, cookies or authorization headers.
- Remove names, emails, addresses, tax IDs, bank details and personal
  identifiers; use consistent synthetic replacements across samples.
- Do not attach full HAR files: they may contain active sessions and personal
  information.
- Preserve structure, JSON formats and relationships between records.
- CAPTCHA or MFA would require a supported access method, not circumvention.

To anonymize portal HAR captures, save them in `local/` and run:

```sh
.venv/bin/python scripts/anonymize_har.py
```

The script uses only the standard library. It writes
`local/anonymized/*.anon.har` and:

- Drops third-party requests and static or binary bodies.
- Redacts cookies, sensitive headers, tokens and credentials.
- Replaces contract, meter, invoice and UUID values with pseudonyms that stay
  consistent within a run.
- Replaces names, addresses and aliases with placeholders.

If any original value remains after replacement, it writes nothing and exits
with an error. Still review the files before sharing them.

The portal analysis is in [docs/portal-analysis.en.md](docs/portal-analysis.en.md),
and the steps that depend on you are in [docs/user-steps.en.md](docs/user-steps.en.md).

## License and attribution

[MIT](LICENSE). Not affiliated with AVSA or Home Assistant.
The icon is original project artwork, not AVSA's logo.
Its editable source is [assets/icon.svg](assets/icon.svg). To regenerate the
PNG with ImageMagick (optional, not required for tests):

```sh
magick -background none assets/icon.svg -strip PNG32:custom_components/aigues_vic/brand/icon.png
```
