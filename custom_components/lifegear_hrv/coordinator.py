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
    CONF_DEVICE_MODEL,
    LOGIN_METHOD_CREDENTIALS,
    LOGIN_METHOD_LOCAL,
    DEVICE_MODEL_M8,
    HEADERS,
    normalize_mode,
    get_api_urls,
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
        self._model = entry.data.get(CONF_DEVICE_MODEL, DEVICE_MODEL_M8)
        self._api_urls = get_api_urls(self._model)

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
                result = await async_login(session, account, password, model=self._model)

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
                "_m8_online": self._is_m8_online(sensor.get("last_update"), state.get("last_update")),
                "_wifi_rssi_pct":   wifi.get("rssi_pct"),
                "_wifi_rssi_label": wifi.get("rssi_label"),
                "_wifi_ssid":       wifi.get("ssid"),
            }
        except Exception as err:
            raise UpdateFailed(f"Local server error ({url}): {err}")

    @staticmethod
    def _is_m8_online(sensor_ts: str | None, state_ts: str | None) -> bool:
        """Return True if M8 has sent data (either sensor or state timestamp exists)."""
        return bool(sensor_ts) or bool(state_ts)

    def _build_status_payload(self) -> str:
        """Build payload for status API (M8 vs M8-E format)."""
        from .const import DEVICE_MODEL_M8E
        if self._model == DEVICE_MODEL_M8E:
            # M8-E needs Mac parameter
            return f"Mac={self.mac}&u_id={self.user_id}&AuthCode={self.auth_code}&ShareMidno="
        return f"u_id={self.user_id}&AuthCode={self.auth_code}"

    def _normalize_device_data(self, device: dict) -> dict:
        """Normalize M8-E field names to M8 format for unified entity handling."""
        from .const import DEVICE_MODEL_M8E
        if self._model != DEVICE_MODEL_M8E:
            return device
        # M8-E uses short names; map to md_ prefixed names for compatibility
        return {
            "mdid": device.get("mdid"),
            "md_mac": device.get("mac"),
            "md_co2": str(device.get("co2", "")),
            "md_pm25": str(device.get("pm25", "")),
            "md_temp": str(device.get("temp", "")),
            "md_rh": str(device.get("rh", "")),
            "md_speed": str(device.get("speed", "")),
            "md_mode": str(device.get("mode", "")),
            "md_ispower": int(device.get("ispower", 0)),
            "md_isconnect": int(device.get("isOnLine", 0)),
        }

    def _extract_device(self, data: list) -> dict | None:
        """Extract device dict from status API response."""
        if not data or len(data) == 0:
            return None
        entry = data[0]
        # Both M8 and M8-E getHomeDeviceDetail return device directly
        return entry if entry.get("mdid") or entry.get("success") is True else None

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from API."""
        if self._local_mode:
            return await self._async_update_local()

        try:
            async with aiohttp.ClientSession() as session:
                payload = self._build_status_payload()
                async with session.post(
                    self._api_urls["status"],
                    data=payload,
                    headers=HEADERS,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as response:
                    text = await response.text()
                    data = json.loads(text)
                    device = self._extract_device(data)
                    if device and device.get("mdid"):
                        self._relogin_attempted = False
                        return self._normalize_device_data(device)
                    # Auth failure or no device - try relogin
                    if not self._relogin_attempted and self._has_cloud_creds:
                        self._relogin_attempted = True
                        if await self._async_relogin():
                            payload2 = self._build_status_payload()
                            async with session.post(
                                self._api_urls["status"],
                                data=payload2,
                                headers=HEADERS,
                                timeout=aiohttp.ClientTimeout(total=10),
                            ) as response2:
                                text2 = await response2.text()
                                data2 = json.loads(text2)
                                device2 = self._extract_device(data2)
                                if device2 and device2.get("mdid"):
                                    self._relogin_attempted = False
                                    return self._normalize_device_data(device2)
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
        power_changed: bool = False,
        speed_changed: bool = False,
    ) -> bool:
        """Call cloud control API with current AuthCode (double-send)."""
        if not self._auth_valid or not self.auth_code:
            return False

        from .const import DEVICE_MODEL_M8E
        try:
            async with aiohttp.ClientSession() as session:
                if self._model == DEVICE_MODEL_M8E:
                    # M8-E: mode/speed first, then power
                    speed_val = str(target_speed) if speed_changed else ""
                    control_payload = (
                        f"Mode={target_mode}&AuthCode={self.auth_code}"
                        f"&Speed={speed_val}&CountDown="
                        f"&u_id={self.user_id}&ShareMidno="
                        f"&Function=&Mac={self.mac}&Auto=&Mute="
                    )
                else:
                    # M8: all-in-one control
                    control_payload = (
                        f"u_id={self.user_id}&AuthCode={self.auth_code}"
                        f"&mdid={self.device_id}&md_mac={self.mac}"
                        f"&md_ispower={target_power}&md_isconnect=1"
                        f"&md_mode={target_mode}&md_speed={target_speed}"
                        f"&md_isreserve=1&md_stime=255&md_etime=255&md_isUse=1"
                    )

                # Double-send control
                async with session.post(
                    self._api_urls["control"], data=control_payload, headers=HEADERS,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as response:
                    text = await response.text()
                    _LOGGER.debug("Cloud control response (1st): %s", text)
                await asyncio.sleep(0.2)
                async with session.post(
                    self._api_urls["control"], data=control_payload, headers=HEADERS,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as response:
                    text = await response.text()
                    _LOGGER.debug("Cloud control response (2nd): %s", text)

                # M8-E: send power after mode/speed
                if self._model == DEVICE_MODEL_M8E and "power" in self._api_urls:
                    # Auto power on if device is off and user changed mode/speed
                    current_power = int((self.data or {}).get("md_ispower", 0))
                    need_power = power_changed or (not current_power and (speed_changed or not power_changed))
                    if need_power:
                        await asyncio.sleep(0.3)
                        power_val = target_power if power_changed else 1
                        power_payload = (
                            f"Mac={self.mac}&u_id={self.user_id}"
                            f"&AuthCode={self.auth_code}"
                            f"&IsPower={power_val}&ShareMidno="
                        )
                        async with session.post(
                            self._api_urls["power"], data=power_payload, headers=HEADERS,
                            timeout=aiohttp.ClientTimeout(total=10),
                        ) as response:
                            text = await response.text()
                            _LOGGER.debug("Cloud getDevicePower response: %s", text)

                return True
        except Exception as err:
            _LOGGER.warning("Cloud control failed: %s", err)
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

        # 2) Also call cloud API directly (reliable mode control)
        cloud_ok = False
        power_changed = ispower is not None
        speed_changed = speed is not None
        if self._has_cloud_creds:
            cloud_ok = await self._async_cloud_set_control(
                cmd["ispower"], cmd["mode"], cmd["speed"],
                power_changed=power_changed, speed_changed=speed_changed,
            )
            if not cloud_ok and self._auth_valid:
                _LOGGER.info("Cloud command failed, attempting re-login")
                if await self._async_relogin():
                    cloud_ok = await self._async_cloud_set_control(
                        cmd["ispower"], cmd["mode"], cmd["speed"],
                        power_changed=power_changed, speed_changed=speed_changed,
                    )
            if cloud_ok:
                _LOGGER.debug("Cloud control ok: mode=%s speed=%s power=%s", cmd["mode"], cmd["speed"], cmd["ispower"])

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
        target_power = ispower if ispower is not None else int(current_data.get("md_ispower", 1))
        target_mode = mode if mode is not None else int(current_data.get("md_mode", 1))
        target_speed = speed if speed is not None else int(current_data.get("md_speed", 1))
        if target_speed < 1:
            target_speed = 1

        power_changed = ispower is not None
        speed_changed = speed is not None
        for attempt in range(max_retries):
            try:
                ok = await self._async_cloud_set_control(
                    target_power, target_mode, target_speed,
                    power_changed=power_changed, speed_changed=speed_changed,
                )
                if not ok:
                    _LOGGER.warning("Cloud control returned False (attempt %d)", attempt + 1)
                    await asyncio.sleep(0.5)
                    continue

                await asyncio.sleep(1.0)
                await self.async_request_refresh()

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
