"""DataUpdateCoordinator for Lifegear HRV."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import timedelta
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

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
    LOGIN_METHOD_CREDENTIALS,
    LOGIN_METHOD_LOCAL,
    API_GET_STATUS,
    API_SET_CONTROL,
    HEADERS,
)

_LOGGER = logging.getLogger(__name__)


class LifegearHRVCoordinator(DataUpdateCoordinator):
    """Class to manage fetching Lifegear HRV data."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize."""
        self.entry = entry
        self.mac = entry.data.get(CONF_MAC, "")
        self.device_id = entry.data.get(CONF_DEVICE_ID, "")
        self._local_mode = entry.data.get(CONF_LOGIN_METHOD) == LOGIN_METHOD_LOCAL
        self._local_server = entry.data.get(CONF_LOCAL_SERVER, "").rstrip("/")

        # Cloud-mode fields
        self.user_id = entry.data.get(CONF_USER_ID, "")
        self.auth_code = entry.data.get(CONF_AUTH_CODE, "")
        self._relogin_attempted = False

        # Local mode polls more frequently (device pushes ~every 3 s)
        interval = timedelta(seconds=5) if self._local_mode else timedelta(seconds=30)
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=interval,
        )

    async def _async_relogin(self) -> bool:
        """Re-login to get a new AuthCode (only for credentials-based login)."""
        if self.entry.data.get(CONF_LOGIN_METHOD) != LOGIN_METHOD_CREDENTIALS:
            return False

        account = self.entry.data.get(CONF_ACCOUNT)
        password = self.entry.data.get(CONF_PASSWORD)
        if not account or not password:
            return False

        _LOGGER.info("Attempting to re-login to refresh AuthCode")
        try:
            from .crypto import async_login
            async with aiohttp.ClientSession() as session:
                result = await async_login(session, account, password)

            # Update entry data with new AuthCode
            new_data = {**self.entry.data, CONF_AUTH_CODE: result["auth_code"]}
            self.hass.config_entries.async_update_entry(self.entry, data=new_data)
            self.auth_code = result["auth_code"]
            _LOGGER.info("Re-login successful, AuthCode refreshed")
            return True
        except Exception as err:
            _LOGGER.error("Re-login failed: %s", err)
            return False

    async def _async_update_local(self) -> dict[str, Any]:
        """Fetch data from local server REST API."""
        url = f"{self._local_server}/api/status"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as response:
                    raw = await response.json()

            sensor = raw.get("sensor", {})
            state  = raw.get("state",  {})

            ispower_raw = state.get("ispower")
            if ispower_raw is None:
                ispower = None
            else:
                ispower = 1 if ispower_raw else 0

            return {
                "md_co2":       str(sensor["co2"])   if sensor.get("co2")   is not None else "",
                "md_pm25":      str(sensor["pm25"])  if sensor.get("pm25")  is not None else "",
                "md_temp":      str(sensor["temp"])  if sensor.get("temp")  is not None else "",
                "md_rh":        str(sensor["rh"])    if sensor.get("rh")    is not None else "",
                "md_speed":     str(state["speed"])  if state.get("speed")  is not None else "",
                "md_mode":      str(state["mode"])   if state.get("mode")   is not None else "",
                "md_ispower":   ispower,
                "md_isconnect": 1,
                "mdid":         self.device_id,
                "md_mac":       self.mac,
                # local-mode extras
                "_local": True,
                "_sensor_ts": sensor.get("last_update"),
                "_state_ts":  state.get("last_update"),
            }
        except Exception as err:
            raise UpdateFailed(f"Local server error ({url}): {err}")

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from API."""
        if self._local_mode:
            return await self._async_update_local()

        try:
            async with aiohttp.ClientSession() as session:
                payload = f"u_id={self.user_id}&AuthCode={self.auth_code}"
                async with session.post(
                    API_GET_STATUS,
                    data=payload,
                    headers=HEADERS,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as response:
                    text = await response.text()
                    data = json.loads(text)
                    if data and len(data) > 0:
                        device = data[0]
                        if device.get("success") is False or device.get("mdid"):
                            # If we have device data, reset relogin flag
                            if device.get("mdid"):
                                self._relogin_attempted = False
                                return device
                            # Auth failure - try relogin
                            if not self._relogin_attempted:
                                self._relogin_attempted = True
                                if await self._async_relogin():
                                    # Retry with new auth code
                                    payload2 = f"u_id={self.user_id}&AuthCode={self.auth_code}"
                                    async with session.post(
                                        API_GET_STATUS,
                                        data=payload2,
                                        headers=HEADERS,
                                        timeout=aiohttp.ClientTimeout(total=10),
                                    ) as response2:
                                        text2 = await response2.text()
                                        data2 = json.loads(text2)
                                        if data2 and len(data2) > 0 and data2[0].get("mdid"):
                                            self._relogin_attempted = False
                                            return data2[0]
                        return device
                    raise UpdateFailed("No data received")
        except UpdateFailed:
            raise
        except Exception as err:
            raise UpdateFailed(f"Error communicating with API: {err}")

    async def _async_set_control_local(
        self,
        ispower: int | None,
        mode: int | None,
        speed: int | None,
    ) -> bool:
        """Send command to local server REST API."""
        current_data = self.data or {}
        cmd = {
            "ispower": ispower if ispower is not None else int(current_data.get("md_ispower") or 1),
            "mode":    mode    if mode    is not None else int(current_data.get("md_mode")    or 3),
            "speed":   speed   if speed   is not None else int(current_data.get("md_speed")   or 1),
        }
        url = f"{self._local_server}/api/command"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    json=cmd,
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as response:
                    result = await response.json()
                    if result.get("ok"):
                        _LOGGER.debug("Local command sent: %s", cmd)
                        await asyncio.sleep(0.5)
                        await self.async_request_refresh()
                        return True
        except Exception as err:
            _LOGGER.error("Local command error: %s", err)
        return False

    async def async_set_control(
        self,
        ispower: int | None = None,
        mode: int | None = None,
        speed: int | None = None,
        max_retries: int = 3,
    ) -> bool:
        """Send control command to device."""
        if self._local_mode:
            return await self._async_set_control_local(ispower, mode, speed)

        current_data = self.data or {}

        # 使用當前數據作為基礎
        target_power = ispower if ispower is not None else int(current_data.get("md_ispower", 1))
        target_mode = mode if mode is not None else int(current_data.get("md_mode", 1))
        target_speed = speed if speed is not None else int(current_data.get("md_speed", 1))

        # 確保風速至少為 1
        if target_speed < 1:
            target_speed = 1

        # 不送感測器數據，避免影響 AQI
        payload = (
            f"u_id={self.user_id}&AuthCode={self.auth_code}"
            f"&mdid={self.device_id}&md_mac={self.mac}"
            f"&md_ispower={target_power}&md_isconnect=1"
            f"&md_mode={target_mode}&md_speed={target_speed}"
            f"&md_isreserve=1&md_stime=255&md_etime=255&md_isUse=1"
        )

        for attempt in range(max_retries):
            try:
                async with aiohttp.ClientSession() as session:
                    # 第一次發送
                    async with session.post(
                        API_SET_CONTROL,
                        data=payload,
                        headers=HEADERS,
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as response:
                        text = await response.text()
                        _LOGGER.debug("Request attempt %d response: %s", attempt + 1, text)

                    # 等待 200ms
                    await asyncio.sleep(0.2)

                    # 第二次發送確保指令送達
                    async with session.post(
                        API_SET_CONTROL,
                        data=payload,
                        headers=HEADERS,
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as response:
                        text = await response.text()
                        _LOGGER.debug("Confirm request response: %s", text)

                # 等待 1 秒後輪詢確認
                await asyncio.sleep(1.0)
                await self.async_request_refresh()

                # 驗證設定值
                new_data = self.data or {}
                actual_power = int(new_data.get("md_ispower", -1))
                actual_mode = int(new_data.get("md_mode", -1))
                actual_speed = int(new_data.get("md_speed", -1))

                power_ok = (ispower is None) or (actual_power == target_power)
                mode_ok = (mode is None) or (actual_mode == target_mode)
                speed_ok = (speed is None) or (actual_speed == target_speed)

                if power_ok and mode_ok and speed_ok:
                    _LOGGER.debug("Control command verified successfully")
                    return True
                else:
                    _LOGGER.warning(
                        "Control verification failed (attempt %d/%d): "
                        "power=%s/%s, mode=%s/%s, speed=%s/%s",
                        attempt + 1, max_retries,
                        actual_power, target_power,
                        actual_mode, target_mode,
                        actual_speed, target_speed,
                    )
                    await asyncio.sleep(0.5)

            except Exception as err:
                _LOGGER.error("Error sending control command (attempt %d): %s", attempt + 1, err)
                await asyncio.sleep(0.5)

        _LOGGER.error("Control command failed after %d retries", max_retries)
        return False
