"""Tests for the night minimum and possible leak entities."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timedelta

from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.aigues_vic.const import DOMAIN, PORTAL_TIMEZONE
from custom_components.aigues_vic.models import HourlyConsumption
from custom_components.aigues_vic.statistics import usage_summary

from .fake_portal import FakePortal

ACTIVE = "100000001"
DAY = date(2026, 10, 20)


def rows(day: date, litres: Sequence[float]) -> list[HourlyConsumption]:
    """Return consecutive hourly rows starting at midnight of a day."""
    start = datetime.combine(day, datetime.min.time())
    return [
        HourlyConsumption(start + timedelta(hours=hour), None, value)
        for hour, value in enumerate(litres)
    ]


def test_usage_summary() -> None:
    """The night minimum and continuous flow use the last complete day."""
    data = rows(DAY - timedelta(days=1), [0] * 24) + rows(
        DAY, [4, 3, 2.5, 3, 6, 9] + [20] * 18
    )
    data += rows(DAY + timedelta(days=1), [0, 0])
    summary = usage_summary(data, DAY)
    assert summary == {
        "night_min": 2.5,
        "night_day": DAY.isoformat(),
        "leak": True,
        "leak_min": 2.5,
        "leak_end": datetime(2026, 10, 21, tzinfo=PORTAL_TIMEZONE).isoformat(),
    }

    data = rows(DAY, [4, 3, 0, 3, 6, 9] + [20] * 18)
    summary = usage_summary(data, DAY)
    assert summary["leak"] is False
    assert summary["night_min"] == 0


def test_usage_summary_without_enough_data() -> None:
    """No leak verdict without 24 hours and no night value without that day."""
    assert usage_summary(rows(DAY, [1] * 23), DAY) == {
        "night_min": 1,
        "night_day": DAY.isoformat(),
    }
    assert usage_summary(rows(DAY - timedelta(days=1), [1] * 3), DAY) == {}


def entity_id(hass: HomeAssistant, platform: str, key: str) -> str:
    """Return the entity id of a contract entity."""
    entity = er.async_get(hass).async_get_entity_id(platform, DOMAIN, f"{ACTIVE}_{key}")
    assert entity is not None
    return entity


async def test_leak_entities(
    hass: HomeAssistant, setup_integration: MockConfigEntry, portal: FakePortal
) -> None:
    """Entities reflect the hourly data of the last complete day."""
    await hass.async_block_till_done(wait_background_tasks=True)
    yesterday = portal.today - timedelta(days=1)
    contract = portal.contracts[ACTIVE]
    night = [
        litres
        for moment, litres in contract.hourly
        if moment.date() == yesterday and moment.hour < 6
    ]
    sensor = hass.states.get(entity_id(hass, "sensor", "night_minimum"))
    assert sensor is not None
    assert float(sensor.state) == min(night)
    assert sensor.attributes["unit_of_measurement"] == "L/h"
    assert sensor.attributes["day"] == yesterday.isoformat()
    leak = hass.states.get(entity_id(hass, "binary_sensor", "possible_leak"))
    assert leak is not None
    assert leak.state == STATE_ON
    assert leak.attributes["device_class"] == "problem"

    noon = datetime.combine(yesterday, datetime.min.time()) + timedelta(hours=12)
    contract.hourly = [
        (moment, 0.0 if moment == noon else litres)
        for moment, litres in contract.hourly
    ]
    await setup_integration.runtime_data.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)
    leak = hass.states.get(entity_id(hass, "binary_sensor", "possible_leak"))
    assert leak is not None
    assert leak.state == STATE_OFF
    assert leak.attributes["minimum"] == 0


async def test_leak_unknown_for_daily_meters(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    portal: FakePortal,
    portal_url: str,
) -> None:
    """Meters without hourly data have no leak verdict."""
    portal.contracts[ACTIVE].hourly.clear()
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    leak = hass.states.get(entity_id(hass, "binary_sensor", "possible_leak"))
    assert leak is not None
    assert leak.state == STATE_UNKNOWN
    sensor = hass.states.get(entity_id(hass, "sensor", "night_minimum"))
    assert sensor is not None
    assert sensor.state == STATE_UNKNOWN
