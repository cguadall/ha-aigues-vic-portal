"""Shared fixtures for the Aigües Vic tests."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Generator
from functools import partial
from unittest.mock import patch

import aiohttp
import pytest
from aiohttp.test_utils import TestServer
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.aigues_vic.api import portal_today
from custom_components.aigues_vic.const import DOMAIN

from .fake_portal import EMAIL, PASSWORD, FakePortal, sample_contract


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(request: pytest.FixtureRequest) -> None:
    """Provide the recorder and enable custom integrations in Home Assistant tests."""
    if "hass" in request.fixturenames:
        request.getfixturevalue("recorder_mock")
        request.getfixturevalue("enable_custom_integrations")


@pytest.fixture(autouse=True)
def no_request_delay() -> Generator[None]:
    """Do not throttle requests to the fake portal."""
    with patch("custom_components.aigues_vic.api.REQUEST_DELAY", 0):
        yield


@pytest.fixture
def portal() -> FakePortal:
    """Return a fake portal with one active and one inactive contract."""
    today = portal_today()
    inactive = sample_contract("200000002", today, days=3)
    inactive.active = False
    return FakePortal(
        [sample_contract("100000001", today, alias="Casa"), inactive], today
    )


@pytest.fixture
async def portal_url(portal: FakePortal, socket_enabled: None) -> AsyncGenerator[str]:
    """Serve the fake portal on localhost and point the client to it."""
    server = TestServer(portal.app, host="127.0.0.1", access_log=None)
    await server.start_server()
    url = f"http://127.0.0.1:{server.port}"
    with (
        patch("custom_components.aigues_vic.api.API_URL", url),
        patch("aiohttp.CookieJar", partial(aiohttp.CookieJar, unsafe=True)),
    ):
        yield url
    await server.close()


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """Return a config entry for the fake account."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=EMAIL,
        unique_id=EMAIL,
        data={CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD},
    )


@pytest.fixture
async def setup_integration(
    hass: HomeAssistant, config_entry: MockConfigEntry, portal_url: str
) -> MockConfigEntry:
    """Set up the integration against the fake portal."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry
