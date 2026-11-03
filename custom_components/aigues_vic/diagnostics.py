"""Diagnostics for Aigües Vic."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant

from .coordinator import AiguesVicConfigEntry

TO_REDACT = {CONF_EMAIL, CONF_PASSWORD, "address", "holder", "meter_serial", "alias"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: AiguesVicConfigEntry
) -> dict[str, Any]:
    """Return diagnostics without credentials or personal data."""
    coordinator = entry.runtime_data
    contracts = [
        {
            "contract": f"contract_{index}",
            "active": overview.active,
            "card": async_redact_data(asdict(overview.card), TO_REDACT),
            "alias_set": overview.contract.alias is not None,
            "daily_rows": len(overview.daily),
            "first_day": overview.daily[0].day.isoformat() if overview.daily else None,
            "last_day": overview.daily[-1].day.isoformat() if overview.daily else None,
            "billed_rows": len(overview.billed),
            "last_billed": overview.billed[-1].reading_date.isoformat()
            if overview.billed
            else None,
            "import": dict(
                coordinator.importer.contract_state(overview.contract.contract_id)
            ),
        }
        for index, overview in enumerate((coordinator.data or {}).values(), start=1)
    ]
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "last_update_success": coordinator.last_update_success,
        "update_time": coordinator.update_time.isoformat(),
        "update_failures": coordinator.update_failures,
        "statistics_failures": coordinator.statistics_failures,
        "contracts": contracts,
    }
