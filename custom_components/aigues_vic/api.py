"""Asynchronous client for the Aigües Vic virtual office REST API."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta
from typing import Any, Final

import aiohttp
from yarl import URL

from .const import API_URL, PORTAL_TIMEZONE
from .exceptions import (
    AiguesVicError,
    CannotConnectError,
    ContractNotFoundError,
    InvalidAuthError,
    ParseError,
    RateLimitedError,
    SessionExpiredError,
)
from .models import (
    BilledConsumption,
    ContractCard,
    ContractOption,
    ContractOverview,
    DailyConsumption,
    HourlyConsumption,
    is_valid_contract_id,
)

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT: Final = aiohttp.ClientTimeout(total=60)
MAX_SPAN_DAYS: Final = 360
HOURLY_SPAN_DAYS: Final = 31
MAX_RESPONSE_BYTES: Final = 8 * 1024 * 1024
MAX_REDIRECTS: Final = 5

type ChunkCallback = Callable[[date, date], None]
REQUEST_DELAY = 1.0


def portal_today() -> date:
    """Return the current date in the portal time zone."""
    return datetime.now(PORTAL_TIMEZONE).date()


async def _read_json(response: aiohttp.ClientResponse) -> Any:
    """Read response body up to size limit and decode JSON."""
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.content.iter_chunked(65536):
        total += len(chunk)
        if total > MAX_RESPONSE_BYTES:
            raise ParseError("Response too large")
        chunks.append(chunk)
    body = b"".join(chunks).decode(response.charset or "utf-8", errors="replace")
    if not body.strip():
        return None
    try:
        return json.loads(body)
    except json.JSONDecodeError as err:
        raise ParseError(f"Malformed JSON response: {err}") from err


class AiguesVicClient:
    """Read-only client for the Aigües Vic REST API."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        email: str,
        password: str,
        *,
        base_url: str | None = None,
        today: Callable[[], date] = portal_today,
        request_delay: float | None = None,
    ) -> None:
        """Initialize the client."""
        self._session = session
        self._email = email
        self._password = password
        self._base_url = (base_url or API_URL).rstrip("/")
        self._origin = URL(self._base_url).origin()
        self._today = today
        self._request_delay = REQUEST_DELAY if request_delay is None else request_delay
        self._lock = asyncio.Lock()
        self._logged_in = False
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._contracts_cache: dict[str, dict[str, Any]] = {}

    def prime_contracts_cache(self, overviews: dict[str, ContractOverview]) -> None:
        """Prime the contracts cache from restored overviews without network calls."""
        self._contracts_cache = {
            cid: {
                "contract_number": cid,
                "contract_status": {"code": "ACTIVE" if ov.active else "INACTIVE"},
                "id": ov.contract.uuid,
                "alias": ov.contract.alias,
                "meter_serial_number": ov.card.meter_serial,
                "meter_caliber": ov.card.meter_calibre_mm,
                "supply_point_address": ov.card.address,
            }
            for cid, ov in overviews.items()
        }

    async def async_get_contracts(self) -> list[ContractOption]:
        """Log in if needed and return the contracts of the account."""
        return await self._call(self._get_contracts)

    async def _get_contracts(self) -> list[ContractOption]:
        data = await self._request("GET", "/contracts/")
        if not isinstance(data, dict) or "contracts" not in data:
            raise ParseError("Missing contracts list in response")
        contracts_raw = data.get("contracts", [])
        self._contracts_cache = {
            str(c.get("contract_number")): c
            for c in contracts_raw
            if c.get("contract_number") is not None
        }
        options: list[ContractOption] = []
        for c in contracts_raw:
            contract_num = str(c.get("contract_number", ""))
            status = c.get("contract_status")
            is_active = isinstance(status, dict) and status.get("code") == "ACTIVE"
            options.append(
                ContractOption(
                    contract_id=contract_num,
                    alias=c.get("alias"),
                    active=is_active,
                    uuid=c.get("id"),
                )
            )
        return options

    async def async_get_overview(self, contract: ContractOption) -> ContractOverview:
        """Return the contract card, recent daily data and billed periods."""
        if not contract.active:
            return ContractOverview(contract, active=False)

        if not is_valid_contract_id(contract.contract_id):
            raise ContractNotFoundError("Invalid contract id")

        async def operation() -> ContractOverview:
            if contract.contract_id not in self._contracts_cache:
                await self._get_contracts()
                if contract.contract_id not in self._contracts_cache:
                    raise ContractNotFoundError("Contract not available")

            raw = self._contracts_cache[contract.contract_id]
            status = raw.get("contract_status")
            if not (isinstance(status, dict) and status.get("code") == "ACTIVE"):
                return ContractOverview(contract, active=False)

            uuid = contract.uuid or raw.get("id") or contract.contract_id
            holder = None
            address = (raw.get("supply_point_address") or "").strip() or None
            if uuid:
                try:
                    billing = await self._request(
                        "GET", f"/contracts/billing-data/{uuid}/"
                    )
                    if isinstance(billing, dict):
                        holder = billing.get("holder_name") or billing.get(
                            "tenant_name"
                        )
                        if billing.get("address"):
                            address = billing["address"].strip()
                except Exception:
                    _LOGGER.debug(
                        "Billing data not available for %s", uuid, exc_info=True
                    )

            card = ContractCard(
                meter_serial=raw.get("meter_serial_number"),
                meter_calibre_mm=raw.get("meter_caliber"),
                address=address,
                holder=holder,
            )

            start_date = self._today() - timedelta(days=30)
            daily = await self._get_daily_chunk(
                contract.contract_id, start_date, self._today()
            )
            billed = await self._get_billed(contract.contract_id)

            return ContractOverview(
                contract,
                active=True,
                card=card,
                daily=tuple(daily),
                billed=tuple(billed),
            )

        return await self._call(operation)

    async def async_get_daily(
        self,
        contract_id: str,
        start: date,
        end: date,
        on_chunk: ChunkCallback | None = None,
    ) -> list[DailyConsumption]:
        """Return daily consumption between two dates, both included."""
        if not is_valid_contract_id(contract_id):
            raise ContractNotFoundError("Invalid contract id")

        async def check_contract() -> None:
            if contract_id not in self._contracts_cache:
                await self._get_contracts()
                if contract_id not in self._contracts_cache:
                    raise ContractNotFoundError("Contract not available")
            status = self._contracts_cache[contract_id].get("contract_status")
            if not (isinstance(status, dict) and status.get("code") == "ACTIVE"):
                raise ContractNotFoundError("Contract is not active")

        await self._call(check_contract)

        result: list[DailyConsumption] = []
        for chunk_start, chunk_end in self._chunks(start, end):
            if on_chunk is not None:
                on_chunk(chunk_start, chunk_end)

            async def fetch_daily(
                s: date = chunk_start, e: date = chunk_end
            ) -> list[DailyConsumption]:
                return await self._get_daily_chunk(contract_id, s, e)

            rows = await self._call(fetch_daily)
            result.extend(row for row in rows if chunk_start <= row.day <= chunk_end)
        return result

    async def async_get_hourly(
        self,
        contract_id: str,
        start: date,
        end: date,
        on_chunk: ChunkCallback | None = None,
    ) -> list[HourlyConsumption]:
        """Return hourly consumption for local days between two dates."""
        if not is_valid_contract_id(contract_id):
            raise ContractNotFoundError("Invalid contract id")

        async def check_contract() -> None:
            if contract_id not in self._contracts_cache:
                await self._get_contracts()
                if contract_id not in self._contracts_cache:
                    raise ContractNotFoundError("Contract not available")
            status = self._contracts_cache[contract_id].get("contract_status")
            if not (isinstance(status, dict) and status.get("code") == "ACTIVE"):
                raise ContractNotFoundError("Contract is not active")

        await self._call(check_contract)

        result: list[HourlyConsumption] = []
        for chunk_start, chunk_end in self._chunks(start, end, HOURLY_SPAN_DAYS):
            if on_chunk is not None:
                on_chunk(chunk_start, chunk_end)

            async def fetch_hourly(
                s: date = chunk_start, e: date = chunk_end
            ) -> list[HourlyConsumption]:
                return await self._get_hourly_chunk(contract_id, s, e)

            rows = await self._call(fetch_hourly)
            result.extend(
                row for row in rows if chunk_start <= row.start.date() <= chunk_end
            )
        return result

    async def async_logout(self) -> None:
        """Close the portal session, ignoring errors."""
        async with self._lock:
            if not self._logged_in:
                return
            try:
                await self._request("POST", "/accounts/logout/", auth=True)
            except AiguesVicError, CannotConnectError:
                _LOGGER.debug("Logout failed", exc_info=True)
            finally:
                self._logged_in = False
                self._access_token = None
                self._refresh_token = None
                self._contracts_cache.clear()

    async def _get_billed(self, contract_id: str) -> list[BilledConsumption]:
        data = await self._request(
            "GET",
            f"/consumptions/billed-consumptions/?contract_number={contract_id}",
        )
        if not isinstance(data, list):
            return []

        parsed_rows: list[tuple[date, float | None, float, str | None]] = []
        for row in data:
            try:
                r_date = date.fromisoformat(row["timestamp"])
            except KeyError, ValueError:
                continue
            reading = float(row["reading"]) if row.get("reading") is not None else None
            vol = float(row.get("consumption", 0))
            incident = row.get("origin")
            parsed_rows.append((r_date, reading, vol, incident))

        parsed_rows.sort(key=lambda r: r[0])

        billed: list[BilledConsumption] = []
        for i, (r_date, reading, vol, incident) in enumerate(parsed_rows):
            days = (r_date - parsed_rows[i - 1][0]).days if i > 0 else None
            billed.append(
                BilledConsumption(
                    reading_date=r_date,
                    days=days,
                    reading_m3=reading,
                    volume_m3=vol,
                    incident=incident,
                )
            )
        return billed

    async def _get_daily_chunk(
        self, contract_id: str, start: date, end: date
    ) -> list[DailyConsumption]:
        url = (
            f"/consumptions/daily-consumptions/"
            f"?contract_number={contract_id}"
            f"&initial_date={start.isoformat()}"
            f"&end_date={end.isoformat()}"
        )
        data = await self._request("GET", url)
        if not isinstance(data, list):
            raise ParseError("Expected list of daily consumptions")
        rows: list[DailyConsumption] = []
        for item in data:
            try:
                ts = item["timestamp"]
                d = date.fromisoformat(ts[:10])
            except KeyError, ValueError:
                continue
            meter = item.get("meter")
            reading = (
                float(item["reading"]) if item.get("reading") is not None else None
            )
            vol_l = float(item.get("consumption", 0))
            rows.append(
                DailyConsumption(
                    day=d,
                    meter_serial=meter,
                    reading_m3=reading,
                    volume_m3=round(vol_l / 1000.0, 4),
                )
            )
        return rows

    async def _get_hourly_chunk(
        self, contract_id: str, start: date, end: date
    ) -> list[HourlyConsumption]:
        url = (
            f"/consumptions/hourly-consumptions/"
            f"?contract_number={contract_id}"
            f"&initial_date={start.isoformat()}"
            f"&end_date={end.isoformat()}"
        )
        data = await self._request("GET", url)
        if not isinstance(data, list):
            raise ParseError("Expected list of hourly consumptions")
        rows: list[HourlyConsumption] = []
        for item in data:
            try:
                ts = item["timestamp"]
                start_dt = datetime.fromisoformat(ts)
            except KeyError, ValueError:
                continue
            meter = item.get("meter")
            vol_l = float(item.get("consumption", 0))
            rows.append(
                HourlyConsumption(
                    start=start_dt,
                    meter_serial=meter,
                    volume_l=vol_l,
                )
            )
        return rows

    def _chunks(
        self, start: date, end: date, span: int = MAX_SPAN_DAYS
    ) -> list[tuple[date, date]]:
        end = min(end, self._today())
        chunks: list[tuple[date, date]] = []
        while start <= end:
            chunk_end = min(end, start + timedelta(days=span - 1))
            chunks.append((start, chunk_end))
            start = chunk_end + timedelta(days=1)
        return chunks

    async def _call[T](self, operation: Callable[[], Awaitable[T]]) -> T:
        async with self._lock:
            if self._logged_in and self._request_delay:
                await asyncio.sleep(self._request_delay)
            if not self._logged_in:
                await self._login()
            try:
                return await operation()
            except SessionExpiredError:
                _LOGGER.debug("Session expired, logging in again")
                self._logged_in = False
                await self._login()
                try:
                    return await operation()
                except SessionExpiredError as err:
                    raise CannotConnectError("Session lost after login") from err

    async def _login(self) -> None:
        self._access_token = None
        self._refresh_token = None
        self._logged_in = False
        data = await self._request(
            "POST",
            "/accounts/auth/login/",
            json_data={"email": self._email, "password": self._password},
            auth=False,
        )
        if not isinstance(data, dict) or "access" not in data:
            raise ParseError("Invalid login response")
        self._access_token = data["access"]
        self._refresh_token = data.get("refresh")
        self._logged_in = True

    async def _async_refresh(self) -> bool:
        """Attempt to refresh the access token."""
        if not self._refresh_token:
            return False
        try:
            data = await self._request(
                "POST",
                "/accounts/refresh-token/",
                json_data={"refresh": self._refresh_token},
                auth=False,
            )
            if isinstance(data, dict) and "access" in data:
                self._access_token = data["access"]
                if "refresh" in data:
                    self._refresh_token = data["refresh"]
                return True
        except Exception:
            _LOGGER.debug("Token refresh failed", exc_info=True)
        return False

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_data: Any = None,
        auth: bool = True,
        refreshed: bool = False,
    ) -> Any:
        url = f"{self._base_url}{path}"
        req_method = method
        req_json = json_data
        try:
            for _ in range(MAX_REDIRECTS + 1):
                headers = {"Accept": "application/json"}
                if auth and self._access_token:
                    headers["Authorization"] = f"Bearer {self._access_token}"

                async with self._session.request(
                    req_method,
                    url,
                    json=req_json,
                    headers=headers,
                    timeout=REQUEST_TIMEOUT,
                    allow_redirects=False,
                ) as response:
                    if response.status in (301, 302, 303, 307, 308):
                        location = response.headers.get("Location")
                        if not location:
                            raise AiguesVicError("Redirect without Location")
                        target = response.url.join(URL(location))
                        if target.origin() != self._origin:
                            raise AiguesVicError("Unexpected redirect origin")
                        url = str(target)
                        if response.status in (301, 302, 303):
                            req_method = "GET"
                            req_json = None
                        continue
                    if response.status in (401, 403, 419):
                        if auth:
                            if (
                                not refreshed
                                and self._refresh_token
                                and await self._async_refresh()
                            ):
                                return await self._request(
                                    method,
                                    path,
                                    json_data=json_data,
                                    auth=True,
                                    refreshed=True,
                                )
                            self._logged_in = False
                            self._access_token = None
                            self._refresh_token = None
                            raise SessionExpiredError
                        raise InvalidAuthError
                    if response.status == 429:
                        raise RateLimitedError
                    if response.status >= 500:
                        raise CannotConnectError(f"HTTP {response.status}")
                    if response.status >= 400:
                        raise AiguesVicError(f"Unexpected HTTP {response.status}")
                    return await _read_json(response)
            raise CannotConnectError("Too many redirects")
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CannotConnectError(type(err).__name__) from err
