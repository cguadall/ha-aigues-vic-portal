"""Config flow for Aigües Vic."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import aiohttp
import probatio
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import AiguesVicClient
from .const import CONF_IMPORT_HISTORY, CONF_PRICE, DOMAIN
from .coordinator import AiguesVicConfigEntry
from .exceptions import (
    AiguesVicError,
    CannotConnectError,
    InvalidAuthError,
    RateLimitedError,
)

_LOGGER = logging.getLogger(__name__)

_EMAIL = TextSelector(TextSelectorConfig(type=TextSelectorType.EMAIL))
_PASSWORD = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


async def _async_validate(hass: HomeAssistant, email: str, password: str) -> str | None:
    """Return an error key, or ``None`` if the credentials work."""
    session = async_create_clientsession(
        hass, auto_cleanup=False, cookie_jar=aiohttp.CookieJar()
    )
    client = AiguesVicClient(session, email, password)
    try:
        if not any(c.active for c in await client.async_get_contracts()):
            return "no_contracts"
    except InvalidAuthError:
        return "invalid_auth"
    except CannotConnectError, RateLimitedError:
        return "cannot_connect"
    except AiguesVicError:
        _LOGGER.exception("Unexpected portal response")
        return "unknown"
    finally:
        await client.async_logout()
        session.detach()
    return None


class AiguesVicConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Aigües Vic."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the virtual office credentials."""
        errors: dict[str, str] = {}
        if user_input is not None:
            email = user_input[CONF_EMAIL].strip()
            await self.async_set_unique_id(email.casefold())
            self._abort_if_unique_id_configured()
            if error := await _async_validate(
                self.hass, email, user_input[CONF_PASSWORD]
            ):
                errors["base"] = error
            else:
                return self.async_create_entry(
                    title=email,
                    data={CONF_EMAIL: email, CONF_PASSWORD: user_input[CONF_PASSWORD]},
                )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                probatio.Schema(
                    {
                        probatio.Required(CONF_EMAIL): _EMAIL,
                        probatio.Required(CONF_PASSWORD): _PASSWORD,
                    }
                ),
                {CONF_EMAIL: (user_input or {}).get(CONF_EMAIL)},
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauthentication after the portal rejected the password."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new password."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            if error := await _async_validate(
                self.hass, entry.data[CONF_EMAIL], user_input[CONF_PASSWORD]
            ):
                errors["base"] = error
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=probatio.Schema({probatio.Required(CONF_PASSWORD): _PASSWORD}),
            description_placeholders={"email": entry.data[CONF_EMAIL]},
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: AiguesVicConfigEntry,
    ) -> AiguesVicOptionsFlow:
        """Return the options flow."""
        return AiguesVicOptionsFlow()


class AiguesVicOptionsFlow(OptionsFlowWithReload):
    """Handle Aigües Vic options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose the history import and the water price."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        options = self.config_entry.options
        price = NumberSelector(
            NumberSelectorConfig(
                min=0,
                max=100,
                step=0.001,
                mode=NumberSelectorMode.BOX,
                unit_of_measurement=f"{self.hass.config.currency}/m³",
            )
        )
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                probatio.Schema(
                    {
                        probatio.Required(CONF_IMPORT_HISTORY): bool,
                        probatio.Required(CONF_PRICE): price,
                    }
                ),
                {
                    CONF_IMPORT_HISTORY: options.get(CONF_IMPORT_HISTORY, False),
                    CONF_PRICE: options.get(CONF_PRICE, 0.0),
                },
            ),
        )
