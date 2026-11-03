"""Import Aigües Vic consumption into Home Assistant long-term statistics."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Final, Literal, TypedDict

from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
    statistics_during_period,
)
from homeassistant.const import UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.recorder import get_instance
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import VolumeConverter

from .api import HOURLY_SPAN_DAYS, AiguesVicClient, ChunkCallback, portal_today
from .const import (
    DOMAIN,
    HISTORY_START,
    INITIAL_IMPORT_DAYS,
    PORTAL_TIMEZONE,
    REIMPORT_DAYS,
)
from .models import ContractOverview, DailyConsumption, HourlyConsumption

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION: Final = 1
_PROBE_DAYS: Final = 7
PROBE_INTERVAL: Final = timedelta(days=7)
DAILY_SPAN_DAYS: Final = 92
NIGHT_HOURS: Final = 6
LEAK_HOURS: Final = 24
USAGE_KEYS: Final = ("night_min", "night_day", "leak", "leak_min", "leak_end")
AVERAGE_DAYS: Final = 30
CONTEXT_KEYS: Final = (
    "month",
    "month_consumption",
    "previous_month",
    "previous_month_consumption",
    "daily_average",
    "average_days",
    "data_until",
)

type Source = Literal["hourly", "daily"]
type ImportPhase = Literal["probe", "hourly", "daily", "cost"]
type Fetch[T] = Callable[[str, date, date, ChunkCallback | None], Awaitable[list[T]]]

IMPORT_STATES: Final = ("idle", "importing", "importing_history", "error")
# Imports covering more than a year are reported as history imports.
HISTORY_IMPORT_DAYS: Final = 366


@dataclass(slots=True)
class ImportStatus:
    """Live progress of the statistics import of one contract."""

    state: str = "idle"
    phase: ImportPhase | None = None
    period: str | None = None
    progress: int | None = None
    started: datetime | None = None
    last_error: str | None = None
    failures: int = 0
    total_days: int = 0
    covered_days: int = 0


class ContractState(TypedDict, total=False):
    """Persisted import state for one contract."""

    source: Source
    history: bool
    last_import: str
    probed: str
    hourly_since: str
    history_since: str
    night_min: float
    night_day: str
    leak: bool
    leak_min: float
    leak_end: str
    price: float
    month: str
    month_consumption: float
    previous_month: str
    previous_month_consumption: float
    daily_average: float
    average_days: int
    data_until: str


@dataclass(frozen=True, slots=True)
class ImportOptions:
    """How to import statistics.

    ``price`` is the water price per m³; 0 disables the cost statistic.
    ``start`` reimports from that day instead of continuing the last import;
    ``HISTORY_START`` reimports everything the portal has.
    """

    import_history: bool = False
    price: float = 0.0
    start: date | None = None


def usage_summary(rows: Sequence[HourlyConsumption], last_day: date) -> ContractState:
    """Summarize hourly rows up to the last complete day to spot leaks.

    ``night_min`` is the lowest hourly consumption (L) between 00:00 and 06:00
    of the last day. ``leak`` is true when none of the last 24 hours had zero
    consumption; ``leak_min`` is the lowest of those hours.
    """
    complete = sorted(
        (row for row in rows if row.start.date() <= last_day),
        key=lambda row: row.start,
    )
    summary: ContractState = {}
    night = [
        row.volume_l
        for row in complete
        if row.start.date() == last_day and row.start.hour < NIGHT_HOURS
    ]
    if night:
        summary["night_min"] = min(night)
        summary["night_day"] = last_day.isoformat()
    last = complete[-LEAK_HOURS:]
    if len(last) == LEAK_HOURS:
        summary["leak_min"] = min(row.volume_l for row in last)
        summary["leak"] = summary["leak_min"] > 0
        end = last[-1].start.replace(tzinfo=PORTAL_TIMEZONE) + timedelta(hours=1)
        summary["leak_end"] = end.isoformat()
    return summary


def statistic_id(contract_id: str) -> str:
    """Return the external statistic id for a contract."""
    return f"{DOMAIN}:water_{contract_id}"


def cost_statistic_id(contract_id: str) -> str:
    """Return the external cost statistic id for a contract."""
    return f"{DOMAIN}:cost_{contract_id}"


def initial_start(yesterday: date) -> date:
    """Return the first day of a new import: the previous month or 30 days."""
    previous_month = (yesterday.replace(day=1) - timedelta(days=1)).replace(day=1)
    return min(previous_month, yesterday - timedelta(days=INITIAL_IMPORT_DAYS - 1))


def storage_key(entry_id: str) -> str:
    """Return the storage key for a config entry."""
    return f"{DOMAIN}.{entry_id}"


def hourly_points(rows: Iterable[HourlyConsumption]) -> dict[datetime, float]:
    """Convert hourly rows to UTC hour starts and cubic metres.

    A repeated local time is the second occurrence of an ambiguous hour when
    clocks go back, so it is resolved with ``fold=1``.
    """
    seen: set[datetime] = set()
    points: dict[datetime, float] = {}
    for row in rows:
        local = row.start.replace(tzinfo=PORTAL_TIMEZONE, fold=row.start in seen)
        seen.add(row.start)
        start = local.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
        points[start] = points.get(start, 0.0) + row.volume_l / 1000
    return points


def daily_points(rows: Iterable[DailyConsumption]) -> dict[datetime, float]:
    """Place each day's consumption at the UTC time of local midnight."""
    points: dict[datetime, float] = {}
    for row in rows:
        start = _local_midnight(row.day)
        points[start] = points.get(start, 0.0) + row.volume_m3
    return points


def build_statistics(
    points: dict[datetime, float], base_sum: float
) -> list[StatisticData]:
    """Return ordered statistics with a running sum."""
    total = base_sum
    statistics: list[StatisticData] = []
    for start in sorted(points):
        total += points[start]
        statistics.append(
            StatisticData(start=start, state=round(points[start], 6), sum=total)
        )
    return statistics


def _local_midnight(day: date) -> datetime:
    return datetime.combine(day, time(), PORTAL_TIMEZONE).astimezone(UTC)


class StatisticsImporter:
    """Keep external water statistics up to date for each contract."""

    def __init__(
        self, hass: HomeAssistant, entry_id: str, client: AiguesVicClient
    ) -> None:
        """Initialize the importer."""
        self._hass = hass
        self._client = client
        self._store: Store[dict[str, ContractState]] = Store(
            hass, STORAGE_VERSION, storage_key(entry_id), private=True
        )
        self._state: dict[str, ContractState] | None = None
        self._status: dict[str, ImportStatus] = {}
        self.status_listener: Callable[[], None] | None = None

    def status(self, contract_id: str) -> ImportStatus:
        """Return the live import status of a contract."""
        return self._status.setdefault(contract_id, ImportStatus())

    def _notify(self) -> None:
        if self.status_listener is not None:
            self.status_listener()

    def _on_chunk(self, contract_id: str, phase: ImportPhase) -> ChunkCallback:
        """Return a callback that reports each portal request of a phase."""
        status = self.status(contract_id)

        def report(start: date, end: date) -> None:
            status.phase = phase
            status.period = f"{start.isoformat()} – {end.isoformat()}"
            if phase != "probe" and status.total_days:
                status.covered_days += (end - start).days + 1
                status.progress = min(
                    99, status.covered_days * 100 // status.total_days
                )
            self._notify()

        return report

    async def async_load(self) -> None:
        """Load the persisted import state."""
        if self._state is None:
            self._state = await self._store.async_load() or {}

    def contract_state(self, contract_id: str) -> ContractState:
        """Return the import state of a contract."""
        return (self._state or {}).get(contract_id, {})

    def needs_import(
        self, contract_id: str, options: ImportOptions, fetched: datetime
    ) -> bool:
        """Return whether data fetched at a given time still has to be imported."""
        state = self.contract_state(contract_id)
        last_import = state.get("last_import")
        return (
            "source" not in state
            or options.import_history != bool(state.get("history"))
            or options.price != state.get("price", 0.0)
            or last_import is None
            or datetime.fromisoformat(last_import) < fetched
        )

    async def async_update(
        self, overviews: Sequence[ContractOverview], options: ImportOptions
    ) -> None:
        """Import new consumption for every active contract."""
        await self.async_load()
        for overview in overviews:
            if not overview.active:
                continue
            status = self.status(overview.contract.contract_id)
            status.state, status.started = "importing", dt_util.utcnow()
            status.phase = status.period = status.progress = None
            status.total_days = status.covered_days = 0
            self._notify()
            try:
                await self._async_update_contract(overview, options)
            except Exception as err:
                _LOGGER.debug(
                    "Statistics import failed (phase=%s, period=%s)",
                    status.phase,
                    status.period,
                    exc_info=True,
                )
                status.state, status.last_error = "error", type(err).__name__
                status.failures += 1
                raise
            else:
                status.state, status.last_error, status.failures = "idle", None, 0
            finally:
                status.phase = status.period = status.progress = None
                self._notify()

    async def _async_update_contract(
        self, overview: ContractOverview, options: ImportOptions
    ) -> None:
        assert self._state is not None
        contract_id = overview.contract.contract_id
        state = self.contract_state(contract_id).copy()
        stat_id = statistic_id(contract_id)
        yesterday = portal_today() - timedelta(days=1)

        source = await self._async_source(contract_id, state, yesterday)
        full = (
            options.start is None
            and options.import_history
            and not state.get("history")
        ) or (options.start is not None and options.start <= HISTORY_START)
        last = await get_instance(self._hass).async_add_executor_job(
            get_last_statistics, self._hass, 1, stat_id, True, {"sum"}
        )
        if full:
            start, base_sum = HISTORY_START, 0.0
        elif options.start is not None or last.get(stat_id):
            if options.start is not None:
                start = max(options.start, HISTORY_START)
            else:
                last_start = datetime.fromtimestamp(last[stat_id][0]["start"], UTC)
                start = last_start.astimezone(PORTAL_TIMEZONE).date() - timedelta(
                    days=REIMPORT_DAYS
                )
            base_sum = await self._async_sum_before(stat_id, _local_midnight(start))
        else:
            start, base_sum = initial_start(yesterday), 0.0

        if start > yesterday:
            return
        status = self.status(contract_id)
        status.total_days = (yesterday - start).days + 1
        status.progress = 0
        if full or status.total_days > HISTORY_IMPORT_DAYS:
            status.state = "importing_history"
        self._notify()
        window = (_local_midnight(start), _local_midnight(yesterday + timedelta(1)))
        points: dict[datetime, float] = {}
        try:
            await self._async_points(
                contract_id, state, source, start, yesterday, points
            )
        except Exception:
            # Keep the newer days already fetched; the next import goes further back.
            points = {k: v for k, v in points.items() if window[0] <= k < window[1]}
            if points:
                partial_sum = await self._async_sum_before(stat_id, min(points))
                self._add_statistics(overview, stat_id, points, partial_sum)
            raise
        points = {k: v for k, v in points.items() if window[0] <= k < window[1]}
        self._add_statistics(overview, stat_id, points, base_sum)

        await get_instance(self._hass).async_block_till_done()
        if options.price > 0:
            status.phase, status.period = "cost", None
            self._notify()
            await self._async_update_cost(
                overview,
                points,
                window[0],
                options.price,
                rebuild=full or state.get("price") != options.price,
            )
        context = await self._async_context(stat_id, source, yesterday)
        for key in CONTEXT_KEYS:
            state.pop(key, None)  # type: ignore[misc]
        state.update(context)
        state["source"] = source
        state["price"] = options.price
        state["last_import"] = dt_util.utcnow().isoformat()
        if full and points:
            first = min(points).astimezone(PORTAL_TIMEZONE).date()
            state["history_since"] = first.isoformat()
        if full and options.import_history:
            state["history"] = True
        elif not options.import_history:
            state.pop("history", None)
        updated_state = {**self._state, contract_id: state}
        await self._store.async_save(updated_state)
        self._state = updated_state

    def _add_statistics(
        self,
        overview: ContractOverview,
        stat_id: str,
        points: dict[datetime, float],
        base_sum: float,
    ) -> None:
        if points:
            async_add_external_statistics(
                self._hass,
                self._metadata(overview, stat_id),
                build_statistics(points, base_sum),
            )
            _LOGGER.debug("Imported %s statistics", len(points))

    async def _async_update_cost(
        self,
        overview: ContractOverview,
        points: dict[datetime, float],
        start: datetime,
        price: float,
        *,
        rebuild: bool,
    ) -> None:
        """Import the cost of the points, or of all consumption on a new price."""
        contract_id = overview.contract.contract_id
        cost_id = cost_statistic_id(contract_id)
        if rebuild:
            stat_id = statistic_id(contract_id)
            stats = await get_instance(self._hass).async_add_executor_job(
                statistics_during_period,
                self._hass,
                _local_midnight(HISTORY_START),
                None,
                {stat_id},
                "hour",
                None,
                {"state"},
            )
            points = {
                datetime.fromtimestamp(row["start"], UTC): row.get("state") or 0.0
                for row in stats.get(stat_id, [])
            }
            base_sum = 0.0
        else:
            base_sum = await self._async_sum_before(cost_id, start)
        if points:
            async_add_external_statistics(
                self._hass,
                self._cost_metadata(overview, cost_id),
                build_statistics({k: v * price for k, v in points.items()}, base_sum),
            )

    async def _async_context(
        self, stat_id: str, source: Source, yesterday: date
    ) -> ContractState:
        """Summarize the imported statistics by month and recent days."""
        zone = dt_util.get_default_time_zone()
        month_start = yesterday.replace(day=1)
        previous_start = (month_start - timedelta(days=1)).replace(day=1)
        average_start = yesterday - timedelta(days=AVERAGE_DAYS - 1)
        stats = await get_instance(self._hass).async_add_executor_job(
            statistics_during_period,
            self._hass,
            datetime.combine(min(previous_start, average_start), time(), zone),
            datetime.combine(yesterday + timedelta(days=1), time(), zone),
            {stat_id},
            "day",
            None,
            {"change"},
        )
        days = {
            datetime.fromtimestamp(row["start"], zone).date(): row.get("change") or 0.0
            for row in stats.get(stat_id, [])
        }
        context: ContractState = {
            "month": month_start.strftime("%Y-%m"),
            "month_consumption": round(
                sum(v for d, v in days.items() if d >= month_start), 6
            ),
            "previous_month": previous_start.strftime("%Y-%m"),
        }
        if days and min(days) <= previous_start:
            context["previous_month_consumption"] = round(
                sum(v for d, v in days.items() if previous_start <= d < month_start),
                6,
            )
        recent = [v for d, v in days.items() if d >= average_start]
        if recent:
            context["daily_average"] = round(sum(recent) / len(recent) * 1000, 1)
            context["average_days"] = len(recent)
        last = await get_instance(self._hass).async_add_executor_job(
            get_last_statistics, self._hass, 1, stat_id, True, set()
        )
        if rows := last.get(stat_id):
            step = timedelta(hours=1) if source == "hourly" else timedelta(days=1)
            end = datetime.fromtimestamp(rows[0]["start"], UTC) + step
            context["data_until"] = end.isoformat()
        return context

    async def _async_source(
        self, contract_id: str, state: ContractState, yesterday: date
    ) -> Source:
        """Return the data source, probing for hourly data once a week.

        A meter that gains hourly data switches to it from the first complete
        hourly day; earlier days keep their daily values.
        """
        source = state.get("source")
        if source == "hourly":
            return source
        probed = state.get("probed")
        today = yesterday + timedelta(days=1)
        if (
            source is not None
            and probed is not None
            and today - date.fromisoformat(probed) < PROBE_INTERVAL
        ):
            return source
        probe = await self._client.async_get_hourly(
            contract_id,
            yesterday - timedelta(days=_PROBE_DAYS - 1),
            yesterday,
            self._on_chunk(contract_id, "probe"),
        )
        state["probed"] = today.isoformat()
        if not probe:
            return "daily"
        if source == "daily":
            since = min(row.start.date() for row in probe) + timedelta(days=1)
            state["hourly_since"] = since.isoformat()
            _LOGGER.info("Hourly data available, importing it from %s", since)
        return "hourly"

    async def _async_points(
        self,
        contract_id: str,
        state: ContractState,
        source: Source,
        start: date,
        end: date,
        points: dict[datetime, float],
    ) -> None:
        """Add consumption points from the newest day back to the start.

        Hourly meters use daily values before their hourly data starts. Points
        are added chunk by chunk, so a failure keeps the newer ones. Fetching
        stops at the first older chunk without data. The usage summary in
        ``state`` is refreshed from the hourly rows.
        """
        for key in USAGE_KEYS:
            state.pop(key, None)  # type: ignore[misc]
        daily_end = end
        found = False
        if source == "hourly":
            since_text = state.get("hourly_since")
            hourly_start = start
            if since_text:
                hourly_start = max(start, date.fromisoformat(since_text))
            rows: list[HourlyConsumption] = []

            def add_hourly(chunk: list[HourlyConsumption]) -> None:
                rows[:0] = chunk
                points.update(hourly_points(chunk))

            ended = await self._async_back(
                self._client.async_get_hourly,
                contract_id,
                hourly_start,
                end,
                HOURLY_SPAN_DAYS,
                add_hourly,
                self._on_chunk(contract_id, "hourly"),
                stop_on_empty=since_text is None,
            )
            found = bool(rows)
            daily_end = hourly_start - timedelta(days=1)
            if ended:
                first = min(row.start for row in rows)
                since = first.date() + timedelta(days=1 if first.hour else 0)
                _LOGGER.info("Hourly data starts on %s, using daily before", since)
                state["hourly_since"] = since.isoformat()
                for moment in [k for k in points if k < _local_midnight(since)]:
                    del points[moment]
                rows = [row for row in rows if row.start.date() >= since]
                daily_end = since - timedelta(days=1)
            state.update(usage_summary(rows, end))
        await self._async_back(
            self._client.async_get_daily,
            contract_id,
            start,
            daily_end,
            DAILY_SPAN_DAYS,
            lambda chunk: points.update(daily_points(chunk)),
            self._on_chunk(contract_id, "daily"),
            stop_on_empty=True,
            found=found,
        )

    async def _async_back[T](
        self,
        fetch: Fetch[T],
        contract_id: str,
        start: date,
        end: date,
        span: int,
        add: Callable[[list[T]], None],
        on_chunk: ChunkCallback,
        *,
        stop_on_empty: bool,
        found: bool = False,
    ) -> bool:
        """Fetch rows chunk by chunk from the end back to the start.

        With ``stop_on_empty``, an older chunk without data, after newer data
        was ``found``, ends the history; returns whether that happened.
        """
        chunk_end = end
        while chunk_end >= start:
            chunk_start = max(start, chunk_end - timedelta(days=span - 1))
            # Errors propagate: only an empty chunk proves there is no older data.
            chunk = await fetch(contract_id, chunk_start, chunk_end, on_chunk)
            if chunk:
                found = True
                add(chunk)
            elif stop_on_empty and found:
                return True
            chunk_end = chunk_start - timedelta(days=1)
        return False

    async def _async_sum_before(self, stat_id: str, start: datetime) -> float:
        lookback = (start - timedelta(days=31), _local_midnight(HISTORY_START))
        for since in lookback:
            stats = await get_instance(self._hass).async_add_executor_job(
                statistics_during_period,
                self._hass,
                since,
                start,
                {stat_id},
                "hour",
                None,
                {"sum"},
            )
            if rows := stats.get(stat_id):
                return float(rows[-1].get("sum") or 0.0)
        return 0.0

    def _cost_metadata(
        self, overview: ContractOverview, stat_id: str
    ) -> StatisticMetaData:
        label = overview.contract.alias or overview.contract.contract_id
        return StatisticMetaData(
            mean_type=StatisticMeanType.NONE,
            has_sum=True,
            name=f"{label} Cost de l'aigua",
            source=DOMAIN,
            statistic_id=stat_id,
            unit_class=None,
            unit_of_measurement=self._hass.config.currency,
        )

    @staticmethod
    def _metadata(overview: ContractOverview, stat_id: str) -> StatisticMetaData:
        label = overview.contract.alias or overview.contract.contract_id
        return StatisticMetaData(
            mean_type=StatisticMeanType.NONE,
            has_sum=True,
            name=f"{label} Consum d'aigua",
            source=DOMAIN,
            statistic_id=stat_id,
            unit_class=VolumeConverter.UNIT_CLASS,
            unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        )


async def async_remove_store(hass: HomeAssistant, entry_id: str) -> None:
    """Remove the persisted import state of a config entry."""
    store: Store[dict[str, Any]] = Store(
        hass, STORAGE_VERSION, storage_key(entry_id), private=True
    )
    await store.async_remove()
