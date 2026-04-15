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
    CONF_LOCAL_SERVER,
    CONF_DEVICE_MODEL,
    LOGIN_METHOD_CREDENTIALS,
    LOGIN_METHOD_MANUAL,
    LOGIN_METHOD_LOCAL,
    DEVICE_MODEL_M8,
    DEVICE_MODEL_M8E,
    DEVICE_MODEL_BATH_HEATER,
    DEVICE_MODEL_M8E_SENSOR,
    HEADERS,
    get_api_urls,
    is_m8e_platform,
    detect_device_model,
)

_LOGGER = logging.getLogger(__name__)


async def validate_manual_input(
    hass: HomeAssistant, data: dict[str, Any], model: str = DEVICE_MODEL_M8
) -> dict[str, Any]:
    """Validate manual u_id + AuthCode input."""
    urls = get_api_urls(model)
    try:
        async with aiohttp.ClientSession() as session:
            # M8-E platform: use getDeviceList to get all devices
            if is_m8e_platform(model):
                from .crypto import async_get_device_list
                devices = await async_get_device_list(
                    session, data[CONF_USER_ID], data[CONF_AUTH_CODE], model
                )
                if not devices:
                    raise InvalidAuth
                device = devices[0]
                info = {
                    "title": device.get("MachineTitle", "樂奇 M8-E"),
                    CONF_DEVICE_ID: str(device["mdid"]),
                    CONF_MAC: device["Mac"],
                    "devices": devices,
                }
                return info

            # M8: use getHomeDeviceDetail
            payload = f"u_id={data[CONF_USER_ID]}&AuthCode={data[CONF_AUTH_CODE]}&ShareMidno="
            async with session.post(
                urls["list"],
                data=payload,
                headers=HEADERS,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                text = await response.text()
                _LOGGER.debug("API Response text: %s", text)
                result = json.loads(text)

                if result and len(result) > 0:
                    device = result[0]
                    mdid = device.get("mdid")
                    if mdid:
                        mac = device.get("md_mac")
                        return {
                            "title": device.get("md_wisdom") or "樂奇全熱交換機",
                            CONF_DEVICE_ID: str(mdid),
                            CONF_MAC: mac,
                        }
                raise InvalidAuth
    except (InvalidAuth, CannotConnect):
        raise
    except aiohttp.ClientError as err:
        _LOGGER.error("Connection error: %s", err)
        raise CannotConnect from err
    except json.JSONDecodeError as err:
        _LOGGER.error("JSON decode error: %s", err)
        raise CannotConnect from err


async def validate_credentials(
    hass: HomeAssistant, data: dict[str, Any], model: str = DEVICE_MODEL_M8
) -> dict[str, Any]:
    """Validate account + password login."""
    from .crypto import async_login

    async with aiohttp.ClientSession() as session:
        try:
            result = await async_login(
                session,
                data[CONF_ACCOUNT],
                data[CONF_PASSWORD],
                model=model,
            )
            default_name = "樂奇 M8-E" if is_m8e_platform(model) else "樂奇全熱交換機"
            info = {
                "title": result.get("title", default_name),
                CONF_USER_ID: result["u_id"],
                CONF_AUTH_CODE: result["auth_code"],
                CONF_DEVICE_ID: result[CONF_DEVICE_ID],
                CONF_MAC: result[CONF_MAC],
            }
            if result.get("devices"):
                info["devices"] = result["devices"]
            return info
        except ValueError as err:
            _LOGGER.error("Auth error: %s", err)
            raise InvalidAuth from err
        except ConnectionError as err:
            _LOGGER.error("Connection error: %s", err)
            raise CannotConnect from err


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Lifegear HRV."""

    VERSION = 5

    def __init__(self) -> None:
        """Initialize."""
        self._model: str = DEVICE_MODEL_M8

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        """Get the options flow for this handler."""
        return OptionsFlowHandler()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle device model and login method selection."""
        if user_input is not None:
            self._model = user_input.get(CONF_DEVICE_MODEL, DEVICE_MODEL_M8)
            method = user_input[CONF_LOGIN_METHOD]
            if method == LOGIN_METHOD_CREDENTIALS:
                return await self.async_step_credentials()
            if method == LOGIN_METHOD_LOCAL:
                return await self.async_step_local()
            return await self.async_step_manual()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DEVICE_MODEL, default=DEVICE_MODEL_M8): vol.In(
                        {
                            DEVICE_MODEL_M8: "智慧果 M8",
                            DEVICE_MODEL_M8E: "淨流系統（M8-E / 暖風機 / 感測器）",
                        }
                    ),
                    vol.Required(CONF_LOGIN_METHOD, default=LOGIN_METHOD_LOCAL): vol.In(
                        {
                            LOGIN_METHOD_LOCAL: "本地控制（無需雲端）",
                            LOGIN_METHOD_CREDENTIALS: "帳號密碼登入（雲端）",
                            LOGIN_METHOD_MANUAL: "手動輸入 u_id + AuthCode（雲端）",
                        }
                    ),
                }
            ),
        )

    async def async_step_local(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle local control setup."""
        errors: dict[str, str] = {}

        if user_input is not None:
            local_url = user_input[CONF_LOCAL_SERVER].rstrip("/")
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        f"{local_url}/api/status",
                        timeout=aiohttp.ClientTimeout(total=5),
                    ) as response:
                        data = await response.json()
                mac = user_input.get(CONF_MAC, "").strip().upper().replace(":", "")
                device_id = user_input.get(CONF_DEVICE_ID, "").strip()
                if not mac:
                    state = data.get("state", {})
                    mac = ""
                entry_data = {
                    CONF_LOGIN_METHOD: LOGIN_METHOD_LOCAL,
                    CONF_DEVICE_MODEL: self._model,
                    CONF_LOCAL_SERVER: local_url,
                    CONF_MAC: mac,
                    CONF_DEVICE_ID: device_id,
                }
                # Optional cloud credentials for mode control via getDeviceMod.asp
                account = user_input.get(CONF_ACCOUNT, "").strip()
                password = user_input.get(CONF_PASSWORD, "").strip()
                if account and password:
                    entry_data[CONF_ACCOUNT] = account
                    entry_data[CONF_PASSWORD] = password
                return self.async_create_entry(title="樂奇全熱交換機 (本地)", data=entry_data)
            except Exception as err:
                _LOGGER.error("Cannot connect to local server: %s", err)
                errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="local",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_LOCAL_SERVER, default="http://192.168.1.x:8765"): str,
                    vol.Optional(CONF_MAC, default=""): str,
                    vol.Optional(CONF_DEVICE_ID, default=""): str,
                    vol.Optional(CONF_ACCOUNT, default=""): str,
                    vol.Optional(CONF_PASSWORD, default=""): str,
                }
            ),
            errors=errors,
        )

    async def async_step_credentials(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle account + password login."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                info = await validate_credentials(self.hass, user_input, model=self._model)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                if is_m8e_platform(self._model) and info.get("devices"):
                    return self._create_all_devices(
                        info["devices"],
                        login_method=LOGIN_METHOD_CREDENTIALS,
                        user_id=info[CONF_USER_ID],
                        auth_code=info[CONF_AUTH_CODE],
                        account=user_input[CONF_ACCOUNT],
                        password=user_input[CONF_PASSWORD],
                    )

                entry_data = {
                    CONF_LOGIN_METHOD: LOGIN_METHOD_CREDENTIALS,
                    CONF_DEVICE_MODEL: self._model,
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

    def _create_all_devices(
        self,
        devices: list[dict],
        login_method: str,
        user_id: str,
        auth_code: str,
        account: str | None = None,
        password: str | None = None,
    ) -> FlowResult:
        """Auto-create config entries for all new devices. Returns first entry."""
        existing_macs = {
            entry.data.get(CONF_MAC)
            for entry in self.hass.config_entries.async_entries(DOMAIN)
        }
        available = [d for d in devices if d["Mac"] not in existing_macs]
        if not available:
            return self.async_abort(reason="already_configured")

        def _build_entry_data(device: dict) -> dict:
            data = {
                CONF_LOGIN_METHOD: login_method,
                CONF_DEVICE_MODEL: detect_device_model(device.get("MachineNo", "")),
                CONF_USER_ID: user_id,
                CONF_AUTH_CODE: auth_code,
                CONF_DEVICE_ID: str(device["mdid"]),
                CONF_MAC: device["Mac"],
            }
            if account and password:
                data[CONF_ACCOUNT] = account
                data[CONF_PASSWORD] = password
            return data

        # Background-create 2nd, 3rd, ... devices
        for device in available[1:]:
            self.hass.async_create_task(
                self.hass.config_entries.flow.async_init(
                    DOMAIN,
                    context={"source": "auto_device"},
                    data={
                        "title": device.get("MachineTitle", "樂奇設備"),
                        "entry_data": _build_entry_data(device),
                    },
                )
            )

        # Return first device as main entry
        first = available[0]
        return self.async_create_entry(
            title=first.get("MachineTitle", "樂奇設備"),
            data=_build_entry_data(first),
        )

    async def async_step_auto_device(
        self, discovery_info: dict[str, Any]
    ) -> FlowResult:
        """Handle auto-creation of additional devices."""
        mac = discovery_info["entry_data"][CONF_MAC]
        await self.async_set_unique_id(mac)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=discovery_info["title"],
            data=discovery_info["entry_data"],
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle manual u_id + AuthCode input."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                info = await validate_manual_input(self.hass, user_input, model=self._model)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                if is_m8e_platform(self._model) and info.get("devices"):
                    return self._create_all_devices(
                        info["devices"],
                        login_method=LOGIN_METHOD_MANUAL,
                        user_id=user_input[CONF_USER_ID],
                        auth_code=user_input[CONF_AUTH_CODE],
                    )

                entry_data = {
                    CONF_LOGIN_METHOD: LOGIN_METHOD_MANUAL,
                    CONF_DEVICE_MODEL: self._model,
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
        """Handle reconfiguration (update auth only, preserve device config)."""
        errors: dict[str, str] = {}
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        is_credentials = entry.data.get(CONF_LOGIN_METHOD) == LOGIN_METHOD_CREDENTIALS
        model = entry.data.get(CONF_DEVICE_MODEL, DEVICE_MODEL_M8)

        if user_input is not None:
            try:
                if is_credentials:
                    info = await validate_credentials(self.hass, user_input, model=model)
                    new_data = {
                        **entry.data,
                        CONF_ACCOUNT: user_input[CONF_ACCOUNT],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_USER_ID: info[CONF_USER_ID],
                        CONF_AUTH_CODE: info[CONF_AUTH_CODE],
                    }
                else:
                    info = await validate_manual_input(self.hass, user_input, model=model)
                    new_data = {
                        **entry.data,
                        CONF_USER_ID: user_input[CONF_USER_ID],
                        CONF_AUTH_CODE: user_input[CONF_AUTH_CODE],
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
        login_method = self.config_entry.data.get(CONF_LOGIN_METHOD)
        is_credentials = login_method == LOGIN_METHOD_CREDENTIALS
        is_local = login_method == LOGIN_METHOD_LOCAL

        model = self.config_entry.data.get(CONF_DEVICE_MODEL, DEVICE_MODEL_M8)

        if user_input is not None:
            try:
                if is_local:
                    # Local mode: just update account/password (no validation needed)
                    new_data = {**self.config_entry.data}
                    account = user_input.get(CONF_ACCOUNT, "").strip()
                    password = user_input.get(CONF_PASSWORD, "").strip()
                    if account and password:
                        new_data[CONF_ACCOUNT] = account
                        new_data[CONF_PASSWORD] = password
                    else:
                        new_data.pop(CONF_ACCOUNT, None)
                        new_data.pop(CONF_PASSWORD, None)
                elif is_credentials:
                    info = await validate_credentials(self.hass, user_input, model=model)
                    new_data = {
                        **self.config_entry.data,
                        CONF_ACCOUNT: user_input[CONF_ACCOUNT],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_USER_ID: info[CONF_USER_ID],
                        CONF_AUTH_CODE: info[CONF_AUTH_CODE],
                    }
                else:
                    data = {
                        CONF_USER_ID: user_input[CONF_USER_ID],
                        CONF_AUTH_CODE: user_input[CONF_AUTH_CODE],
                    }
                    info = await validate_manual_input(self.hass, data, model=model)
                    new_data = {
                        **self.config_entry.data,
                        CONF_USER_ID: user_input[CONF_USER_ID],
                        CONF_AUTH_CODE: user_input[CONF_AUTH_CODE],
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

        if is_local:
            schema = vol.Schema(
                {
                    vol.Optional(CONF_ACCOUNT, default=self.config_entry.data.get(CONF_ACCOUNT, "")): str,
                    vol.Optional(CONF_PASSWORD, default=self.config_entry.data.get(CONF_PASSWORD, "")): str,
                }
            )
        elif is_credentials:
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
