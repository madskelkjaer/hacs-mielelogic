"""Config flow for the MieleLogic integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    MieleLogicApiClient,
    MieleLogicAuthError,
    MieleLogicConnectionError,
)
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_EXPIRES_AT,
    CONF_REFRESH_TOKEN,
    DOMAIN,
    LOGGER,
)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class MieleLogicConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for MieleLogic."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step: ask for credentials and validate them."""
        errors: dict[str, str] = {}

        if user_input is not None:
            username = user_input[CONF_USERNAME]
            password = user_input[CONF_PASSWORD]

            # Avoid configuring the same account twice.
            await self.async_set_unique_id(username.lower())
            self._abort_if_unique_id_configured()

            session = async_get_clientsession(self.hass)
            client = MieleLogicApiClient(session)

            try:
                tokens = await client.async_login(username, password)
            except MieleLogicAuthError:
                errors["base"] = "invalid_auth"
            except MieleLogicConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001 - surface as generic error to the UI
                LOGGER.exception("Unexpected error during MieleLogic login")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(
                    title=username,
                    data={
                        CONF_USERNAME: username,
                        CONF_PASSWORD: password,
                        CONF_ACCESS_TOKEN: tokens["access_token"],
                        CONF_REFRESH_TOKEN: tokens["refresh_token"],
                        CONF_EXPIRES_AT: tokens["expires_at"],
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )
