"""Tests for the portal data cache used at startup."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.aigues_vic.cache import cache_key, deserialize, serialize
from custom_components.aigues_vic.statistics import storage_key

from .fake_portal import FakePortal
from .test_init import ACTIVE, entity_id


async def restart(
    hass: HomeAssistant, entry: MockConfigEntry, portal: FakePortal
) -> list[str]:
    """Set the entry up again and return the portal requests it made."""
    await hass.async_block_till_done(wait_background_tasks=True)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    portal.requests.clear()
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    return portal.paths()


async def test_serialization_round_trip(setup_integration: MockConfigEntry) -> None:
    """Every overview survives serialization unchanged."""
    for overview in setup_integration.runtime_data.data.values():
        assert deserialize(serialize(overview)) == overview


async def test_restart_uses_recent_cache(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    portal: FakePortal,
    hass_storage: dict[str, Any],
) -> None:
    """Data fetched since the last scheduled refresh is reused without login."""
    entry = setup_integration
    sensor = entity_id(hass, ACTIVE, "meter_reading")
    before = hass.states.get(sensor)
    assert before is not None
    assert hass_storage[cache_key(entry.entry_id)]["data"]["fetched"]
    assert await restart(hass, entry, portal) == []
    after = hass.states.get(sensor)
    assert after is not None
    assert after.state == before.state


async def test_restore_imports_pending_statistics(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    portal: FakePortal,
    hass_storage: dict[str, Any],
) -> None:
    """Cached data that was never imported is imported after a restart."""
    entry = setup_integration
    await hass.async_block_till_done(wait_background_tasks=True)
    hass_storage[storage_key(entry.entry_id)]["data"][ACTIVE]["last_import"] = (
        "2000-01-01T00:00:00+00:00"
    )
    paths = await restart(hass, entry, portal)
    assert "GET /contracts/" not in paths
    assert any("hourly" in path for path in paths)


async def test_stale_or_invalid_cache_is_refreshed(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    portal: FakePortal,
    hass_storage: dict[str, Any],
) -> None:
    """Old or unreadable cached data triggers a normal portal refresh."""
    entry = setup_integration
    key = cache_key(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    hass_storage[key]["data"]["fetched"] = "2000-01-01T00:00:00+00:00"
    assert "GET /contracts/" in await restart(hass, entry, portal)

    hass_storage[key]["data"]["contracts"] = {ACTIVE: {"active": True}}
    assert "GET /contracts/" in await restart(hass, entry, portal)

    valid = serialize(entry.runtime_data.data[ACTIVE])
    hass_storage[key]["data"]["contracts"] = {"999999999": valid}
    assert "GET /contracts/" in await restart(hass, entry, portal)

    invalid_id = serialize(entry.runtime_data.data[ACTIVE])
    invalid_id["contract"]["contract_id"] = "../bad"
    hass_storage[key]["data"]["contracts"] = {"../bad": invalid_id}
    assert "GET /contracts/" in await restart(hass, entry, portal)
