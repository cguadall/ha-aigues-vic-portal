"""Tests for the long-term statistics import."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from homeassistant.components.recorder.statistics import (
    list_statistic_ids,
    statistics_during_period,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.recorder import get_instance
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.aigues_vic.const import CONF_IMPORT_HISTORY, PORTAL_TIMEZONE
from custom_components.aigues_vic.models import DailyConsumption, HourlyConsumption
from custom_components.aigues_vic.statistics import (
    ImportOptions,
    build_statistics,
    daily_points,
    hourly_points,
    initial_start,
    statistic_id,
    storage_key,
)

from .fake_portal import FakePortal, sample_contract

ACTIVE = "100000001"
STAT_ID = statistic_id(ACTIVE)


def test_statistic_id() -> None:
    """The statistic id is stable."""
    assert STAT_ID == "aigues_vic:water_100000001"


def test_hourly_points_dst() -> None:
    """Repeated and missing local hours around DST changes are handled."""
    rows = [
        HourlyConsumption(datetime(2026, 10, 25, 1), None, 1),
        HourlyConsumption(datetime(2026, 10, 25, 2), None, 2),
        HourlyConsumption(datetime(2026, 10, 25, 2), None, 3),
        HourlyConsumption(datetime(2026, 10, 25, 3), None, 4),
        HourlyConsumption(datetime(2026, 3, 29, 2), None, 5),
        HourlyConsumption(datetime(2026, 3, 29, 3), None, 6),
    ]
    points = hourly_points(rows)
    assert points == {
        datetime(2026, 10, 24, 23, tzinfo=UTC): 0.001,
        datetime(2026, 10, 25, 0, tzinfo=UTC): 0.002,
        datetime(2026, 10, 25, 1, tzinfo=UTC): 0.003,
        datetime(2026, 10, 25, 2, tzinfo=UTC): 0.004,
        datetime(2026, 3, 29, 1, tzinfo=UTC): pytest.approx(0.011),
    }


def test_daily_points_and_sums() -> None:
    """Daily values are placed at local midnight with a running sum."""
    points = daily_points(
        [
            DailyConsumption(date(2026, 7, 2), None, None, 0.2),
            DailyConsumption(date(2026, 1, 1), None, None, 0.1),
        ]
    )
    assert list(points) == [
        datetime(2026, 7, 1, 22, tzinfo=UTC),
        datetime(2025, 12, 31, 23, tzinfo=UTC),
    ]
    stats = build_statistics(points, 10)
    assert [(s["start"], s["state"], s["sum"]) for s in stats] == [
        (datetime(2025, 12, 31, 23, tzinfo=UTC), 0.1, 10.1),
        (datetime(2026, 7, 1, 22, tzinfo=UTC), 0.2, pytest.approx(10.3)),
    ]


async def get_stats(hass: HomeAssistant) -> list[Any]:
    """Return all imported hourly statistics of the active contract."""
    await hass.async_block_till_done(wait_background_tasks=True)
    await async_wait_recording_done(hass)
    stats = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        datetime(2015, 1, 1, tzinfo=UTC),
        None,
        {STAT_ID},
        "hour",
        None,
        {"state", "sum"},
    )
    return stats.get(STAT_ID, [])


def expected_hourly(portal: FakePortal, first: date) -> tuple[int, float]:
    """Return the number of hours and litres from a day up to yesterday."""
    rows = [
        litres
        for moment, litres in portal.contracts[ACTIVE].hourly
        if first <= moment.date() < portal.today
    ]
    return len(rows), sum(rows) / 1000


async def test_initial_hourly_import(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    portal: FakePortal,
    hass_storage: dict[str, Any],
) -> None:
    """The last 30 complete days are imported hourly and reimports are stable."""
    stats = await get_stats(hass)
    count, total = expected_hourly(
        portal, initial_start(portal.today - timedelta(days=1))
    )
    assert len(stats) == count
    assert stats[-1]["sum"] == pytest.approx(total)
    metadata = await get_instance(hass).async_add_executor_job(
        list_statistic_ids, hass, {STAT_ID}
    )
    assert metadata[0]["name"] == "Casa Consum d'aigua"
    assert metadata[0]["statistics_unit_of_measurement"] == "m³"
    stored = hass_storage[storage_key(setup_integration.entry_id)]["data"]
    assert list(stored) == [ACTIVE]
    assert stored[ACTIVE]["source"] == "hourly"
    assert "history" not in stored[ACTIVE]
    assert "last_import" in stored[ACTIVE]

    coordinator = setup_integration.runtime_data
    await coordinator.importer.async_update(
        list(coordinator.data.values()), ImportOptions()
    )
    again = await get_stats(hass)
    assert len(again) == count
    assert again[-1]["sum"] == pytest.approx(total)


async def test_full_history_option(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    portal: FakePortal,
    hass_storage: dict[str, Any],
) -> None:
    """Enabling the option rebuilds the statistics from the oldest data."""
    entry = setup_integration
    await get_stats(hass)
    hass.config_entries.async_update_entry(entry, options={CONF_IMPORT_HISTORY: True})
    await hass.config_entries.async_reload(entry.entry_id)
    stats = await get_stats(hass)
    count, total = expected_hourly(portal, date(2016, 1, 1))
    assert len(stats) == count
    assert stats[-1]["sum"] == pytest.approx(total)
    key = storage_key(entry.entry_id)
    assert hass_storage[key]["data"][ACTIVE]["history"] is True

    hass.config_entries.async_update_entry(entry, options={CONF_IMPORT_HISTORY: False})
    await hass.config_entries.async_reload(entry.entry_id)
    await get_stats(hass)
    assert "history" not in hass_storage[key]["data"][ACTIVE]


async def test_daily_source(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    portal: FakePortal,
    portal_url: str,
) -> None:
    """Meters without hourly data are imported daily."""
    contract = portal.contracts[ACTIVE]
    contract.hourly.clear()
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    stats = await get_stats(hass)
    first = initial_start(portal.today - timedelta(days=1))
    days = [v for d, (_, v) in contract.daily.items() if first <= d < portal.today]
    assert len(stats) == len(days)
    assert stats[-1]["sum"] == pytest.approx(sum(days))


async def test_daily_source_switches_to_hourly(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    portal: FakePortal,
    portal_url: str,
    hass_storage: dict[str, Any],
) -> None:
    """A daily meter is probed weekly and switches to hourly data once available."""
    contract = portal.contracts[ACTIVE]
    hourly = list(contract.hourly)
    contract.hourly.clear()
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await get_stats(hass)
    coordinator = config_entry.runtime_data
    importer = coordinator.importer
    state = importer.contract_state(ACTIVE)
    assert state["source"] == "daily"
    assert state["probed"] == portal.today.isoformat()

    # Hourly data appears for the last few days only.
    yesterday = portal.today - timedelta(days=1)
    first_hourly = yesterday - timedelta(days=3)
    contract.hourly.extend(row for row in hourly if row[0].date() >= first_hourly)

    portal.requests.clear()
    await importer.async_update(list(coordinator.data.values()), ImportOptions())
    assert "hourly" not in " ".join(portal.paths())
    assert importer.contract_state(ACTIVE)["source"] == "daily"

    state = importer.contract_state(ACTIVE)
    state["probed"] = (portal.today - timedelta(days=7)).isoformat()
    await importer.async_update(list(coordinator.data.values()), ImportOptions())
    stats = await get_stats(hass)
    state = importer.contract_state(ACTIVE)
    since = first_hourly + timedelta(days=1)
    assert state["source"] == "hourly"
    assert state["hourly_since"] == since.isoformat()
    stored = hass_storage[storage_key(config_entry.entry_id)]["data"][ACTIVE]
    assert stored["hourly_since"] == since.isoformat()

    first = initial_start(portal.today - timedelta(days=1))
    daily = [v for d, (_, v) in contract.daily.items() if first <= d < since]
    daily_total = sum(daily)
    count, hourly_total = expected_hourly(portal, since)
    assert len(stats) == len(daily) + count
    assert stats[-1]["sum"] == pytest.approx(daily_total + hourly_total)

    # Later imports keep the daily values before the switch.
    state["probed"] = (portal.today - timedelta(days=7)).isoformat()
    await importer.async_update(list(coordinator.data.values()), ImportOptions())
    again = await get_stats(hass)
    assert len(again) == len(stats)
    assert again[-1]["sum"] == pytest.approx(stats[-1]["sum"])


async def test_full_history_after_switch(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    portal: FakePortal,
    portal_url: str,
) -> None:
    """A full history import keeps daily values before hourly data started."""
    contract = portal.contracts[ACTIVE]
    hourly = list(contract.hourly)
    contract.hourly.clear()
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await get_stats(hass)
    importer = config_entry.runtime_data.importer
    overviews = list(config_entry.runtime_data.data.values())
    contract.hourly.extend(hourly)
    importer.contract_state(ACTIVE)["probed"] = "2000-01-01"
    await importer.async_update(overviews, ImportOptions())
    since = date.fromisoformat(importer.contract_state(ACTIVE)["hourly_since"])

    await importer.async_update(overviews, ImportOptions(import_history=True))
    stats = await get_stats(hass)
    daily_total = sum(v for d, (_, v) in contract.daily.items() if d < since)
    count, hourly_total = expected_hourly(portal, since)
    daily_days = sum(1 for d in contract.daily if d < since)
    assert len(stats) == daily_days + count
    assert stats[-1]["sum"] == pytest.approx(daily_total + hourly_total)


async def test_full_history_uses_daily_before_hourly_data(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    portal: FakePortal,
    portal_url: str,
) -> None:
    """Days before the meter had hourly data are imported with daily values."""
    contract = sample_contract(ACTIVE, portal.today, alias="Casa", days=400)
    first_hourly = portal.today - timedelta(days=100)
    contract.hourly = [row for row in contract.hourly if row[0].date() >= first_hourly]
    portal.contracts[ACTIVE] = contract
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        config_entry, options={CONF_IMPORT_HISTORY: True}
    )
    portal.requests.clear()
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    stats = await get_stats(hass)

    importer = config_entry.runtime_data.importer
    assert importer.contract_state(ACTIVE)["hourly_since"] == first_hourly.isoformat()
    daily = [v for d, (_, v) in contract.daily.items() if d < first_hourly]
    count, hourly_total = expected_hourly(portal, first_hourly)
    assert len(stats) == len(daily) + count
    assert stats[-1]["sum"] == pytest.approx(sum(daily) + hourly_total)
    hourly_requests = [
        p for p in portal.paths() if p == "GET /consumptions/hourly-consumptions/"
    ]
    # The probe plus the chunks back to the first one without data.
    assert len(hourly_requests) <= 1 + 100 // 31 + 2


async def test_full_history_when_portal_omits_empty_tables(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    portal: FakePortal,
    portal_url: str,
) -> None:
    """Ranges without data, shown by the portal without a table, are empty."""
    portal.omit_empty_tables = True
    contract = sample_contract(ACTIVE, portal.today, alias="Casa", days=400)
    first_hourly = portal.today - timedelta(days=100)
    contract.hourly = [row for row in contract.hourly if row[0].date() >= first_hourly]
    portal.contracts[ACTIVE] = contract
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        config_entry, options={CONF_IMPORT_HISTORY: True}
    )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    stats = await get_stats(hass)

    coordinator = config_entry.runtime_data
    assert coordinator.statistics_failures == 0
    state = coordinator.importer.contract_state(ACTIVE)
    assert state["history"] is True
    assert state["hourly_since"] == first_hourly.isoformat()
    daily = [v for d, (_, v) in contract.daily.items() if d < first_hourly]
    count, hourly_total = expected_hourly(portal, first_hourly)
    assert len(stats) == len(daily) + count
    assert stats[-1]["sum"] == pytest.approx(sum(daily) + hourly_total)


def history_contract(portal: FakePortal) -> tuple[date, date]:
    """Use 400 days of daily data with hourly data for the last 100 days."""
    contract = sample_contract(ACTIVE, portal.today, alias="Casa", days=400)
    first_hourly = portal.today - timedelta(days=100)
    contract.hourly = [row for row in contract.hourly if row[0].date() >= first_hourly]
    portal.contracts[ACTIVE] = contract
    return min(contract.daily), first_hourly


def expected_history(portal: FakePortal, first_hourly: date) -> tuple[int, float]:
    """Return the expected rows and total of a full history import."""
    contract = portal.contracts[ACTIVE]
    daily = [v for d, (_, v) in contract.daily.items() if d < first_hourly]
    count, hourly_total = expected_hourly(portal, first_hourly)
    return len(daily) + count, sum(daily) + hourly_total


async def test_full_history_unexpected_old_page_is_not_the_end(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    portal: FakePortal,
    portal_url: str,
) -> None:
    """An odd page is an error, not proof that older data is missing."""
    first_daily, first_hourly = history_contract(portal)
    portal.odd_before = portal.today - timedelta(days=200)
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        config_entry, options={CONF_IMPORT_HISTORY: True}
    )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    partial = await get_stats(hass)

    coordinator = config_entry.runtime_data
    assert coordinator.statistics_failures == 1
    state = coordinator.importer.contract_state(ACTIVE)
    assert "history" not in state
    assert "history_since" not in state
    assert coordinator.importer.status(ACTIVE).state == "error"
    assert coordinator.importer.status(ACTIVE).last_error == "ParseError"
    first = datetime.fromtimestamp(partial[0]["start"], UTC)
    assert first.astimezone(PORTAL_TIMEZONE).date() > first_daily

    portal.odd_before = None
    await coordinator._async_update_statistics(list(coordinator.data.values()))
    stats = await get_stats(hass)
    assert coordinator.statistics_failures == 0
    state = coordinator.importer.contract_state(ACTIVE)
    assert state["history"] is True
    assert state["history_since"] == first_daily.isoformat()
    count, total = expected_history(portal, first_hourly)
    assert len(stats) == count
    assert stats[0]["sum"] == pytest.approx(stats[0]["state"])
    assert stats[-1]["sum"] == pytest.approx(total)


async def test_full_history_keeps_recent_days_on_failure(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    portal: FakePortal,
    portal_url: str,
) -> None:
    """A failure on an old chunk keeps the newer days and retries the rest."""
    first_daily, first_hourly = history_contract(portal)
    portal.odd_before = portal.today - timedelta(days=150)
    portal.odd_status = 500
    config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        config_entry, options={CONF_IMPORT_HISTORY: True}
    )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    partial = await get_stats(hass)

    coordinator = config_entry.runtime_data
    assert coordinator.statistics_failures == 1
    assert "history" not in coordinator.importer.contract_state(ACTIVE)
    first = datetime.fromtimestamp(partial[0]["start"], UTC)
    assert first.astimezone(PORTAL_TIMEZONE).date() <= first_hourly
    assert partial[0]["sum"] == pytest.approx(partial[0]["state"])
    assert partial[-1]["sum"] == pytest.approx(sum(row["state"] for row in partial))

    portal.odd_before = portal.odd_status = None
    await coordinator._async_update_statistics(list(coordinator.data.values()))
    stats = await get_stats(hass)
    assert coordinator.statistics_failures == 0
    state = coordinator.importer.contract_state(ACTIVE)
    assert state["history"] is True
    assert state["history_since"] == first_daily.isoformat()
    count, total = expected_history(portal, first_hourly)
    assert len(stats) == count
    assert stats[-1]["sum"] == pytest.approx(total)
