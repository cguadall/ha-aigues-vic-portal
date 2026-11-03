"""Tests for retries, repair issues and the import diagnostic sensor."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.aigues_vic.const import DOMAIN, PORTAL_TIMEZONE
from custom_components.aigues_vic.coordinator import (
    FAILURES_BEFORE_ISSUE,
    RETRY_DELAYS,
    issue_id,
)
from custom_components.aigues_vic.exceptions import AiguesVicError

from .fake_portal import FakePortal

ACTIVE = "100000001"


async def setup_at_seven(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    portal: FakePortal,
    freezer: FrozenDateTimeFactory,
) -> MockConfigEntry:
    """Set up at 07:00 so retries never cross the daily refresh."""
    await hass.config.async_set_time_zone("Europe/Madrid")
    freezer.move_to(datetime.combine(portal.today, time(7), PORTAL_TIMEZONE))
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    return config_entry


async def advance(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, delay: timedelta
) -> None:
    """Move time forward and run the due callbacks."""
    freezer.tick(delay)
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_failed_updates_retry_and_raise_issue(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    portal: FakePortal,
    portal_url: str,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Failed updates are retried with backoff and reported after three."""
    entry = await setup_at_seven(hass, config_entry, portal, freezer)
    coordinator = entry.runtime_data
    issues = ir.async_get(hass)
    key = issue_id("update_failed", entry.entry_id)

    portal.fail_status = 500
    await coordinator.async_refresh()
    assert not coordinator.last_update_success
    assert issues.async_get_issue(DOMAIN, key) is None

    for delay in RETRY_DELAYS[: FAILURES_BEFORE_ISSUE - 1]:
        portal.requests.clear()
        await advance(hass, freezer, delay)
        assert portal.paths(), f"no retry after {delay}"

    issue = issues.async_get_issue(DOMAIN, key)
    assert issue is not None
    assert issue.translation_key == "update_failed"
    assert issue.translation_placeholders == {
        "account": entry.title,
        "failures": str(FAILURES_BEFORE_ISSUE),
        "error": "CannotConnectError",
    }

    portal.fail_status = None
    await advance(hass, freezer, RETRY_DELAYS[FAILURES_BEFORE_ISSUE - 1])
    assert coordinator.last_update_success
    assert issues.async_get_issue(DOMAIN, key) is None
    assert coordinator._unsub_retry is None


async def test_auth_failure_is_not_retried(
    hass: HomeAssistant,
    setup_integration: MockConfigEntry,
    portal: FakePortal,
) -> None:
    """Rejected credentials start reauthentication instead of retrying."""
    coordinator = setup_integration.runtime_data
    await hass.async_block_till_done(wait_background_tasks=True)
    portal.password = "other"
    await coordinator.client.async_logout()
    await coordinator.async_refresh()
    assert not coordinator.last_update_success
    assert coordinator._unsub_retry is None


async def test_statistics_failures_raise_issue(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """Repeated statistics failures are reported, retried and then cleared."""
    entry = setup_integration
    coordinator = entry.runtime_data
    issues = ir.async_get(hass)
    key = issue_id("statistics_failed", entry.entry_id)

    with patch.object(coordinator.importer, "async_update", side_effect=AiguesVicError):
        for _ in range(FAILURES_BEFORE_ISSUE):
            await coordinator._async_update_statistics([])
    issue = issues.async_get_issue(DOMAIN, key)
    assert issue is not None
    assert issue.translation_placeholders is not None
    assert issue.translation_placeholders["error"] == "AiguesVicError"
    assert coordinator._unsub_retry is not None

    await coordinator._async_update_statistics([])
    assert issues.async_get_issue(DOMAIN, key) is None

    with patch.object(coordinator.importer, "async_update", side_effect=AiguesVicError):
        for _ in range(FAILURES_BEFORE_ISSUE):
            await coordinator._async_update_statistics([])
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert issues.async_get_issue(DOMAIN, key) is None
    assert coordinator._unsub_retry is None


async def test_last_import_sensor(
    hass: HomeAssistant, setup_integration: MockConfigEntry
) -> None:
    """The diagnostic sensor reports when statistics were last imported."""
    await hass.async_block_till_done(wait_background_tasks=True)
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("sensor", DOMAIN, f"{ACTIVE}_last_import")
    assert entity_id is not None
    entity = registry.async_get(entity_id)
    assert entity is not None
    assert entity.entity_category == EntityCategory.DIAGNOSTIC
    state = hass.states.get(entity_id)
    assert state is not None
    imported = dt_util.parse_datetime(state.state)
    assert imported is not None
    assert (dt_util.utcnow() - imported).total_seconds() < 60
