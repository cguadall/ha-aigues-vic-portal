# User Validation Checklist

[Català](user-steps.md)

This checklist guides verification of the Aigües de Vic integration in a real Home Assistant environment. Check the items as you complete them.

## 1. Prerequisites

- [ ] An active account on the new [Aigües de Vic virtual office](https://oficina.aiguesvic.com) with verified email and password.
- [ ] Home Assistant version **2026.10.0** or later with `recorder` enabled (default).

## 2. Installation

Choose one of the two options:

- [ ] **Option A (HACS - Recommended):**
  1. In Home Assistant, open **HACS → Integrations**.
  2. In the three dots menu (⋮), select **Custom repositories**.
  3. Add `https://github.com/cguadall/ha-aigues-vic-portal` with the **Integration** category.
  4. Search for "Aigües Vic", install version **v0.3.1**, and restart Home Assistant.
- [ ] **Option B (Manual):**
  1. Copy `custom_components/aigues_vic/` into your Home Assistant `config/custom_components/` directory.
  2. Restart Home Assistant.

## 3. Testing and Configuration

- [ ] Open **Settings → Devices & Services → Add Integration** and search for **Aigües Vic**.
- [ ] First try entering an incorrect password: it should display "Invalid email or password".
- [ ] Enter valid credentials: configuration should succeed and create a device for each active contract (`ALTA`).
- [ ] Verify that inactive/cancelled contracts do not have active devices or entities.
- [ ] Verify that the sensors display the expected values:
  - **Meter index** (m³)
  - **Reading date**
  - **Last day consumption** (L)
  - **Last billed consumption** (m³)
  - **Night minimum consumption** (L/h) and **Possible leak** (for smart meters with hourly telemetry)
- [ ] Press the **Synchronize now** button from the device card and verify in the logs that a refresh completes without errors.

## 4. Energy & Water Dashboard

- [ ] Navigate to **Settings → Dashboards → Energy → Water consumption**.
- [ ] Add the `aigues_vic:water_<contract_number>` statistic ("<contract> Water consumption").
  *(Note that the meter reading index is the cumulative dial reading; only add the consumption statistic to the dashboard).*
- [ ] Optionally, in the integration options, enable "Import full consumption history" to build full history from the oldest available data on the portal.
- [ ] Check the next day that the scheduled daily refresh runs properly between 06:00 and 06:59.
