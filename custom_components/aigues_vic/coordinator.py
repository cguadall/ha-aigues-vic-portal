"""Data update coordinator for Aigües Vic."""

from __future__ import annotations

import asyncio
import logging
import zlib
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from typing import Final

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later, async_track_time_change
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import AiguesVicClient
from .cache import DataCache
from .const import CONF_IMPORT_HISTORY, CONF_PRICE, DAILY_UPDATE_HOUR, DOMAIN
from .exceptions import AiguesVicError, InvalidAuthError
from .models import ContractOverview
from .statistics import ImportOptions, StatisticsImporter

_LOGGER = logging.getLogger(__name__)

RETRY_DELAYS: Final = (
    timedelta(minutes=30),
    timedelta(hours=1),
    timedelta(hours=2),
    timedelta(hours=4),
)
FAILURES_BEFORE_ISSUE: Final = 3

type AiguesVicConfigEntry = ConfigEntry[AiguesVicCoordinator]


def daily_update_time(entry_id: str) -> time:
    """Return the local time of the daily refresh of a config entry.

    The minute is spread per entry so installations do not query the portal
    at the same moment.
    """
    return time(DAILY_UPDATE_HOUR, zlib.crc32(entry_id.encode()) % 60)


def issue_id(kind: str, entry_id: str) -> str:
    """Return the repair issue id of a failure kind for a config entry."""
    return f"{kind}_{entry_id}"


class AiguesVicCoordinator(DataUpdateCoordinator[dict[str, ContractOverview]]):
    """Fetch every contract of one account once per update."""

    config_entry: AiguesVicConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: AiguesVicConfigEntry,
        client: AiguesVicClient,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=None,
        )
        self.client = client
        self.importer = StatisticsImporter(hass, entry.entry_id, client)
        self.cache = DataCache(hass, entry.entry_id)
        self._statistics_task: asyncio.Task[None] | None = None
        self.update_failures = 0
        self.statistics_failures = 0
        self._unsub_retry: CALLBACK_TYPE | None = None
        self.next_retry: datetime | None = None
        self.update_error: str | None = None
        self.status_signal = f"{DOMAIN}_import_status_{entry.entry_id}"
        self.importer.status_listener = self._async_status_changed
        self.update_time = daily_update_time(entry.entry_id)
        entry.async_on_unload(
            async_track_time_change(
                hass,
                self._async_daily_refresh,
                hour=self.update_time.hour,
                minute=self.update_time.minute,
                second=0,
            )
        )
        entry.async_on_unload(self._async_cancel_retry)

    async def _async_daily_refresh(self, _now: datetime) -> None:
        """Refresh daily between 06:00 and 06:59 in Home Assistant's time zone."""
        if self.config_entry.pref_disable_polling:
            return
        _LOGGER.debug("Starting scheduled daily portal refresh")
        await self.async_refresh()

    def last_scheduled_update(self) -> datetime:
        """Return the most recent scheduled refresh time, now or earlier."""
        now = dt_util.now()
        scheduled = datetime.combine(now.date(), self.update_time, now.tzinfo)
        if scheduled > now:
            scheduled = datetime.combine(
                now.date() - timedelta(days=1), self.update_time, now.tzinfo
            )
        return scheduled

    async def async_restore(self) -> bool:
        """Use cached data fetched since the last scheduled refresh, if any.

        Statistics are only imported again when the cached data has not been
        imported yet or the history option asks for a full import.
        """
        cached = await self.cache.async_load()
        if cached is None or cached[0] < self.last_scheduled_update():
            return False
        fetched, overviews = cached
        _LOGGER.debug("Using portal data cached at %s", fetched)
        self.client.prime_contracts_cache(overviews)
        self.async_set_updated_data(overviews)
        if any(
            overview.active
            and self.importer.needs_import(
                overview.contract.contract_id, self.import_options, fetched
            )
            for overview in overviews.values()
        ):
            self._async_start_statistics(list(overviews.values()))
        return True

    @property
    def import_options(self) -> ImportOptions:
        """Return the statistics import options of the config entry."""
        options = self.config_entry.options
        return ImportOptions(
            import_history=bool(options.get(CONF_IMPORT_HISTORY, False)),
            price=float(options.get(CONF_PRICE, 0.0)),
        )

    async def async_reimport(self, start: date) -> None:
        """Import statistics again from a day, after any running import."""
        if self._statistics_task is not None and not self._statistics_task.done():
            await asyncio.shield(self._statistics_task)
        overviews = list((self.data or {}).values())
        options = replace(self.import_options, start=start)
        task = self.config_entry.async_create_task(
            self.hass,
            self.importer.async_update(overviews, options),
            f"{DOMAIN} reimport {self.config_entry.entry_id}",
        )
        self._statistics_task = task
        try:
            await task
        except AiguesVicError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="reimport_failed",
                translation_placeholders={"error": type(err).__name__},
            ) from err
        finally:
            self.async_update_listeners()

    async def _async_update_data(self) -> dict[str, ContractOverview]:
        try:
            contracts = await self.client.async_get_contracts()
            overviews = {
                contract.contract_id: await self.client.async_get_overview(contract)
                for contract in contracts
            }
        except InvalidAuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="invalid_auth"
            ) from err
        except AiguesVicError as err:
            _LOGGER.debug(
                "Portal data update failed (%s)", type(err).__name__, exc_info=True
            )
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_failed",
                translation_placeholders={"error": type(err).__name__},
            ) from err

        await self.cache.async_save(dt_util.utcnow(), overviews)
        self._async_start_statistics(list(overviews.values()))
        return overviews

    @callback
    def _async_start_statistics(self, overviews: list[ContractOverview]) -> None:
        if self._statistics_task is None or self._statistics_task.done():
            self._statistics_task = self.config_entry.async_create_background_task(
                self.hass,
                self._async_update_statistics(overviews),
                f"{DOMAIN} statistics {self.config_entry.entry_id}",
            )

    @callback
    def _async_refresh_finished(self) -> None:
        """Track consecutive failures and schedule a retry after an error."""
        if self.last_update_success:
            self.update_error = None
            self.update_failures = 0
            self._async_cancel_retry()
            self._async_clear_issue("update_failed")
            return
        if isinstance(self.last_exception, ConfigEntryAuthFailed):
            return
        self.update_failures += 1
        cause = self.last_exception
        error = type(cause.__cause__ or cause).__name__ if cause else "unknown"
        self.update_error = error
        self._async_failure(self.update_failures, "update_failed", error)

    async def _async_update_statistics(self, overviews: list[ContractOverview]) -> None:
        try:
            await self.importer.async_update(overviews, self.import_options)
        except (AiguesVicError, HomeAssistantError) as err:
            _LOGGER.warning(
                "Could not update water statistics: %s (%s)", type(err).__name__, err
            )
            self.statistics_failures += 1
            self._async_failure(
                self.statistics_failures, "statistics_failed", type(err).__name__
            )
        else:
            self.statistics_failures = 0
            self._async_clear_issue("statistics_failed")
        self.async_update_listeners()

    @callback
    def _async_failure(self, failures: int, kind: str, error: str) -> None:
        if failures >= FAILURES_BEFORE_ISSUE:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id(kind, self.config_entry.entry_id),
                is_fixable=False,
                is_persistent=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=kind,
                translation_placeholders={
                    "account": self.config_entry.title,
                    "failures": str(failures),
                    "error": error,
                },
            )
        if self.config_entry.pref_disable_polling:
            return
        delay = RETRY_DELAYS[min(failures, len(RETRY_DELAYS)) - 1]
        _LOGGER.debug("Retrying portal refresh in %s", delay)
        self._async_cancel_retry()
        self._unsub_retry = async_call_later(self.hass, delay, self._async_retry)
        self.next_retry = dt_util.utcnow() + delay

    async def _async_retry(self, _now: datetime) -> None:
        self._unsub_retry = None
        self.next_retry = None
        await self.async_refresh()

    @callback
    def _async_cancel_retry(self) -> None:
        if self._unsub_retry is not None:
            self._unsub_retry()
            self._unsub_retry = None
        self.next_retry = None

    @callback
    def _async_status_changed(self) -> None:
        async_dispatcher_send(self.hass, self.status_signal)

    @callback
    def _async_clear_issue(self, kind: str) -> None:
        ir.async_delete_issue(
            self.hass, DOMAIN, issue_id(kind, self.config_entry.entry_id)
        )
