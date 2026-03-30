"""DataUpdateCoordinator for Lifegear HRV."""
from __future__ import annotations

import asyncio
import json
import logging
import time
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
    normalize_mode,
)

_LOGGER = logging.getLogger(__name__)

# Minimum seconds between re-login attempts (prevent login war with APP)
_RELOGIN_COOLDOWN = 120


class LifegearHRVCoordinator(DataUpdateCoordinator):
    """Class to manage fetching Lifegear HRV data."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize."""
        self.entry = entry
        self.mac = entry.data.get(CONF_MAC, "")
        self.device_id = entry.data.get(CONF_DEVICE_ID, "")
        self._local_mode = entry.data.get(CONF_LOGIN_METHOD) == LOGIN_METHOD_LOCAL
        self._local_server = entry.data.get(CONF_LOCAL_SERVER, "").strip().rstrip("/")

        # Cloud-mode fields
        self.user_id = entry.data.get(CONF_USER_ID, "")
        self.auth_code = entry.data.get(CONF_AUTH_CODE, "")
        self._relogin_attempted = False

        # AuthCode management (works for both credentials and local+credentials modes)
        self._has_cloud_creds = bool(
            entry.data.get(CONF_ACCOUNT) and entry.data.get(CONF_PASSWORD)
        )
        # For credentials mode, auth_code from config is already valid
        self._auth_valid = bool(self.auth_code) and not self._local_mode
        self._last_relogin_time: float = 0

        # Local mode polls more frequently (device pushes ~every 3 s)
        interval = timedelta(seconds=5) if self._local_mode else timedelta(seconds=30)
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=interval,
        )

    async def _async_relogin(self) -> bool:
        """Re-login to get a new AuthCode (credentials or local+credentials)."""
        account = self.entry.data.get(CONF_ACCOUNT)
        password = self.entry.data.get(CONF_PASSWORD)
        if not account or not password:
            return False

        # Cooldown to prevent login war with APP
        now = time.monotonic()
        if now - self._last_relogin_time < _RELOGIN_COOLDOWN:
            _LOGGER.debug("Re-login cooldown active, skipping")
            return False

        _LOGGER.info("Attempting to re-login to refresh AuthCode")
        self._last_relogin_time = now
        try:
            from .crypto import async_login
            async with aiohttp.ClientSession() as session:
                result = await async_login(session, account, password)

            new_data = {
                **self.entry.data,
                CONF_USER_ID: result["u_id"],
                CONF_AUTH_CODE: result["auth_code"],
            }
            # Also grab device_id/mac if we didn't have them
            if not self.device_id and result.get(CONF_DEVICE_ID):
                new_data[CONF_DEVICE_ID] = result[CONF_DEVICE_ID]
                self.device_id = result[CONF_DEVICE_ID]
            if not self.mac and result.get(CONF_MAC):
                new_data[CONF_MAC] = result[CONF_MAC]
                self.mac = result[CONF_MAC]

            self.hass.config_entries.async_update_entry(self.entry, data=new_data)
            self.user_id = result["u_id"]
            self.auth_code = result["auth_code"]
            self._auth_valid = True
            _LOGGER.info("Re-login successful, AuthCode refreshed")
            return True
        except Exception as err:
            _LOGGER.error("Re-login failed: %s", err)
            self._auth_valid = False
            return False

    async def async_cloud_login(self) -> None:
        """Initial cloud login to obtain AuthCode (called once at startup)."""
        if not self._has_cloud_creds:
            return
        if await self._async_relogin():
            _LOGGER.info("Initial cloud login successful (u_id=%s)", self.user_id)
        else:
            _LOGGER.warning("Initial cloud login failed — mode control via cloud will be unavailable")

    async def async_manual_relogin(self) -> bool:
        """Manual re-login triggered by user (bypasses cooldown)."""
        if not self._has_cloud_creds:
            _LOGGER.warning("No cloud credentials configured")
            return False
        self._last_relogin_time = 0  # bypass cooldown
        return await self._async_relogin()

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

            wifi = raw.get("wifi", {})

            return {
                "md_co2":       str(sensor["co2"])   if sensor.get("co2")   is not None else None,
                "md_pm25":      str(sensor["pm25"])  if sensor.get("pm25")  is not None else None,
                "md_temp":      str(sensor["temp"])  if sensor.get("temp")  is not None else None,
                "md_rh":        str(sensor["rh"])    if sensor.get("rh")    is not None else None,
                "md_speed":     str(state["speed"])  if state.get("speed")  is not None else None,
                "md_mode":      str(state["mode"])   if state.get("mode")   is not None else None,
                "md_ispower":   ispower,
                "md_isconnect": 1,
                "mdid":         self.device_id,
                "md_mac":       self.mac,
                # local-mode extras
                "_local": True,
                "_sensor_ts": sensor.get("last_update"),
                "_state_ts":  state.get("last_update"),
                "_m8_online": self._is_m8_online(sensor.get("last_update")),
                "_wifi_rssi_pct":   wifi.get("rssi_pct"),
                "_wifi_rssi_label": wifi.get("rssi_label"),
                "_wifi_ssid":       wifi.get("ssid"),
            }
        except Exception as err:
            raise UpdateFailed(f"Local server error ({url}): {err}")

    @staticmethod
    def _is_m8_online(last_update_iso: str | None) -> bool:
        """Return True if M8 has sent data (last_update is set)."""
        return bool(last_update_iso)

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

    async def _async_cloud_set_control(
        self,
        target_power: int,
        target_mode: int,
        target_speed: int,
    ) -> bool:
        """Call getDeviceMod.asp directly with current AuthCode (double-send)."""
        if not self._auth_valid or not self.auth_code:
            return False

        payload = (
            f"u_id={self.user_id}&AuthCode={self.auth_code}"
            f"&mdid={self.device_id}&md_mac={self.mac}"
            f"&md_ispower={target_power}&md_isconnect=1"
            f"&md_mode={target_mode}&md_speed={target_speed}"
            f"&md_isreserve=1&md_stime=255&md_etime=255&md_isUse=1"
        )
        try:
            async with aiohttp.ClientSession() as session:
                # Double-send to ensure cloud registers the change
                async with session.post(
                    API_SET_CONTROL, data=payload, headers=HEADERS,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as response:
                    text = await response.text()
                    _LOGGER.debug("Cloud getDeviceMod response (1st): %s", text)
                await asyncio.sleep(0.2)
                async with session.post(
                    API_SET_CONTROL, data=payload, headers=HEADERS,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as response:
                    text = await response.text()
                    _LOGGER.debug("Cloud getDeviceMod response (2nd): %s", text)
                return True
        except Exception as err:
            _LOGGER.warning("Cloud getDeviceMod failed: %s", err)
            return False

    async def _async_set_control_local(
        self,
        ispower: int | None,
        mode: int | None,
        speed: int | None,
    ) -> bool:
        """Send command to local add-on REST API + cloud getDeviceMod.asp."""
        current_data = self.data or {}
        cmd = {
            "ispower": ispower if ispower is not None else int(current_data.get("md_ispower") or 1),
            "mode":    mode if mode is not None else normalize_mode(current_data.get("md_mode") or 3),
            "speed":   speed if speed is not None else int(current_data.get("md_speed") or 1),
        }
        cmd["mode"] = normalize_mode(cmd["mode"])
        if cmd["speed"] < 1:
            cmd["speed"] = 1

        # 1) Send to local add-on (MitM injection for speed/power)
        addon_ok = False
        url = f"{self._local_server}/api/command"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    json=cmd,
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as response:
                    result = await response.json()
                    addon_ok = result.get("ok", False)
                    _LOGGER.debug("Local command sent: %s (ok=%s)", cmd, addon_ok)
        except Exception as err:
            _LOGGER.error("Local command error: %s", err)

        # 2) Also call cloud getDeviceMod.asp directly (reliable mode control)
        cloud_ok = False
        if self._has_cloud_creds:
            cloud_ok = await self._async_cloud_set_control(
                cmd["ispower"], cmd["mode"], cmd["speed"]
            )
            if not cloud_ok and self._auth_valid:
                _LOGGER.info("Cloud command failed, attempting re-login")
                if await self._async_relogin():
                    cloud_ok = await self._async_cloud_set_control(
                        cmd["ispower"], cmd["mode"], cmd["speed"]
                    )
            if cloud_ok:
                _LOGGER.debug("Cloud getDeviceMod ok: mode=%s speed=%s", cmd["mode"], cmd["speed"])

        if addon_ok or cloud_ok:
            await asyncio.sleep(0.5)
            await self.async_request_refresh()
            return True
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
