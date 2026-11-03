"""Tests for the Aigües Vic config and options flows."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import patch

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.aigues_vic.const import CONF_IMPORT_HISTORY, CONF_PRICE, DOMAIN
from custom_components.aigues_vic.exceptions import (
    AiguesVicError,
    CannotConnectError,
    RateLimitedError,
)
from custom_components.aigues_vic.models import ContractOption

from .fake_portal import EMAIL, PASSWORD, FakePortal

GET_CONTRACTS = (
    "custom_components.aigues_vic.config_flow.AiguesVicClient.async_get_contracts"
)


@pytest.fixture(autouse=True)
def no_setup() -> Generator[None]:
    """Do not set up the entry created by the flow."""
    with patch("custom_components.aigues_vic.async_setup_entry", return_value=True):
        yield


async def test_user_flow(
    hass: HomeAssistant, portal_url: str, portal: FakePortal
) -> None:
    """Valid credentials create an entry and leave no portal session open."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_EMAIL: EMAIL, CONF_PASSWORD: "wrong"},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EMAIL: f" {EMAIL} ", CONF_PASSWORD: PASSWORD}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == EMAIL
    assert result["data"] == {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD}
    assert result["result"].unique_id == EMAIL
    assert "POST /accounts/logout/" in portal.paths()


@pytest.mark.parametrize(
    ("side_effect", "error"),
    [
        ([], "no_contracts"),
        ([ContractOption("1", active=False)], "no_contracts"),
        (CannotConnectError, "cannot_connect"),
        (RateLimitedError, "cannot_connect"),
        (AiguesVicError, "unknown"),
    ],
)
async def test_user_flow_errors(
    hass: HomeAssistant, side_effect: object, error: str
) -> None:
    """Errors are shown and the flow can recover."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    mock = (
        patch(GET_CONTRACTS, return_value=side_effect)
        if isinstance(side_effect, list)
        else patch(GET_CONTRACTS, side_effect=side_effect)
    )
    with mock:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}

    with patch(GET_CONTRACTS, return_value=[ContractOption("1")]):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_EMAIL: EMAIL, CONF_PASSWORD: PASSWORD}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_duplicate(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """The same account cannot be added twice."""
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EMAIL: EMAIL.upper(), CONF_PASSWORD: PASSWORD}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth(
    hass: HomeAssistant, config_entry: MockConfigEntry, portal_url: str
) -> None:
    """Reauthentication updates the password."""
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    assert result["description_placeholders"]["email"] == EMAIL

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "wrong"}
    )
    assert result["errors"] == {"base": "invalid_auth"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: PASSWORD}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data[CONF_PASSWORD] == PASSWORD


async def test_options_flow(hass: HomeAssistant, config_entry: MockConfigEntry) -> None:
    """The history and price options can be changed."""
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_IMPORT_HISTORY: True, CONF_PRICE: 1.25}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options == {CONF_IMPORT_HISTORY: True, CONF_PRICE: 1.25}
