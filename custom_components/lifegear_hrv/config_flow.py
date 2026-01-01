"""Config flow for Lifegear HRV."""
from __future__ import annotations

import json
import logging
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError

from .const import (
    DOMAIN,
    CONF_USER_ID,
    CONF_AUTH_CODE,
    CONF_DEVICE_ID,
    CONF_MAC,
    API_GET_STATUS,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USER_ID): str,
        vol.Required(CONF_AUTH_CODE): str,
    }
)


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect."""
    try:
        async with aiohttp.ClientSession() as session:
            payload = f"u_id={data[CONF_USER_ID]}&AuthCode={data[CONF_AUTH_CODE]}"
            headers = {
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "*/*",
                "User-Agent": "Sunon/1.0.14",
                "Accept-Language": "zh-TW,zh-Hant;q=0.9",
            }
            async with session.post(
                API_GET_STATUS,
                data=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                text = await response.text()
                _LOGGER.debug("API Response text: %s", text)
                
                result = json.loads(text)
                
                if result and len(result) > 0:
                    device = result[0]
                    if device.get("mdid"):
                        return {
                            "title": device.get("md_wisdom", "樂奇全熱交換機"),
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
    except Exception as err:
        _LOGGER.error("Unexpected error: %s", err)
        raise CannotConnect from err


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Lifegear HRV."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                user_input[CONF_DEVICE_ID] = info[CONF_DEVICE_ID]
                user_input[CONF_MAC] = info[CONF_MAC]
                return self.async_create_entry(title=info["title"], data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""
