"""Data models for the Aigües Vic virtual office."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Final

_CONTRACT_ID: Final = re.compile(r"^\d{1,12}$")


def is_valid_contract_id(contract_id: str) -> bool:
    """Return whether a contract identifier has the expected numeric format."""
    return _CONTRACT_ID.fullmatch(contract_id) is not None


@dataclass(frozen=True, slots=True)
class ContractOption:
    """A contract of the account and whether the portal lists it as active."""

    contract_id: str
    alias: str | None = None
    active: bool = True
    uuid: str | None = None


@dataclass(frozen=True, slots=True)
class ContractCard:
    """Details shown for the active contract."""

    meter_serial: str | None = None
    meter_calibre_mm: int | None = None
    address: str | None = None
    holder: str | None = None


@dataclass(frozen=True, slots=True)
class DailyConsumption:
    """One daily consumption record.

    ``reading_m3`` is the meter reading at the end of the day.
    """

    day: date
    meter_serial: str | None
    reading_m3: float | None
    volume_m3: float


@dataclass(frozen=True, slots=True)
class BilledConsumption:
    """One row of the billed consumption table."""

    reading_date: date
    days: int | None
    reading_m3: float | None
    volume_m3: float
    incident: str | None


@dataclass(frozen=True, slots=True)
class HourlyConsumption:
    """One hourly consumption row; ``start`` is naive portal local time."""

    start: datetime
    meter_serial: str | None
    volume_l: float


@dataclass(frozen=True, slots=True)
class ContractOverview:
    """Current data for one contract."""

    contract: ContractOption
    active: bool
    card: ContractCard = field(default_factory=ContractCard)
    daily: tuple[DailyConsumption, ...] = ()
    billed: tuple[BilledConsumption, ...] = ()
