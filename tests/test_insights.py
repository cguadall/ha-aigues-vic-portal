"""Tests for the cost statistic, context sensors and the reimport action."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import pytest
from homeassistant.components.recorder.statistics import (
    list_statistic_ids,
    statistics_during_period,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.recorder import get_instance
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.aigues_vic.const import CONF_PRICE, DOMAIN, PORTAL_TIMEZONE
from custom_components.aigues_vic.statistics import (
    cost_statistic_id,
    initial_start,
    statistic_id,
)

from .fake_portal import FakePortal, sample_contract

ACTIVE = "100000001"


@pytest.fixture(autouse=True)
async def madrid(hass: HomeAssistant, portal: FakePortal) -> None:
    """Use the portal time zone and enough data for two full months."""
    await hass.config.async_set_time_zone("Europe/Madrid")
    portal.contracts[ACTIVE] = sample_contract(
        ACTIVE, portal.today, alias="Casa", days=90
    )


async def last_sums(hass: HomeAssistant, *ids: str) -> dict[str, list[Any]]:
    """Return the hourly statistics of the given ids."""
    await hass.async_block_till_done(wait_background_tasks=True)
    await async_wait_recording_done(hass)
    return await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        datetime(2015, 1, 1, tzinfo=UTC),
        None,
        set(ids),
        "hour",
        None,
        {"state", "sum"},
    )


def litres(portal: FakePortal, first: date, last: date) -> float:
    """Return the hourly litres between two days, both included."""
    return sum(
        value
        for moment, value in portal.contracts[ACTIVE].hourly
        if first <= moment.date() <= last
    )


def state_of(hass: HomeAssistant, key: str) -> Any:
    """Return the state object of a sensor of the active contract."""
    entity = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{ACTIVE}_{key}")
    assert entity is not None
    state = hass.states.get(entity)
    assert state is not None
    return state


async def test_context_sensors(
    hass: HomeAssistant, setup_integration: MockConfigEntry, portal: FakePortal
) -> None:
    """Month, previous month, daily average and data end use the statistics."""
    await last_sums(hass)
    yesterday = portal.today - timedelta(days=1)
    month_start = yesterday.replace(day=1)
    previous_start = (month_start - timedelta(days=1)).replace(day=1)
    assert initial_start(yesterday) <= previous_start

    month = state_of(hass, "month_consumption")
    assert float(month.state) == pytest.approx(
        litres(portal, month_start, yesterday) / 1000, abs=1e-6
    )
    assert month.attributes["month"] == month_start.strftime("%Y-%m")
    previous = state_of(hass, "previous_month_consumption")
    assert float(previous.state) == pytest.approx(
        litres(portal, previous_start, month_start - timedelta(days=1)) / 1000,
        abs=1e-6,
    )
    assert previous.attributes["month"] == previous_start.strftime("%Y-%m")
    average = state_of(hass, "daily_average")
    expected = litres(portal, yesterday - timedelta(days=29), yesterday) / 30
    assert float(average.state) == pytest.approx(expected, abs=0.05)
    assert average.attributes["days"] == 30
    until = state_of(hass, "data_until")
    assert until.state == datetime.combine(
        portal.today, time(), PORTAL_TIMEZONE
    ).astimezone(UTC).isoformat(timespec="seconds")


async def test_cost_statistic(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    portal: FakePortal,
) -> None:
    """Setting a price builds a cost statistic in the configured currency."""
    entry = setup_integration
    water, cost = statistic_id(ACTIVE), cost_statistic_id(ACTIVE)
    assert cost not in await last_sums(hass, water, cost)

    portal.requests.clear()
    hass.config_entries.async_update_entry(entry, options={CONF_PRICE: 1.5})
    await hass.config_entries.async_reload(entry.entry_id)
    stats = await last_sums(hass, water, cost)
    assert "GET /contracts/" not in portal.paths()
    assert len(stats[cost]) == len(stats[water])
    assert stats[cost][-1]["sum"] == pytest.approx(stats[water][-1]["sum"] * 1.5)
    metadata = await get_instance(hass).async_add_executor_job(
        list_statistic_ids, hass, {cost}
    )
    assert metadata[0]["name"] == "Casa Cost de l'aigua"
    assert metadata[0]["statistics_unit_of_measurement"] == hass.config.currency

    coordinator = entry.runtime_data
    overviews = list(coordinator.data.values())
    await coordinator.importer.async_update(overviews, coordinator.import_options)
    again = await last_sums(hass, water, cost)
    assert len(again[cost]) == len(stats[cost])
    assert again[cost][-1]["sum"] == pytest.approx(stats[cost][-1]["sum"])


async def test_reimport_action(
    hass: HomeAssistant, setup_integration: MockConfigEntry, portal: FakePortal
) -> None:
    """The action rebuilds statistics from a day, beyond the usual window."""
    water = statistic_id(ACTIVE)
    before = (await last_sums(hass, water))[water]
    changed = portal.today - timedelta(days=20)
    contract = portal.contracts[ACTIVE]
    contract.hourly = [
        (moment, value + 100 if moment.date() == changed else value)
        for moment, value in contract.hourly
    ]
    await hass.services.async_call(
        DOMAIN,
        "reimport",
        {
            "config_entry_id": setup_integration.entry_id,
            "start_date": (changed - timedelta(days=1)).isoformat(),
        },
        blocking=True,
    )
    after = (await last_sums(hass, water))[water]
    assert len(after) == len(before)
    assert after[-1]["sum"] == pytest.approx(before[-1]["sum"] + 24 * 0.1)


async def test_reimport_action_validation(
    hass: HomeAssistant, setup_integration: MockConfigEntry, portal: FakePortal
) -> None:
    """Invalid dates and unknown accounts are rejected."""
    for data in (
        {"config_entry_id": setup_integration.entry_id, "start_date": portal.today},
        {"config_entry_id": setup_integration.entry_id, "start_date": "2015-12-31"},
        {"config_entry_id": "missing", "start_date": "2026-01-01"},
    ):
        with pytest.raises(ServiceValidationError):
            await hass.services.async_call(DOMAIN, "reimport", data, blocking=True)


async def test_reimport_action_without_start(
    hass: HomeAssistant, setup_integration: MockConfigEntry, portal: FakePortal
) -> None:
    """Without a start date the action imports everything the portal has."""
    water = statistic_id(ACTIVE)
    await hass.services.async_call(
        DOMAIN,
        "reimport",
        {"config_entry_id": setup_integration.entry_id},
        blocking=True,
    )
    stats = (await last_sums(hass, water))[water]
    contract = portal.contracts[ACTIVE]
    first = min(contract.daily)
    hours = [v for m, v in contract.hourly if m.date() < portal.today]
    assert len(stats) == len(hours)
    assert stats[-1]["sum"] == pytest.approx(sum(hours) / 1000)
    state = setup_integration.runtime_data.importer.contract_state(ACTIVE)
    assert state["history_since"] == first.isoformat()
    assert "history" not in state
