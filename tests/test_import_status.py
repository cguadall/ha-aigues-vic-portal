"""Tests for the import status diagnostic sensor."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.const import EVENT_STATE_CHANGED, EntityCategory
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.aigues_vic.const import DOMAIN
from custom_components.aigues_vic.exceptions import ParseError

from .fake_portal import FakePortal

ACTIVE = "100000001"


async def test_failed_import_preserves_state_and_logs_context(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A parse error preserves previous results and logs its request context."""
    await hass.async_block_till_done(wait_background_tasks=True)
    coordinator = setup_integration.runtime_data
    before = coordinator.importer.contract_state(ACTIVE).copy()
    assert "night_min" in before
    assert "leak" in before

    with (
        caplog.at_level("DEBUG", logger="custom_components.aigues_vic"),
        patch.object(
            coordinator.client,
            "_get_hourly_chunk",
            side_effect=ParseError("Expected column not found"),
        ),
    ):
        await coordinator._async_update_statistics(list(coordinator.data.values()))

    assert coordinator.importer.contract_state(ACTIVE) == before
    records = [
        record
        for record in caplog.records
        if record.name == "custom_components.aigues_vic.statistics"
        and "Statistics import failed" in record.message
    ]
    assert len(records) == 1
    assert "phase=hourly" in records[0].message
    assert "period=" in records[0].message
    assert records[0].exc_info is not None
    assert str(records[0].exc_info[1]) == "Expected column not found"


def status_entity(hass: HomeAssistant) -> str:
    """Return the entity id of the import status sensor."""
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "sensor", DOMAIN, f"{ACTIVE}_import_status"
    )
    assert entity_id is not None
    entity = registry.async_get(entity_id)
    assert entity is not None
    assert entity.entity_category == EntityCategory.DIAGNOSTIC
    return entity_id


async def test_import_status_progress(
    hass: HomeAssistant, setup_integration: MockConfigEntry, portal: FakePortal
) -> None:
    """A long reimport reports history progress and ends up to date."""
    await hass.async_block_till_done(wait_background_tasks=True)
    entity_id = status_entity(hass)
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "idle"
    assert state.attributes["options"] == [
        "idle",
        "importing",
        "importing_history",
        "error",
    ]
    assert state.attributes["last_success"] is not None

    seen: list[tuple[str, dict[str, Any]]] = []

    @callback
    def _changed(event: Event[EventStateChangedData]) -> None:
        if event.data["entity_id"] == entity_id and event.data["new_state"]:
            new = event.data["new_state"]
            seen.append((new.state, dict(new.attributes)))

    unsub = hass.bus.async_listen(EVENT_STATE_CHANGED, _changed)
    await setup_integration.runtime_data.async_reimport(
        portal.today - timedelta(days=400)
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    unsub()

    running = [attrs for value, attrs in seen if value == "importing_history"]
    assert running
    progress = [attrs["progress"] for attrs in running if attrs["progress"]]
    assert len(progress) > 2
    assert progress == sorted(progress)
    assert all(0 < value < 100 for value in progress)
    assert {attrs["phase"] for attrs in running} >= {"hourly"}
    assert any(attrs["period"] for attrs in running)
    assert seen[-1][0] == "idle"
    assert seen[-1][1]["phase"] is None
    assert seen[-1][1]["progress"] is None


async def test_import_status_errors(
    hass: HomeAssistant, setup_integration: MockConfigEntry, portal: FakePortal
) -> None:
    """Import and portal failures are reported until the next success."""
    await hass.async_block_till_done(wait_background_tasks=True)
    coordinator = setup_integration.runtime_data
    entity_id = status_entity(hass)

    portal.fail_status = 500
    with pytest.raises(HomeAssistantError):
        await coordinator.async_reimport(portal.today - timedelta(days=10))
    await hass.async_block_till_done()
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "error"
    assert state.attributes["last_error"] == "CannotConnectError"
    assert state.attributes["failures"] == 1

    await coordinator.async_refresh()
    await hass.async_block_till_done()
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "error"
    assert state.attributes["next_retry"] is not None

    portal.fail_status = None
    await coordinator.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "idle"
    assert state.attributes["last_error"] is None
    assert state.attributes["failures"] == 0
    assert state.attributes["next_retry"] is None
