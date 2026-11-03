"""Tests for setup, sensors, the coordinator and diagnostics."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.aigues_vic import async_remove_config_entry_device
from custom_components.aigues_vic.cache import cache_key
from custom_components.aigues_vic.const import DAILY_UPDATE_HOUR, DOMAIN
from custom_components.aigues_vic.coordinator import daily_update_time
from custom_components.aigues_vic.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.aigues_vic.exceptions import (
    AiguesVicError,
    ContractNotFoundError,
)
from custom_components.aigues_vic.statistics import storage_key

from .fake_portal import FakePortal, sample_contract

ACTIVE = "100000001"


async def test_first_refresh_error_diagnostics(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    portal_url: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Initial setup failures expose the underlying error in debug logs."""
    config_entry.add_to_hass(hass)
    with (
        caplog.at_level(logging.DEBUG, logger="custom_components.aigues_vic"),
        patch(
            "custom_components.aigues_vic.api.AiguesVicClient.async_get_overview",
            side_effect=ContractNotFoundError("Contract not available"),
        ),
    ):
        assert not await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.SETUP_RETRY
    assert "Portal data update failed (ContractNotFoundError)" in caplog.text
    assert "ContractNotFoundError: Contract not available" in caplog.text
    records = [
        record
        for record in caplog.records
        if record.message == "Portal data update failed (ContractNotFoundError)"
    ]
    assert len(records) == 1
    assert records[0].exc_info is not None


def entity_id(hass: HomeAssistant, contract_id: str, key: str) -> str:
    """Return the entity id of a sensor."""
    entity = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{contract_id}_{key}"
    )
    assert entity is not None
    return entity


async def test_setup_and_sensors(
    hass: HomeAssistant, setup_integration: MockConfigEntry, portal: FakePortal
) -> None:
    """Sensors are created for active contracts with portal values."""
    entry = setup_integration
    assert entry.state is ConfigEntryState.LOADED
    today = portal.today
    yesterday = today - timedelta(days=1)
    reading, volume = portal.contracts[ACTIVE].daily[yesterday]

    state = hass.states.get(entity_id(hass, ACTIVE, "meter_reading"))
    assert state is not None
    assert state.state == str(reading)
    assert state.attributes["unit_of_measurement"] == "m³"
    assert "state_class" not in state.attributes
    assert state.attributes["device_class"] == "water"
    date_state = hass.states.get(entity_id(hass, ACTIVE, "reading_date"))
    assert date_state is not None
    assert date_state.state == yesterday.isoformat()
    day_state = hass.states.get(entity_id(hass, ACTIVE, "last_day_consumption"))
    assert day_state is not None
    assert float(day_state.state) == round(volume * 1000, 1)
    assert day_state.attributes["day"] == yesterday.isoformat()
    registry = er.async_get(hass)
    date_entry = registry.async_get(entity_id(hass, ACTIVE, "reading_date"))
    assert date_entry is not None
    assert date_entry.entity_category is EntityCategory.DIAGNOSTIC
    billed = hass.states.get(entity_id(hass, ACTIVE, "last_billed_consumption"))
    assert billed is not None
    assert billed.state == "21.0"
    assert billed.attributes["period_end"] == (today - timedelta(days=16)).isoformat()
    assert billed.attributes["days"] == 92

    assert (
        len(er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)) == 13
    )
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, ACTIVE), entry.entry_id
    )
    assert device is not None
    assert device.name == "Casa"
    assert device.model == "15 mm"
    assert device.serial_number == "A12BC34567D"
    assert (
        dr.async_get(hass).async_get_device_by_identifier(
            (DOMAIN, "200000002"), entry.entry_id
        )
        is None
    )

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    unloaded = hass.config_entries.async_get_entry(entry.entry_id)
    assert unloaded is not None
    assert unloaded.state is ConfigEntryState.NOT_LOADED
    assert "POST /accounts/logout/" in portal.paths()


async def test_sensor_fallbacks(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    portal: FakePortal,
    portal_url: str,
) -> None:
    """Billed readings are used when there are no daily rows."""
    contract = portal.contracts[ACTIVE]
    contract.daily.clear()
    contract.hourly.clear()
    contract.billed = contract.billed[:1]
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    reading = hass.states.get(entity_id(hass, ACTIVE, "meter_reading"))
    assert reading is not None
    assert reading.state == "970"
    for key in ("last_day_consumption", "last_billed_consumption"):
        state = hass.states.get(entity_id(hass, ACTIVE, key))
        assert state is not None
        assert state.state == "unknown"

    contract.billed = []
    await config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)
    state = hass.states.get(entity_id(hass, ACTIVE, "reading_date"))
    assert state is not None
    assert state.state == "unknown"


@pytest.mark.parametrize(
    ("password", "fail_status", "state"),
    [
        ("other", None, ConfigEntryState.SETUP_ERROR),
        (None, 500, ConfigEntryState.SETUP_RETRY),
    ],
)
async def test_setup_errors(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    portal: FakePortal,
    portal_url: str,
    password: str | None,
    fail_status: int | None,
    state: ConfigEntryState,
) -> None:
    """Authentication errors start reauth; other errors retry."""
    if password:
        portal.password = password
    portal.fail_status = fail_status
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is state
    flows = hass.config_entries.flow.async_progress()
    assert bool(flows) is (state is ConfigEntryState.SETUP_ERROR)
    if flows:
        assert flows[0]["context"]["source"] == SOURCE_REAUTH


async def test_contract_alias_change(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    portal: FakePortal,
) -> None:
    """Changing contract alias updates device name and friendly name on reload."""
    entry = setup_integration
    device_reg = dr.async_get(hass)
    device = device_reg.async_get_device_by_identifier((DOMAIN, ACTIVE), entry.entry_id)
    assert device is not None
    assert device.name == "Casa"

    portal.contracts[ACTIVE].alias = "Oficina"
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    device = device_reg.async_get_device_by_identifier((DOMAIN, ACTIVE), entry.entry_id)
    assert device is not None
    assert device.name == "Casa"

    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    device = device_reg.async_get_device_by_identifier((DOMAIN, ACTIVE), entry.entry_id)
    assert device is not None
    assert device.name == "Oficina"
    state = hass.states.get(entity_id(hass, ACTIVE, "meter_reading"))
    assert state is not None
    assert state.attributes.get("friendly_name") == "Oficina Meter index"


async def test_contract_changes(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    portal: FakePortal,
) -> None:
    """New contracts add sensors and removed contracts become unavailable."""
    entry = setup_integration
    portal.contracts["300000003"] = sample_contract(
        "300000003", portal.today, days=5, hourly=False
    )
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get(entity_id(hass, "300000003", "meter_reading")) is not None

    del portal.contracts[ACTIVE]
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    state = hass.states.get(entity_id(hass, ACTIVE, "meter_reading"))
    assert state is not None
    assert state.state == STATE_UNAVAILABLE

    registry = dr.async_get(hass)
    removed = registry.async_get_device_by_identifier((DOMAIN, ACTIVE), entry.entry_id)
    kept = registry.async_get_device_by_identifier(
        (DOMAIN, "300000003"), entry.entry_id
    )
    assert removed is not None
    assert kept is not None
    assert await async_remove_config_entry_device(hass, entry, removed)
    assert not await async_remove_config_entry_device(hass, entry, kept)

    portal.fail_status = 500
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    state = hass.states.get(entity_id(hass, "300000003", "meter_reading"))
    assert state is not None
    assert state.state == STATE_UNAVAILABLE


async def test_daily_refresh_uses_home_assistant_local_time(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    portal: FakePortal,
    portal_url: str,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Scheduled refresh runs at 06:MM local, independent of manual refresh."""
    await hass.config.async_set_time_zone("Europe/Madrid")
    zone = ZoneInfo("Europe/Madrid")
    config_entry.add_to_hass(hass)
    start = datetime(2026, 10, 21, 5, 0, tzinfo=zone)
    freezer.move_to(start)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    portal.requests.clear()
    await config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert "GET /contracts/" in portal.paths()

    portal.requests.clear()
    update_time = config_entry.runtime_data.update_time
    assert update_time.hour == DAILY_UPDATE_HOUR
    assert update_time == daily_update_time(config_entry.entry_id)
    scheduled = datetime.combine(date(2026, 10, 21), update_time, zone)
    before = scheduled - timedelta(minutes=1)
    freezer.move_to(before)
    async_fire_time_changed(hass, before)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert "GET /contracts/" not in portal.paths()

    freezer.move_to(scheduled)
    async_fire_time_changed(hass, scheduled)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert "GET /contracts/" in portal.paths()


async def test_statistics_errors_are_logged(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Statistics failures do not break the coordinator."""
    coordinator = setup_integration.runtime_data
    with patch.object(coordinator.importer, "async_update", side_effect=AiguesVicError):
        await coordinator._async_update_statistics([])
    assert "Could not update water statistics: AiguesVicError" in caplog.text


async def test_diagnostics(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """Diagnostics contain no credentials or personal data."""
    await hass.async_block_till_done(wait_background_tasks=True)
    result = await async_get_config_entry_diagnostics(hass, setup_integration)
    text = repr(result)
    for secret in (
        "example.invalid",
        "correct horse",
        "A12BC34567D",
        "EXEMPLE",
        "Casa",
    ):
        assert secret not in text
    assert result["contracts"][0]["daily_rows"] == 31
    assert result["contracts"][0]["alias_set"] is True
    assert result["contracts"][1]["active"] is False
    assert result["contracts"][1]["first_day"] is None
    assert result["contracts"][0]["import"]["source"] == "hourly"
    assert result["update_failures"] == 0


async def test_remove_entry_removes_store(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    hass_storage: dict[str, Any],
) -> None:
    """Removing the entry deletes the import state and the data cache."""
    entry = setup_integration
    await hass.async_block_till_done(wait_background_tasks=True)
    key = storage_key(entry.entry_id)
    assert hass_storage[key]["data"][ACTIVE]["source"] == "hourly"
    assert cache_key(entry.entry_id) in hass_storage
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert key not in hass_storage
    assert cache_key(entry.entry_id) not in hass_storage


async def test_synchronize_button(
    hass: HomeAssistant, setup_integration: MockConfigEntry, portal: FakePortal
) -> None:
    """The button refreshes the portal data and reports failures."""
    button = er.async_get(hass).async_get_entity_id(
        "button", DOMAIN, f"{ACTIVE}_synchronize"
    )
    assert button is not None
    assert hass.states.get(button) is not None
    yesterday = portal.today - timedelta(days=1)
    portal.contracts[ACTIVE].daily[yesterday] = (4321, 0.5)
    portal.requests.clear()

    await hass.services.async_call(
        "button", "press", {"entity_id": button}, blocking=True
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert "GET /contracts/" in portal.paths()
    reading = hass.states.get(entity_id(hass, ACTIVE, "meter_reading"))
    assert reading is not None
    assert reading.state == "4321"

    portal.fail_status = 500
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "button", "press", {"entity_id": button}, blocking=True
        )
    state = hass.states.get(button)
    assert state is not None
    assert state.state != STATE_UNAVAILABLE

    portal.fail_status = None
    await hass.services.async_call(
        "button", "press", {"entity_id": button}, blocking=True
    )
    reading = hass.states.get(entity_id(hass, ACTIVE, "meter_reading"))
    assert reading is not None
    assert reading.state == "4321"
