"""Config flow for Lifegear HRV."""
from __future__ import annotations

import json
import logging
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError

from .const import (
    DOMAIN,
    CONF_USER_ID,
    CONF_AUTH_CODE,
    CONF_DEVICE_ID,
    CONF_MAC,
    CONF_ACCOUNT,
    CONF_PASSWORD,
    CONF_LOGIN_METHOD,
    LOGIN_METHOD_CREDENTIALS,
    LOGIN_METHOD_MANUAL,
    API_GET_STATUS,
    HEADERS,
)

_LOGGER = logging.getLogger(__name__)


async def validate_manual_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate manual u_id + AuthCode input."""
    try:
        async with aiohttp.ClientSession() as session:
            payload = f"u_id={data[CONF_USER_ID]}&AuthCode={data[CONF_AUTH_CODE]}"
            async with session.post(
                API_GET_STATUS,
                data=payload,
                headers=HEADERS,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                text = await response.text()
                _LOGGER.debug("API Response text: %s", text)
                result = json.loads(text)

                if result and len(result) > 0:
                    device = result[0]
                    if device.get("mdid"):
                        return {
                            "title": device.get("md_wisdom") or "樂奇全熱交換機",
                            CONF_DEVICE_ID: str(device.get("mdid")),
                            CONF_MAC: device.get("md_mac"),
                        }
                raise InvalidAuth
    except aiohttp.ClientError as err:
        _LOGGER.error("Connection error: %s", err)
        raise CannotConnect from err
    except json.JSONDecodeError as err:
        _LOGGER.error("JSON decode error: %s", err)
        raise CannotConnect from err


async def validate_credentials(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate account + password login."""
    from .crypto import async_login

    async with aiohttp.ClientSession() as session:
        try:
            result = await async_login(
                session,
                data[CONF_ACCOUNT],
                data[CONF_PASSWORD],
            )
            return {
                "title": result.get("title", "樂奇全熱交換機"),
                CONF_USER_ID: result["u_id"],
                CONF_AUTH_CODE: result["auth_code"],
                CONF_DEVICE_ID: result[CONF_DEVICE_ID],
                CONF_MAC: result[CONF_MAC],
            }
        except ValueError as err:
            _LOGGER.error("Auth error: %s", err)
            raise InvalidAuth from err
        except ConnectionError as err:
            _LOGGER.error("Connection error: %s", err)
            raise CannotConnect from err


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Lifegear HRV."""

    VERSION = 2

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        """Get the options flow for this handler."""
        return OptionsFlowHandler()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle login method selection."""
        if user_input is not None:
            if user_input[CONF_LOGIN_METHOD] == LOGIN_METHOD_CREDENTIALS:
                return await self.async_step_credentials()
            return await self.async_step_manual()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_LOGIN_METHOD, default=LOGIN_METHOD_CREDENTIALS): vol.In(
                        {
                            LOGIN_METHOD_CREDENTIALS: "帳號密碼登入",
                            LOGIN_METHOD_MANUAL: "手動輸入 (u_id + AuthCode)",
                        }
                    ),
                }
            ),
        )

    async def async_step_credentials(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle account + password login."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                info = await validate_credentials(self.hass, user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                entry_data = {
                    CONF_LOGIN_METHOD: LOGIN_METHOD_CREDENTIALS,
                    CONF_ACCOUNT: user_input[CONF_ACCOUNT],
                    CONF_PASSWORD: user_input[CONF_PASSWORD],
                    CONF_USER_ID: info[CONF_USER_ID],
                    CONF_AUTH_CODE: info[CONF_AUTH_CODE],
                    CONF_DEVICE_ID: info[CONF_DEVICE_ID],
                    CONF_MAC: info[CONF_MAC],
                }
                return self.async_create_entry(title=info["title"], data=entry_data)

        return self.async_show_form(
            step_id="credentials",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ACCOUNT): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle manual u_id + AuthCode input."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                info = await validate_manual_input(self.hass, user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                entry_data = {
                    CONF_LOGIN_METHOD: LOGIN_METHOD_MANUAL,
                    CONF_USER_ID: user_input[CONF_USER_ID],
                    CONF_AUTH_CODE: user_input[CONF_AUTH_CODE],
                    CONF_DEVICE_ID: info[CONF_DEVICE_ID],
                    CONF_MAC: info[CONF_MAC],
                }
                return self.async_create_entry(title=info["title"], data=entry_data)

        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_USER_ID): str,
                    vol.Required(CONF_AUTH_CODE): str,
                }
            ),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle reconfiguration."""
        errors: dict[str, str] = {}
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        is_credentials = entry.data.get(CONF_LOGIN_METHOD) == LOGIN_METHOD_CREDENTIALS

        if user_input is not None:
            try:
                if is_credentials:
                    info = await validate_credentials(self.hass, user_input)
                    new_data = {
                        CONF_LOGIN_METHOD: LOGIN_METHOD_CREDENTIALS,
                        CONF_ACCOUNT: user_input[CONF_ACCOUNT],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_USER_ID: info[CONF_USER_ID],
                        CONF_AUTH_CODE: info[CONF_AUTH_CODE],
                        CONF_DEVICE_ID: info[CONF_DEVICE_ID],
                        CONF_MAC: info[CONF_MAC],
                    }
                else:
                    info = await validate_manual_input(self.hass, user_input)
                    new_data = {
                        CONF_LOGIN_METHOD: LOGIN_METHOD_MANUAL,
                        CONF_USER_ID: user_input[CONF_USER_ID],
                        CONF_AUTH_CODE: user_input[CONF_AUTH_CODE],
                        CONF_DEVICE_ID: info[CONF_DEVICE_ID],
                        CONF_MAC: info[CONF_MAC],
                    }
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                self.hass.config_entries.async_update_entry(entry, data=new_data)
                await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="reconfigure_successful")

        if is_credentials:
            schema = vol.Schema(
                {
                    vol.Required(CONF_ACCOUNT, default=entry.data.get(CONF_ACCOUNT, "")): str,
                    vol.Required(CONF_PASSWORD, default=entry.data.get(CONF_PASSWORD, "")): str,
                }
            )
        else:
            schema = vol.Schema(
                {
                    vol.Required(CONF_USER_ID, default=entry.data.get(CONF_USER_ID, "")): str,
                    vol.Required(CONF_AUTH_CODE, default=entry.data.get(CONF_AUTH_CODE, "")): str,
                }
            )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=schema,
            errors=errors,
        )


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        errors: dict[str, str] = {}
        is_credentials = self.config_entry.data.get(CONF_LOGIN_METHOD) == LOGIN_METHOD_CREDENTIALS

        if user_input is not None:
            try:
                if is_credentials:
                    info = await validate_credentials(self.hass, user_input)
                    new_data = {
                        CONF_LOGIN_METHOD: LOGIN_METHOD_CREDENTIALS,
                        CONF_ACCOUNT: user_input[CONF_ACCOUNT],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_USER_ID: info[CONF_USER_ID],
                        CONF_AUTH_CODE: info[CONF_AUTH_CODE],
                        CONF_DEVICE_ID: info[CONF_DEVICE_ID],
                        CONF_MAC: info[CONF_MAC],
                    }
                else:
                    data = {
                        CONF_USER_ID: user_input[CONF_USER_ID],
                        CONF_AUTH_CODE: user_input[CONF_AUTH_CODE],
                    }
                    info = await validate_manual_input(self.hass, data)
                    new_data = {
                        CONF_LOGIN_METHOD: LOGIN_METHOD_MANUAL,
                        CONF_USER_ID: user_input[CONF_USER_ID],
                        CONF_AUTH_CODE: user_input[CONF_AUTH_CODE],
                        CONF_DEVICE_ID: info[CONF_DEVICE_ID],
                        CONF_MAC: info[CONF_MAC],
                    }
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                self.hass.config_entries.async_update_entry(
                    self.config_entry, data=new_data
                )
                return self.async_create_entry(title="", data={})

        if is_credentials:
            schema = vol.Schema(
                {
                    vol.Required(CONF_ACCOUNT, default=self.config_entry.data.get(CONF_ACCOUNT, "")): str,
                    vol.Required(CONF_PASSWORD, default=self.config_entry.data.get(CONF_PASSWORD, "")): str,
                }
            )
        else:
            schema = vol.Schema(
                {
                    vol.Required(CONF_USER_ID, default=self.config_entry.data.get(CONF_USER_ID, "")): str,
                    vol.Required(CONF_AUTH_CODE, default=self.config_entry.data.get(CONF_AUTH_CODE, "")): str,
                }
            )

        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            errors=errors,
        )


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""
