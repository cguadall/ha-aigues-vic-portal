"""Aigües Vic water consumption integration."""

from __future__ import annotations

import aiohttp
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers.typing import ConfigType

from .api import AiguesVicClient
from .cache import DataCache
from .const import DOMAIN
from .coordinator import AiguesVicConfigEntry, AiguesVicCoordinator, issue_id
from .services import async_setup_services
from .statistics import async_remove_store

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.SENSOR]
ISSUE_KINDS = ("update_failed", "statistics_failed")
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration actions."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: AiguesVicConfigEntry) -> bool:
    """Set up Aigües Vic from a config entry."""
    session = async_create_clientsession(hass, cookie_jar=aiohttp.CookieJar())
    client = AiguesVicClient(session, entry.data[CONF_EMAIL], entry.data[CONF_PASSWORD])
    coordinator = AiguesVicCoordinator(hass, entry, client)
    await coordinator.importer.async_load()
    if not await coordinator.async_restore():
        await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: AiguesVicConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        if getattr(entry, "runtime_data", None) is not None:
            await entry.runtime_data.client.async_logout()
        _async_delete_issues(hass, entry)
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: AiguesVicConfigEntry) -> None:
    """Remove stored state and cached data; long-term statistics are kept."""
    _async_delete_issues(hass, entry)
    await async_remove_store(hass, entry.entry_id)
    await DataCache(hass, entry.entry_id).async_remove()


def _async_delete_issues(hass: HomeAssistant, entry: AiguesVicConfigEntry) -> None:
    for kind in ISSUE_KINDS:
        ir.async_delete_issue(hass, DOMAIN, issue_id(kind, entry.entry_id))


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: AiguesVicConfigEntry, device: DeviceEntry
) -> bool:
    """Allow removing devices of contracts that are no longer reported."""
    contracts = entry.runtime_data.data or {}
    return not any(
        identifier[1] in contracts and contracts[identifier[1]].active
        for identifier in device.identifiers
    )
