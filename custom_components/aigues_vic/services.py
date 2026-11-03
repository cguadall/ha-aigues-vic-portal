"""Actions of the Aigües Vic integration."""

from __future__ import annotations

from datetime import date, timedelta

import probatio
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .api import portal_today
from .const import DOMAIN, HISTORY_START
from .coordinator import AiguesVicConfigEntry

SERVICE_REIMPORT = "reimport"
ATTR_CONFIG_ENTRY_ID = "config_entry_id"
ATTR_START_DATE = "start_date"

REIMPORT_SCHEMA = probatio.Schema(
    {
        probatio.Required(ATTR_CONFIG_ENTRY_ID): cv.string,
        probatio.Optional(ATTR_START_DATE): cv.date,
    }
)


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the integration actions."""

    async def _async_reimport(call: ServiceCall) -> None:
        entry: AiguesVicConfigEntry | None = hass.config_entries.async_get_entry(
            call.data[ATTR_CONFIG_ENTRY_ID]
        )
        if (
            entry is None
            or entry.domain != DOMAIN
            or entry.state is not ConfigEntryState.LOADED
        ):
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="entry_not_loaded"
            )
        start: date = call.data.get(ATTR_START_DATE, HISTORY_START)
        yesterday = portal_today() - timedelta(days=1)
        if not HISTORY_START <= start <= yesterday:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_start_date",
                translation_placeholders={
                    "first": HISTORY_START.isoformat(),
                    "last": yesterday.isoformat(),
                },
            )
        await entry.runtime_data.async_reimport(start)

    hass.services.async_register(
        DOMAIN, SERVICE_REIMPORT, _async_reimport, schema=REIMPORT_SCHEMA
    )
