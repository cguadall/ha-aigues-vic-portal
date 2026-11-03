"""Sensors for Aigües Vic contracts."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import EntityCategory, UnitOfVolume, UnitOfVolumeFlowRate
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .api import portal_today
from .coordinator import AiguesVicConfigEntry, AiguesVicCoordinator
from .entity import AiguesVicEntity
from .models import BilledConsumption, ContractOverview, DailyConsumption
from .statistics import IMPORT_STATES, ContractState

PARALLEL_UPDATES = 0


def latest_complete_day(overview: ContractOverview) -> DailyConsumption | None:
    """Return the latest daily row before today; today is still partial."""
    today = portal_today()
    days = [row for row in overview.daily if row.day < today]
    return days[-1] if days else None


def latest_reading(overview: ContractOverview) -> tuple[date, float] | None:
    """Return the most recent meter reading from daily or billed data."""
    today = portal_today()
    candidates = [
        (row.day, row.reading_m3)
        for row in overview.daily
        if row.day < today and row.reading_m3 is not None
    ] + [
        (row.reading_date, row.reading_m3)
        for row in overview.billed
        if row.reading_m3 is not None
    ]
    return max(candidates, key=lambda item: item[0]) if candidates else None


def latest_billed(overview: ContractOverview) -> BilledConsumption | None:
    """Return the most recent billed period, skipping the initial reading."""
    periods = [row for row in overview.billed if row.days is not None]
    return periods[-1] if periods else None


@dataclass(frozen=True, kw_only=True)
class AiguesVicSensorDescription(SensorEntityDescription):
    """Describe an Aigües Vic sensor."""

    value_fn: Callable[
        [ContractOverview, ContractState], float | int | date | datetime | None
    ]
    attributes_fn: Callable[
        [ContractOverview, ContractState], dict[str, Any] | None
    ] = lambda _overview, _imported: None


def _reading(
    overview: ContractOverview, _imported: ContractState
) -> int | float | None:
    reading = latest_reading(overview)
    if not reading:
        return None
    val = reading[1]
    return int(val) if isinstance(val, (int, float)) and val.is_integer() else val


def _reading_date(overview: ContractOverview, _imported: ContractState) -> date | None:
    reading = latest_reading(overview)
    return reading[0] if reading else None


def _last_day(overview: ContractOverview, _imported: ContractState) -> float | None:
    row = latest_complete_day(overview)
    return round(row.volume_m3 * 1000, 1) if row else None


def _last_billed(overview: ContractOverview, _imported: ContractState) -> float | None:
    row = latest_billed(overview)
    return row.volume_m3 if row else None


def _last_import(
    _overview: ContractOverview, imported: ContractState
) -> datetime | None:
    value = imported.get("last_import")
    return dt_util.parse_datetime(value) if value else None


def _night_min(_overview: ContractOverview, imported: ContractState) -> float | None:
    return imported.get("night_min")


def _night_attributes(
    _overview: ContractOverview, imported: ContractState
) -> dict[str, Any] | None:
    day = imported.get("night_day")
    return {"day": day} if day else None


def _last_day_attributes(
    overview: ContractOverview, _imported: ContractState
) -> dict[str, Any] | None:
    row = latest_complete_day(overview)
    return {"day": row.day.isoformat()} if row else None


def _state_value(key: str) -> Callable[[ContractOverview, ContractState], Any]:
    return lambda _overview, imported: imported.get(key)


def _state_attribute(
    name: str, key: str
) -> Callable[[ContractOverview, ContractState], dict[str, Any] | None]:
    def attributes(
        _overview: ContractOverview, imported: ContractState
    ) -> dict[str, Any] | None:
        value = imported.get(key)
        return {name: value} if value is not None else None

    return attributes


def _data_until(
    _overview: ContractOverview, imported: ContractState
) -> datetime | None:
    value = imported.get("data_until")
    return dt_util.parse_datetime(value) if value else None


def _billed_attributes(
    overview: ContractOverview, _imported: ContractState
) -> dict[str, Any] | None:
    row = latest_billed(overview)
    if row is None:
        return None
    return {"period_end": row.reading_date.isoformat(), "days": row.days}


SENSORS: tuple[AiguesVicSensorDescription, ...] = (
    AiguesVicSensorDescription(
        key="meter_reading",
        translation_key="meter_reading",
        device_class=SensorDeviceClass.WATER,
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        suggested_display_precision=0,
        value_fn=_reading,
    ),
    AiguesVicSensorDescription(
        key="reading_date",
        translation_key="reading_date",
        device_class=SensorDeviceClass.DATE,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_reading_date,
    ),
    AiguesVicSensorDescription(
        key="last_day_consumption",
        translation_key="last_day_consumption",
        device_class=SensorDeviceClass.WATER,
        native_unit_of_measurement=UnitOfVolume.LITERS,
        suggested_display_precision=0,
        value_fn=_last_day,
        attributes_fn=_last_day_attributes,
    ),
    AiguesVicSensorDescription(
        key="night_minimum",
        translation_key="night_minimum",
        device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
        native_unit_of_measurement=UnitOfVolumeFlowRate.LITERS_PER_HOUR,
        suggested_display_precision=1,
        value_fn=_night_min,
        attributes_fn=_night_attributes,
    ),
    AiguesVicSensorDescription(
        key="month_consumption",
        translation_key="month_consumption",
        device_class=SensorDeviceClass.WATER,
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        suggested_display_precision=2,
        value_fn=_state_value("month_consumption"),
        attributes_fn=_state_attribute("month", "month"),
    ),
    AiguesVicSensorDescription(
        key="previous_month_consumption",
        translation_key="previous_month_consumption",
        device_class=SensorDeviceClass.WATER,
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        suggested_display_precision=2,
        value_fn=_state_value("previous_month_consumption"),
        attributes_fn=_state_attribute("month", "previous_month"),
    ),
    AiguesVicSensorDescription(
        key="daily_average",
        translation_key="daily_average",
        device_class=SensorDeviceClass.WATER,
        native_unit_of_measurement=UnitOfVolume.LITERS,
        suggested_display_precision=0,
        value_fn=_state_value("daily_average"),
        attributes_fn=_state_attribute("days", "average_days"),
    ),
    AiguesVicSensorDescription(
        key="last_billed_consumption",
        translation_key="last_billed_consumption",
        device_class=SensorDeviceClass.WATER,
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        suggested_display_precision=0,
        value_fn=_last_billed,
        attributes_fn=_billed_attributes,
    ),
    AiguesVicSensorDescription(
        key="data_until",
        translation_key="data_until",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_data_until,
    ),
    AiguesVicSensorDescription(
        key="last_import",
        translation_key="last_import",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_last_import,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AiguesVicConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors for active contracts, including ones added later."""
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
        entities: list[SensorEntity] = [
            AiguesVicSensor(coordinator, contract_id, description)
            for contract_id in new
            for description in SENSORS
        ]
        entities.extend(
            AiguesVicImportStatusSensor(coordinator, contract_id) for contract_id in new
        )
        async_add_entities(entities)

    _add_new_contracts()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_contracts))


class AiguesVicSensor(AiguesVicEntity, SensorEntity):
    """A value derived from one contract's portal data."""

    entity_description: AiguesVicSensorDescription

    def __init__(
        self,
        coordinator: AiguesVicCoordinator,
        contract_id: str,
        description: AiguesVicSensorDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, contract_id, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        """Return whether the contract is still reported as active."""
        return super().available and self._contract_active

    @property
    def native_value(self) -> float | int | date | datetime | None:
        """Return the sensor value."""
        overview = self._overview
        if overview is None:
            return None
        imported = self.coordinator.importer.contract_state(self._contract_id)
        return self.entity_description.value_fn(overview, imported)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra attributes."""
        overview = self._overview
        if overview is None:
            return None
        imported = self.coordinator.importer.contract_state(self._contract_id)
        return self.entity_description.attributes_fn(overview, imported)


class AiguesVicImportStatusSensor(AiguesVicEntity, SensorEntity):
    """Whether the statistics of a contract are up to date or being imported."""

    _attr_translation_key = "import_status"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(IMPORT_STATES)
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _unrecorded_attributes = frozenset({"phase", "period", "progress"})

    def __init__(self, coordinator: AiguesVicCoordinator, contract_id: str) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, contract_id, "import_status")

    async def async_added_to_hass(self) -> None:
        """Follow the import progress between coordinator updates."""
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, self.coordinator.status_signal, self.async_write_ha_state
            )
        )

    @property
    def available(self) -> bool:
        """Stay available after a failed update to report the error."""
        return self._contract_active

    @property
    def native_value(self) -> str:
        """Return the import state."""
        status = self.coordinator.importer.status(self._contract_id)
        if status.state == "idle" and self.coordinator.update_error is not None:
            return "error"
        return status.state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the progress and the outcome of the imports."""
        coordinator = self.coordinator
        status = coordinator.importer.status(self._contract_id)
        imported = coordinator.importer.contract_state(self._contract_id)
        if status.state == "error":
            last_error, failures = status.last_error, status.failures
        else:
            last_error, failures = coordinator.update_error, coordinator.update_failures
        last_import = imported.get("last_import")
        return {
            "phase": status.phase,
            "period": status.period,
            "progress": status.progress,
            "started": status.started,
            "last_success": dt_util.parse_datetime(last_import)
            if last_import
            else None,
            "last_error": last_error,
            "failures": failures,
            "next_retry": coordinator.next_retry,
            "history_complete": bool(imported.get("history")),
            "hourly_since": imported.get("hourly_since"),
            "history_since": imported.get("history_since"),
        }
