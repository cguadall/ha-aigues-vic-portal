"""Button to synchronize Aigües Vic data on demand."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .coordinator import AiguesVicConfigEntry, AiguesVicCoordinator
from .entity import AiguesVicEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AiguesVicConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up a synchronize button for each active contract."""
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
            AiguesVicSyncButton(coordinator, contract_id) for contract_id in new
        )

    _add_new_contracts()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_contracts))


class AiguesVicSyncButton(AiguesVicEntity, ButtonEntity):
    """Refresh every contract of the account and its statistics now."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "synchronize"

    def __init__(self, coordinator: AiguesVicCoordinator, contract_id: str) -> None:
        """Initialize the button."""
        super().__init__(coordinator, contract_id, "synchronize")

    @property
    def available(self) -> bool:
        """Stay available after a failed update so the user can retry."""
        return self._contract_active

    async def async_press(self) -> None:
        """Synchronize with the virtual office and report failures."""
        await self.coordinator.async_refresh()
        if not self.coordinator.last_update_success:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="sync_failed"
            ) from self.coordinator.last_exception
