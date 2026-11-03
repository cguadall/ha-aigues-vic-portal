"""Binary sensors for Aigües Vic contracts."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.components.binary_sensor.const import BinarySensorDeviceClass
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import AiguesVicConfigEntry, AiguesVicCoordinator
from .entity import AiguesVicEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AiguesVicConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up a possible leak sensor for each active contract."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _add_new_contracts() -> None:
        new = [
            contract_id
            for contract_id, overview in (coordinator.data or {}).items()
            if overview.active and contract_id not in known
        ]
        known.update(new)
        async_add_entities(
            AiguesVicLeakSensor(coordinator, contract_id) for contract_id in new
        )

    _add_new_contracts()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_contracts))


class AiguesVicLeakSensor(AiguesVicEntity, BinarySensorEntity):
    """On when water was used in every one of the last 24 hours with data."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_translation_key = "possible_leak"

    def __init__(self, coordinator: AiguesVicCoordinator, contract_id: str) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator, contract_id, "possible_leak")

    @property
    def available(self) -> bool:
        """Return whether the contract is still reported as active."""
        return super().available and self._contract_active

    @property
    def is_on(self) -> bool | None:
        """Return whether a continuous flow was detected; unknown without data."""
        return self.coordinator.importer.contract_state(self._contract_id).get("leak")

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the lowest hourly consumption and the end of the window."""
        state = self.coordinator.importer.contract_state(self._contract_id)
        if "leak" not in state:
            return None
        return {"minimum": state.get("leak_min"), "until": state.get("leak_end")}
