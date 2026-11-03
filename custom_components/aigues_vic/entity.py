"""Base entity for Aigües Vic contracts."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, PORTAL_URL
from .coordinator import AiguesVicCoordinator
from .models import ContractOverview


class AiguesVicEntity(CoordinatorEntity[AiguesVicCoordinator]):
    """An entity attached to the device of one contract."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: AiguesVicCoordinator, contract_id: str, key: str
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._contract_id = contract_id
        self._attr_unique_id = f"{contract_id}_{key}"
        overview = coordinator.data[contract_id]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, contract_id)},
            name=overview.contract.alias or contract_id,
            manufacturer="Aigües Vic",
            model=f"{overview.card.meter_calibre_mm} mm"
            if overview.card.meter_calibre_mm
            else None,
            serial_number=overview.card.meter_serial,
            configuration_url=PORTAL_URL,
        )

    @property
    def _overview(self) -> ContractOverview | None:
        return (self.coordinator.data or {}).get(self._contract_id)

    @property
    def _contract_active(self) -> bool:
        overview = self._overview
        return overview is not None and overview.active
