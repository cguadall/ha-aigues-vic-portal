# Pending user steps

[Català](user-steps.md)

These steps need your portal account, your browser or your Home Assistant
installation, and cannot be automated from the repository. Tick them off as
you complete them.

## 1. Account security

- [ ] Change the virtual office password. The shape of the current one
      (length and symbol positions) was exposed during the initial analysis.
- [ ] Untick "Recorda'm" and delete the `oficina.aiguesvic.com` cookies in the
      browser. With that option enabled, the portal stores the email and
      password in clear text in the `username` and `password` cookies.

## 2. Additional HAR captures

The 16 October 2026 HAR now confirms failed login and the subsequent successful
login. Successful logout is captured in the 15 October 2026 HAR from 23:40.
The 16:29 capture, from a Firefox container, also confirms the first
`GET /login`, cookie approval and another complete login/logout.
These cases do not need repeating.

The 20 October 2026 capture from 23:04 confirms yearly daily queries and
data from January 2022. The six-month hourly query has an incomplete HAR
body, but the exported CSV contains 4,345 consecutive hours through
18 October and matches every readable HAR row. Repeating with one month is
optional to investigate the capture, not to demonstrate this data.

Capture the pending cases in the table. For each one:

1. Open the browser developer tools (F12) → **Network** tab, and enable
   **Preserve log**.
2. Perform the action, without ticking "Recorda'm".
3. Export with **Save all as HAR** into `local/` (ignored by Git), once the
   request has finished. For long hourly queries, start with one month to
   avoid an excessively large response.
4. Run `.venv/bin/python scripts/anonymize_har.py`. If it cannot guarantee the
   anonymization, it exits with an error and writes nothing.
5. Say which new files are in `local/anonymized/`. Never share the original
   HAR files.

Exported CSV files also contain personal data (the meter number). Keep them
in `local/` and do not share them without anonymizing: the script above
only processes HAR, not CSV.

| Capture | Action | Why |
| --- | --- | --- |
| Failed login — completed | Captured on 16 October 2026 | Confirmed: 302 to `/login` and form with "Dades d'accés incorrectes." |
| Successful login and logout — completed | Captured on 16 and 15 October 2026, respectively | Redirects confirmed |
| Initial form and cookies — completed | Captured on 16 October 2026 at 16:29 in a Firefox container | Initial form and Cookiebot confirmed; the HTTP client without `CookieConsent` still needs validation |
| Expired session | Leave the tab open for a few hours, then reload `/abonat/consums` | Detect expiry and the redirect |
| Daily history — completed | Captured on 20 October 2026: yearly ranges in 2022, 2024 and 2025 | Complete tables; excluded start date, missing days and multi-day readings |
| Old history — partial | Confirmed from January 2022; query an earlier year (e.g. 2017) | The earliest boundary is still unknown |
| Hourly history — data checked | Six-month CSV with 4,345 hours from 20 April to 18 October 2026; all 2,218 readable HAR rows match | One-month capture optional to investigate HAR NULs; no row limit demonstrated |
| DST change | On `/abonat/consums/horaris`, query 24–27 October 2026 (after the 27th) | Confirm the timezone and the 25-hour day |
| Other contracts — completed | Captured on 20 October 2026: `/abonat/contractes` with the `ALTA`/`BAIXA` state | The integration only queries `ALTA` contracts |

## 3. Real installation test

Version 0.1.0 can now be installed. It has been tested against a simulated
portal, not the real one.

- [ ] Copy `custom_components/aigues_vic/` into Home Assistant's
      `config/custom_components/` folder (version 2026.10 or later) and
      restart. For Docker Compose on a Raspberry Pi, follow the
      [deployment guide](deployment-docker.en.md).
- [ ] Enable debug logging in `configuration.yaml` before testing:

      ```yaml
      logger:
        logs:
          custom_components.aigues_vic: debug
      ```

- [ ] First try a wrong password: it must show "Invalid email or password".
      The portal flow is now confirmed by the HAR; if the result differs,
      collect the integration log.
- [ ] Add the integration from **Settings → Devices & services** with your
      email and password. Check that it can sign in without importing browser
      cookies or approving Cookiebot: the HTTP client does not execute
      JavaScript or send `CookieConsent`.
- [ ] Check that each active contract has a device and that the sensors match
      what the portal shows (reading, date, last day consumption and last
      billed consumption). Inactive contracts must not have a device.
- [ ] Press the device's **Synchronize now** button and check in the logs that
      a new query runs without errors.
- [ ] After a few minutes, search for `aigues_vic:water_<contract>` under
      **Developer tools → Statistics** and compare a few hourly values with
      the portal.
- [ ] Add the water-consumption statistic for the contract to the energy
      dashboard. The meter reading is only the cumulative index from the same
      meter (in whole m³), not additional consumption; do not add it.
- [ ] Enable the history import option and check that running it again or
      reloading the integration does not duplicate consumption.
- [ ] Leave it running for more than a day to check the automatic daily refresh
      between 06:00 and 06:59 (Home Assistant's configured time zone; the
      exact time is in the diagnostics), session renewal and incremental
      import.
- [ ] If something fails, attach the integration diagnostics and the
      `custom_components.aigues_vic` log lines. They are designed to exclude
      personal data, but review them before sharing anyway.

      If initial setup keeps retrying, enable `debug` logging, restart or
      reload, and search for `Portal data update failed`. This message
      includes the original exception traceback even when the UI only
      shows the error type. With Docker:

      ```bash
      sudo docker logs --since 10m homeassistant 2>&1 \
        | grep -A 45 -B 3 'Portal data update failed'
      ```

## 4. Publishing decisions

- [ ] Decide when to make the repository public: HACS only supports public
      repositories.
- [ ] Create the first release (`v0.1.0`) on GitHub once the real test passes.
