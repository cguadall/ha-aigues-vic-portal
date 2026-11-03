"""Persist the last portal data so restarts do not log in to the portal."""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Final

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .models import (
    BilledConsumption,
    ContractCard,
    ContractOption,
    ContractOverview,
    DailyConsumption,
    is_valid_contract_id,
)

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION: Final = 1


def cache_key(entry_id: str) -> str:
    """Return the storage key of the data cache of a config entry."""
    return f"{DOMAIN}.{entry_id}.cache"


def serialize(overview: ContractOverview) -> dict[str, Any]:
    """Return a JSON compatible representation of a contract overview."""
    contract, card = overview.contract, overview.card
    return {
        "contract": {
            "contract_id": contract.contract_id,
            "alias": contract.alias,
            "active": contract.active,
        },
        "active": overview.active,
        "card": {
            "meter_serial": card.meter_serial,
            "meter_calibre_mm": card.meter_calibre_mm,
            "address": card.address,
            "holder": card.holder,
        },
        "daily": [
            [row.day.isoformat(), row.meter_serial, row.reading_m3, row.volume_m3]
            for row in overview.daily
        ],
        "billed": [
            [
                row.reading_date.isoformat(),
                row.days,
                row.reading_m3,
                row.volume_m3,
                row.incident,
            ]
            for row in overview.billed
        ],
    }


def deserialize(data: dict[str, Any]) -> ContractOverview:
    """Rebuild a contract overview stored by ``serialize``."""
    contract = ContractOption(**data["contract"])
    if not is_valid_contract_id(contract.contract_id):
        raise ValueError("Invalid contract id")
    return ContractOverview(
        contract=contract,
        active=data["active"],
        card=ContractCard(**data["card"]),
        daily=tuple(
            DailyConsumption(date.fromisoformat(day), serial, reading, volume)
            for day, serial, reading, volume in data["daily"]
        ),
        billed=tuple(
            BilledConsumption(date.fromisoformat(day), days, reading, volume, note)
            for day, days, reading, volume, note in data["billed"]
        ),
    )


class DataCache:
    """Store the overviews of the last successful update."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Initialize the cache."""
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, cache_key(entry_id), private=True
        )

    async def async_load(
        self,
    ) -> tuple[datetime, dict[str, ContractOverview]] | None:
        """Return the fetch time and overviews, or None if there is no cache."""
        data = await self._store.async_load()
        if not data:
            return None
        try:
            fetched = datetime.fromisoformat(data["fetched"])
            overviews: dict[str, ContractOverview] = {}
            for contract_id, raw in data["contracts"].items():
                overview = deserialize(raw)
                if contract_id != overview.contract.contract_id:
                    raise ValueError("Mismatched contract id")
                overviews[contract_id] = overview
        except (KeyError, TypeError, ValueError) as err:
            _LOGGER.debug("Ignoring invalid data cache: %s", err)
            return None
        return fetched, overviews

    async def async_save(
        self, fetched: datetime, overviews: dict[str, ContractOverview]
    ) -> None:
        """Store the overviews fetched at the given time."""
        await self._store.async_save(
            {
                "fetched": fetched.isoformat(),
                "contracts": {
                    contract_id: serialize(overview)
                    for contract_id, overview in overviews.items()
                },
            }
        )

    async def async_remove(self) -> None:
        """Remove the stored cache."""
        await self._store.async_remove()
