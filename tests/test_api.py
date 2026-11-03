"""Tests for the portal client against the fake portal."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import date, timedelta
from unittest.mock import patch

import aiohttp
import pytest

from custom_components.aigues_vic.api import AiguesVicClient
from custom_components.aigues_vic.exceptions import (
    AiguesVicError,
    CannotConnectError,
    ContractNotFoundError,
    InvalidAuthError,
    ParseError,
    RateLimitedError,
)
from custom_components.aigues_vic.models import ContractOption

from .fake_portal import EMAIL, PASSWORD, FakePortal

ACTIVE = ContractOption("100000001", "Casa")
INACTIVE = ContractOption("200000002", None, active=False)


@pytest.fixture
async def session() -> AsyncGenerator[aiohttp.ClientSession]:
    """Return a client session with its own cookie jar."""
    async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as s:
        yield s


@pytest.fixture
def client(session: aiohttp.ClientSession, portal_url: str) -> AiguesVicClient:
    """Return a client for the fake portal."""
    return AiguesVicClient(session, EMAIL, PASSWORD)


async def test_contracts_and_overview(
    client: AiguesVicClient, portal: FakePortal
) -> None:
    """The client logs in, lists contracts and reads both contract states."""
    assert await client.async_get_contracts() == [ACTIVE, INACTIVE]
    overview = await client.async_get_overview(ACTIVE)
    assert overview.active
    assert overview.card.meter_serial == "A12BC34567D"
    assert len(overview.daily) == 31
    assert overview.daily[-1].day == portal.today
    assert [b.days for b in overview.billed] == [None, 92, 92]

    requests = len(portal.requests)
    inactive = await client.async_get_overview(INACTIVE)
    assert not inactive.active
    assert inactive.daily == ()
    assert len(portal.requests) == requests
    assert portal.paths().count("POST /accounts/auth/login/") == 1

    await client.async_logout()
    assert portal.paths()[-1] == "POST /accounts/logout/"
    await client.async_logout()
    assert portal.paths().count("POST /accounts/logout/") == 1


async def test_fresh_session_jwt_auth(
    client: AiguesVicClient,
    session: aiohttp.ClientSession,
    portal: FakePortal,
) -> None:
    """A fresh session logs in with JSON credentials and uses Bearer authorization."""
    assert await client.async_get_contracts() == [ACTIVE, INACTIVE]
    assert portal.paths() == [
        "POST /accounts/auth/login/",
        "GET /contracts/",
    ]
    login_req = portal.requests[0]
    assert login_req[2] == {"email": EMAIL, "password": PASSWORD}

    assert "GET /contracts/" in portal.paths()

    await client.async_logout()
    assert portal.paths()[-1] == "POST /accounts/logout/"
    assert not portal.access_tokens


async def test_token_refresh(client: AiguesVicClient, portal: FakePortal) -> None:
    """When an access token expires, it is refreshed transparently."""
    await client.async_get_contracts()
    # Invalidate access token while keeping refresh token intact
    portal.access_tokens.clear()
    assert (await client.async_get_overview(ACTIVE)).active
    assert portal.paths().count("POST /accounts/refresh-token/") == 1
    assert portal.paths().count("POST /accounts/auth/login/") == 1


async def test_invalid_auth(client: AiguesVicClient, portal: FakePortal) -> None:
    """Invalid credentials raise InvalidAuthError and allow a subsequent retry."""
    portal.password = "other"
    with pytest.raises(InvalidAuthError):
        await client.async_get_contracts()
    assert portal.paths() == ["POST /accounts/auth/login/"]
    assert not portal.access_tokens

    portal.password = PASSWORD
    assert await client.async_get_contracts() == [ACTIVE, INACTIVE]
    assert portal.paths() == [
        "POST /accounts/auth/login/",
        "POST /accounts/auth/login/",
        "GET /contracts/",
    ]
    await client.async_logout()


async def test_session_expiry_relogin(
    client: AiguesVicClient, portal: FakePortal
) -> None:
    """An expired session and expired refresh token triggers a full relogin."""
    await client.async_get_contracts()
    portal.expire_sessions = True
    assert (await client.async_get_overview(ACTIVE)).active
    assert portal.paths().count("POST /accounts/auth/login/") == 2


async def test_session_lost_after_login(
    client: AiguesVicClient, portal: FakePortal
) -> None:
    """A session that is lost again right after login is a connection error."""
    await client.async_get_contracts()
    portal.lose_session_on_consumption = True
    with pytest.raises(CannotConnectError):
        await client.async_get_overview(ACTIVE)
    assert portal.paths().count("POST /accounts/auth/login/") == 2


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (429, RateLimitedError),
        (500, CannotConnectError),
        (404, AiguesVicError),
    ],
)
async def test_http_errors(
    client: AiguesVicClient,
    portal: FakePortal,
    status: int,
    error: type[Exception],
) -> None:
    """HTTP errors are mapped to integration errors."""
    portal.fail_status = status
    with pytest.raises(error):
        await client.async_get_contracts()


async def test_connection_error(
    session: aiohttp.ClientSession, socket_enabled: None
) -> None:
    """A refused connection is a connection error."""
    client = AiguesVicClient(session, EMAIL, PASSWORD, base_url="http://127.0.0.1:9")
    with pytest.raises(CannotConnectError):
        await client.async_get_contracts()


async def test_missing_login_token(client: AiguesVicClient, portal: FakePortal) -> None:
    """A login response without an access token cannot be used."""
    portal.drop_login_token = True
    with pytest.raises(ParseError):
        await client.async_get_contracts()


async def test_daily_chunks(client: AiguesVicClient, portal: FakePortal) -> None:
    """Long ranges are split into chunks."""
    today = portal.today
    start = today - timedelta(days=400)
    end = today + timedelta(days=5)
    rows = await client.async_get_daily(ACTIVE.contract_id, start, end)
    assert rows[0].day == today - timedelta(days=60)
    assert rows[-1].day == today
    daily_requests = [
        (path, params["initial_date"], params["end_date"])
        for method, path, params in portal.requests
        if path == "/consumptions/daily-consumptions/"
    ]
    assert len(daily_requests) == 2


async def test_hourly(client: AiguesVicClient, portal: FakePortal) -> None:
    """Hourly rows are limited to the requested local days."""
    day = portal.today - timedelta(days=1)
    rows = await client.async_get_hourly(ACTIVE.contract_id, day, day)
    assert len(rows) == 24
    assert {row.start.date() for row in rows} == {day}
    assert (
        await client.async_get_hourly(ACTIVE.contract_id, day, day - timedelta(1)) == []
    )


async def test_ranged_inactive(client: AiguesVicClient) -> None:
    """Ranged queries for an inactive contract fail."""
    with pytest.raises(ContractNotFoundError):
        await client.async_get_daily(
            INACTIVE.contract_id, date(2026, 1, 1), date(2026, 1, 2)
        )


@pytest.mark.parametrize("contract_id", ["../x", "999999999"])
async def test_unknown_contract(client: AiguesVicClient, contract_id: str) -> None:
    """Invalid or unknown contracts are rejected."""
    with pytest.raises(ContractNotFoundError):
        await client.async_get_overview(ContractOption(contract_id, None))


async def test_logout_errors_ignored(
    client: AiguesVicClient, portal: FakePortal
) -> None:
    """Logout failures are ignored."""
    await client.async_get_contracts()
    portal.fail_status = 500
    await client.async_logout()


async def test_redirect_security_and_response_limits(
    client: AiguesVicClient, session: aiohttp.ClientSession
) -> None:
    """Cross-origin redirects, redirect loops and oversized bodies fail safely."""

    class RedirectApp:
        def __init__(self, target_status: int, location: str | None = None) -> None:
            self.app = aiohttp.web.Application()
            self.app.add_routes(
                [aiohttp.web.route("*", "/accounts/auth/login/", self._redir)]
            )
            self.target_status = target_status
            self.location = location

        async def _redir(self, _request: aiohttp.web.Request) -> aiohttp.web.Response:
            headers = {"Location": self.location} if self.location else {}
            return aiohttp.web.Response(status=self.target_status, headers=headers)

    from aiohttp.test_utils import TestServer

    # 1. Cross-origin redirect
    server = TestServer(RedirectApp(302, "https://evil.example/login").app)
    await server.start_server()
    c = AiguesVicClient(
        session, EMAIL, PASSWORD, base_url=f"http://127.0.0.1:{server.port}"
    )
    with pytest.raises(AiguesVicError, match="Unexpected redirect origin"):
        await c.async_get_contracts()
    await server.close()

    # 2. Redirect without location
    server = TestServer(RedirectApp(302, None).app)
    await server.start_server()
    c = AiguesVicClient(
        session, EMAIL, PASSWORD, base_url=f"http://127.0.0.1:{server.port}"
    )
    with pytest.raises(AiguesVicError, match="Redirect without Location"):
        await c.async_get_contracts()
    await server.close()

    # 3. Redirect loop
    server = TestServer(RedirectApp(302, "/accounts/auth/login/").app)
    await server.start_server()
    c = AiguesVicClient(
        session, EMAIL, PASSWORD, base_url=f"http://127.0.0.1:{server.port}"
    )
    with pytest.raises(CannotConnectError, match="Too many redirects"):
        await c.async_get_contracts()
    await server.close()

    # 4. Oversized response body
    with patch("custom_components.aigues_vic.api.MAX_RESPONSE_BYTES", 32):
        with pytest.raises(ParseError, match="Response too large"):
            await client.async_get_contracts()
