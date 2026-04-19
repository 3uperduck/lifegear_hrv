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
    DEVICE_MODEL_BATH_HEATER,
    DEVICE_MODEL_M8E_SENSOR,
    HEADERS,
    normalize_mode,
    get_api_urls,
    is_m8e_platform,
)

_LOGGER = logging.getLogger(__name__)

# Minimum seconds between re-login attempts (prevent login war with APP)
_RELOGIN_COOLDOWN = 120

# Process-wide lock per account. The cloud issues single-session AuthCodes:
# a new code invalidates every previously-issued code for the user. Two
# coordinators on the same account must not re-login concurrently or they
# will wipe each other's auth_code mid-request. The lock is keyed by the
# account id and shared across every coordinator instance.
_relogin_locks: dict[str, asyncio.Lock] = {}


def _get_relogin_lock(account: str) -> asyncio.Lock:
    if account not in _relogin_locks:
        _relogin_locks[account] = asyncio.Lock()
    return _relogin_locks[account]


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
        # Cloud mode: 60s to reduce server load (3 devices stagger naturally)
        interval = timedelta(seconds=5) if self._local_mode else timedelta(seconds=60)
        self._poll_count = 0
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

        lock = _get_relogin_lock(str(account))
        async with lock:
            # After acquiring the lock, check whether a sibling coordinator
            # just finished re-logging in on the same account and already
            # propagated a fresh AuthCode to our entry data. If so, adopt
            # it instead of hitting the cloud again (which would invalidate
            # the sibling's in-flight request).
            stored_auth = self.entry.data.get(CONF_AUTH_CODE)
            if (
                stored_auth
                and stored_auth != self.auth_code
            ):
                _LOGGER.info(
                    "AuthCode was refreshed by a sibling entry; adopting"
                )
                self.auth_code = stored_auth
                self.user_id = self.entry.data.get(CONF_USER_ID, self.user_id)
                self._auth_valid = True
                self._last_relogin_time = time.monotonic()
                self._relogin_attempted = False
                return True

            # Cooldown to prevent login war with APP
            now = time.monotonic()
            if now - self._last_relogin_time < _RELOGIN_COOLDOWN:
                _LOGGER.debug("Re-login cooldown active, skipping")
                return False

            return await self._do_relogin(account, password, now)

    async def _do_relogin(
        self, account: str, password: str, now: float
    ) -> bool:
        """Perform the actual cloud re-login. Must be called with the
        account lock held."""
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

            # Propagate the new AuthCode to sibling entries with the same
            # account. The cloud issues single-session AuthCodes: a new one
            # invalidates every previously-issued code for the user. Without
            # this sync two entries on the same account would ping-pong
            # relogins forever, causing flapping entities.
            self._propagate_auth_code(result["u_id"], result["auth_code"], now)

            _LOGGER.info("Re-login successful, AuthCode refreshed")
            return True
        except Exception as err:
            _LOGGER.error("Re-login failed: %s", err)
            self._auth_valid = False
            return False

    def _propagate_auth_code(
        self, user_id: str, auth_code: str, relogin_time: float
    ) -> None:
        """Push freshly minted AuthCode to sibling config entries on the same
        account, and bump their re-login cooldown so they won't race back.
        """
        for sibling in self.hass.config_entries.async_entries(DOMAIN):
            if sibling.entry_id == self.entry.entry_id:
                continue
            if sibling.data.get(CONF_USER_ID) != user_id:
                continue
            # Only sync entries that actually use cloud credentials.
            # Manual / local-only entries stay untouched.
            if not (
                sibling.data.get(CONF_ACCOUNT)
                and sibling.data.get(CONF_PASSWORD)
            ):
                continue
            if sibling.data.get(CONF_AUTH_CODE) == auth_code:
                continue
            self.hass.config_entries.async_update_entry(
                sibling,
                data={**sibling.data, CONF_AUTH_CODE: auth_code},
            )
            sibling_coord = self.hass.data.get(DOMAIN, {}).get(sibling.entry_id)
            if sibling_coord is not None:
                sibling_coord.auth_code = auth_code
                sibling_coord._auth_valid = True
                sibling_coord._relogin_attempted = False
                # Share the cooldown window so the sibling does not also
                # try to re-login on its next update and invalidate us.
                sibling_coord._last_relogin_time = relogin_time
            _LOGGER.info(
                "Propagated new AuthCode to sibling entry %s (%s)",
                sibling.entry_id, sibling.title,
            )

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
        """Return True if M8 has pushed data within the last 60 seconds."""
        from datetime import datetime, timezone

        now_utc = datetime.now(timezone.utc)
        for ts in (sensor_ts, state_ts):
            if ts:
                try:
                    dt = datetime.fromisoformat(ts)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    if (now_utc - dt).total_seconds() < 60:
                        return True
                except (ValueError, TypeError):
                    pass
        return False

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

    async def async_filter_reset(self, filter_type: int) -> bool:
        """Reset filter usage counter. FilterType: 1=Primary, 2=High."""
        if "filter_reset" not in self._api_urls:
            return False
        payload = (
            f"Mac={self.mac}&u_id={self.user_id}"
            f"&AuthCode={self.auth_code}&FilterType={filter_type}"
        )
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self._api_urls["filter_reset"], data=payload, headers=HEADERS,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as response:
                    text = await response.text()
                    _LOGGER.debug("Filter reset response: %s", text)
            await asyncio.sleep(1.0)
            await self.async_request_refresh()
            return True
        except Exception as err:
            _LOGGER.error("Filter reset failed: %s", err)
            return False

    async def async_filter_set_alarm_time(self, filter_type: int, alarm_time: int) -> bool:
        """Set filter alarm time. FilterType: 1=Primary, 2=High."""
        if "filter_edit" not in self._api_urls:
            return False
        payload = (
            f"Mac={self.mac}&u_id={self.user_id}"
            f"&AuthCode={self.auth_code}&FilterType={filter_type}"
            f"&AlarmTime={alarm_time}"
        )
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self._api_urls["filter_edit"], data=payload, headers=HEADERS,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as response:
                    text = await response.text()
                    _LOGGER.debug("Filter alarm edit response: %s", text)
            await asyncio.sleep(1.0)
            await self.async_request_refresh()
            return True
        except Exception as err:
            _LOGGER.error("Filter alarm edit failed: %s", err)
            return False

    def _get_addon_base_url_candidates(self) -> list[str]:
        """Return ordered list of candidate URLs for the m8_local_server addon.

        The addon runs with `host_network: true`, so its REST API is reachable
        via several names depending on the HA install type. We try each in
        order and cache the first that responds. Users without the addon
        simply return from every candidate with a failure — harmless.
        """
        candidates: list[str] = []
        from urllib.parse import urlparse
        # 1. User-configured internal/external URL hostname (explicit setup)
        for url_attr in ("internal_url", "external_url"):
            url = getattr(self.hass.config, url_attr, None)
            if url:
                host = urlparse(url).hostname
                if host:
                    candidates.append(f"http://{host}:8765")
        # 2. Supervisor-native addon hostnames (HAOS / Supervised)
        candidates.extend([
            "http://local-m8-local-server:8765",
            "http://local-m8-local-server.local.hass.io:8765",
            "http://homeassistant.local.hass.io:8765",
            "http://homeassistant.local:8765",
            "http://host.docker.internal:8765",
            "http://172.30.32.1:8765",
        ])
        # Dedupe while preserving order
        seen: set[str] = set()
        deduped: list[str] = []
        for url in candidates:
            if url not in seen:
                seen.add(url)
                deduped.append(url)
        return deduped

    async def _async_resolve_addon_base_url(
        self, session: aiohttp.ClientSession
    ) -> str | None:
        """Probe the candidate list and cache the first URL that responds."""
        cached = getattr(self, "_addon_base_url_cache", None)
        if cached:
            return cached
        for url in self._get_addon_base_url_candidates():
            probe_url = f"{url}/api/sensor/by_mac"
            try:
                async with session.get(
                    probe_url, timeout=aiohttp.ClientTimeout(total=2)
                ) as response:
                    if response.status == 200:
                        self._addon_base_url_cache = url
                        _LOGGER.info("m8_local_server addon reachable at %s", url)
                        return url
            except Exception:
                continue
        self._addon_base_url_cache = None
        return None

    async def _async_fetch_addon_duct_temps(
        self, session: aiohttp.ClientSession, result: dict
    ) -> None:
        """Fetch HRV duct temperatures (TempOA/SA/RA) from local m8_local_server addon.

        The M8-E HRV ESP only pushes duct temperatures via PostAirIndex. These
        are captured by the addon MitM and exposed at /api/sensor/by_mac.
        Only available when:
          1. The addon is running and reachable
          2. The UDM DNAT rule is routing HRV traffic through the addon
        Falls through silently otherwise — duct-temp entities become unavailable.
        Computes heat-recovery efficiency from the three temps.
        """
        if not self.mac:
            return
        base = await self._async_resolve_addon_base_url(session)
        if not base:
            return
        url = f"{base}/api/sensor/by_mac"
        try:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=3)
            ) as response:
                if response.status != 200:
                    return
                data = await response.json()
        except Exception as err:
            _LOGGER.debug("Addon duct-temp fetch failed: %s", err)
            return

        slot = data.get(self.mac.upper()) or data.get(self.mac) or {}

        def _to_float(v):
            try:
                return float(v) if v not in (None, "") else None
            except (TypeError, ValueError):
                return None

        oa = _to_float(slot.get("temp_oa"))
        sa = _to_float(slot.get("temp_sa"))
        ra = _to_float(slot.get("temp_ra"))
        result["md_temp_oa"] = oa
        result["md_temp_sa"] = sa
        result["md_temp_ra"] = ra
        # Note: PostAirIndex also has a top-level `Temp` field, but
        # 24-hour comparison confirmed it is byte-for-byte identical to
        # TempRA — a firmware alias, not an independent sensor. Don't
        # expose it as its own entity (would be a duplicate).

        # Heat recovery efficiency: (SA - OA) / (RA - OA) × 100
        # Only meaningful when there's a meaningful temperature gradient.
        if oa is not None and sa is not None and ra is not None:
            gradient = ra - oa
            if abs(gradient) >= 0.5:
                result["md_hrv_efficiency"] = round((sa - oa) / gradient * 100, 1)
            else:
                result["md_hrv_efficiency"] = None
        else:
            result["md_hrv_efficiency"] = None

    async def _async_fetch_filter_alarm(self, session: aiohttp.ClientSession, result: dict) -> None:
        """Fetch filter alarm data and merge into result dict. Only every 10th poll (~10 min)."""
        if "filter_alarm" not in self._api_urls:
            return
        self._poll_count += 1
        if self._poll_count % 30 != 1:
            # Carry over previous filter data
            if self.data:
                for key in ("filter_high_used", "filter_high_alarm", "filter_high_reset",
                            "filter_primary_used", "filter_primary_alarm", "filter_primary_reset"):
                    if key in self.data:
                        result[key] = self.data[key]
            return
        auth_payload = f"u_id={self.user_id}&Mac={self.mac}&AuthCode={self.auth_code}"
        try:
            async with session.post(
                self._api_urls["filter_alarm"],
                data=auth_payload,
                headers=HEADERS,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                text = await response.text()
                data = json.loads(text)
                if data and data[0].get("success"):
                    filt = data[0].get("result", [{}])[0]
                    result["filter_high_used"] = filt.get("HighUsedTime")
                    result["filter_high_alarm"] = filt.get("HighAlarmTime")
                    result["filter_high_reset"] = filt.get("HighResetTime")
                    result["filter_primary_used"] = filt.get("PrimaryUsedTime")
                    result["filter_primary_alarm"] = filt.get("PrimaryAlarmTime")
                    result["filter_primary_reset"] = filt.get("PrimaryResetTime")
        except Exception as err:
            _LOGGER.debug("Filter alarm fetch failed: %s", err)

    async def _async_update_bath_heater(self) -> dict[str, Any]:
        """Fetch bath heater state from getDeviceFunction + getDeviceAirIndex."""
        auth_payload = f"u_id={self.user_id}&Mac={self.mac}&AuthCode={self.auth_code}"
        result: dict[str, Any] = {"_device_model": DEVICE_MODEL_BATH_HEATER}

        async with aiohttp.ClientSession() as session:
            # 1) getDeviceFunction → power, function, speed, countdown
            async with session.post(
                self._api_urls["device_function"],
                data=auth_payload,
                headers=HEADERS,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                text = await response.text()
                data = json.loads(text)
                if data and data[0].get("success"):
                    dev = data[0].get("result", [{}])[0]
                    result["md_ispower"] = int(dev.get("IsPower", 0))
                    # API returned data successfully = device reachable
                    result["md_isconnect"] = 1
                    # Parse Function list for selected values
                    for func_group in dev.get("Function", []):
                        param = func_group.get("Parameters", "")
                        for sub in func_group.get("ParametersSub", []):
                            if param == "Function" and str(sub.get("Selected")) == "1":
                                result["md_function"] = int(sub["Data"])
                            elif param == "Speed" and str(sub.get("Selected")) == "1":
                                result["md_speed"] = str(sub["Data"])
                            elif param == "CountDown":
                                if sub.get("FunctionTitle") == "SetCountDown":
                                    result["md_set_countdown"] = sub.get("Data", "")
                                elif sub.get("FunctionTitle") == "CountDown":
                                    result["md_countdown"] = sub.get("Data", "")

            # 2) getDeviceAirIndex → sensor data
            async with session.post(
                self._api_urls["air_index"],
                data=auth_payload,
                headers=HEADERS,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                text = await response.text()
                data = json.loads(text)
                if data and data[0].get("success"):
                    air = data[0].get("result", [{}])[0]
                    result["md_co2"] = air.get("co2", "")
                    result["md_pm25"] = air.get("pm25", "")
                    result["md_temp"] = air.get("temp", "")
                    result["md_rh"] = air.get("rh", "")

            # 3) getDeviceFilterAlarm → filter data
            await self._async_fetch_filter_alarm(session, result)

        return result

    async def _async_update_m8e_sensor(self) -> dict[str, Any]:
        """Fetch M8-E sensor data from getDeviceAirIndex + real online status."""
        auth_payload = f"u_id={self.user_id}&Mac={self.mac}&AuthCode={self.auth_code}"
        list_payload = (
            f"u_id={self.user_id}&ShareMidno=&AuthCode={self.auth_code}"
        )
        result: dict[str, Any] = {"_device_model": DEVICE_MODEL_M8E_SENSOR}

        async with aiohttp.ClientSession() as session:
            # 1) getDeviceAirIndex → sensor values (always succeeds if
            #    cloud has cached data, even if the device is offline)
            async with session.post(
                self._api_urls["air_index"],
                data=auth_payload,
                headers=HEADERS,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                text = await response.text()
                data = json.loads(text)
                if data and data[0].get("success"):
                    air = data[0].get("result", [{}])[0]
                    result["md_co2"] = air.get("co2", "")
                    result["md_pm25"] = air.get("pm25", "")
                    result["md_temp"] = air.get("temp", "")
                    result["md_rh"] = air.get("rh", "")
                else:
                    raise UpdateFailed("No sensor data received")

            # 2) getDeviceList → real isOnLine status for this MAC.
            #    getDeviceAirIndex doesn't carry isOnLine, so without
            #    this step the sensor always shows "connected" as long
            #    as the cloud has any cached data.
            try:
                async with session.post(
                    self._api_urls["device_list"],
                    data=list_payload,
                    headers=HEADERS,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as response:
                    text = await response.text()
                    data = json.loads(text)
                    if data and data[0].get("success"):
                        for dev in data[0].get("result", []):
                            if dev.get("Mac") == self.mac:
                                result["md_isconnect"] = int(
                                    dev.get("isOnLine", 0)
                                )
                                break
                        else:
                            result["md_isconnect"] = 0
                    else:
                        result["md_isconnect"] = 1  # fallback
            except Exception as err:
                _LOGGER.debug("getDeviceList failed for sensor: %s", err)
                result["md_isconnect"] = 1  # optimistic fallback

        return result

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from API."""
        if self._local_mode:
            return await self._async_update_local()

        if self._model == DEVICE_MODEL_BATH_HEATER:
            try:
                return await self._async_update_bath_heater()
            except Exception as err:
                if not self._relogin_attempted and self._has_cloud_creds:
                    self._relogin_attempted = True
                    if await self._async_relogin():
                        return await self._async_update_bath_heater()
                raise UpdateFailed(f"Bath heater update error: {err}")

        if self._model == DEVICE_MODEL_M8E_SENSOR:
            try:
                return await self._async_update_m8e_sensor()
            except Exception as err:
                if not self._relogin_attempted and self._has_cloud_creds:
                    self._relogin_attempted = True
                    if await self._async_relogin():
                        return await self._async_update_m8e_sensor()
                raise UpdateFailed(f"M8-E sensor update error: {err}")

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
                        result = self._normalize_device_data(device)
                        await self._async_fetch_filter_alarm(session, result)
                        await self._async_fetch_addon_duct_temps(session, result)
                        return result
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
                                    result2 = self._normalize_device_data(device2)
                                    await self._async_fetch_filter_alarm(session, result2)
                                    await self._async_fetch_addon_duct_temps(session, result2)
                                    return result2
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

    async def _async_bath_heater_set_control(
        self,
        ispower: int | None = None,
        function: int | None = None,
        speed: int | None = None,
        countdown: int | None = None,
    ) -> bool:
        """Control bath heater via getDevicePower + getDeviceFunctionEdit.

        App flow: power on → function edit (always, with SetCountDown).
        Power off: just send power off.
        """
        if not self._auth_valid or not self.auth_code:
            return False

        current_data = self.data or {}
        target_function = function if function is not None else current_data.get("md_function", 25)
        target_speed = speed if speed is not None else int(current_data.get("md_speed", 3) or 3)
        target_countdown = countdown if countdown is not None else int(current_data.get("md_set_countdown", 60) or 60)
        countdown_str = str(target_countdown)

        try:
            async with aiohttp.ClientSession() as session:
                # Power off: just send power off, done
                if ispower == 0:
                    power_payload = (
                        f"Mac={self.mac}&u_id={self.user_id}"
                        f"&AuthCode={self.auth_code}"
                        f"&IsPower=0&ShareMidno="
                    )
                    async with session.post(
                        self._api_urls["power"], data=power_payload, headers=HEADERS,
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as response:
                        text = await response.text()
                        _LOGGER.debug("Bath heater power off: %s", text)
                    return True

                # Power on or function/speed change:
                # 1) Send power on
                power_payload = (
                    f"Mac={self.mac}&u_id={self.user_id}"
                    f"&AuthCode={self.auth_code}"
                    f"&IsPower=1&ShareMidno="
                )
                async with session.post(
                    self._api_urls["power"], data=power_payload, headers=HEADERS,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as response:
                    text = await response.text()
                    _LOGGER.debug("Bath heater power on: %s", text)

                await asyncio.sleep(0.3)

                # 2) Send function edit
                control_payload = (
                    f"Mode=&AuthCode={self.auth_code}"
                    f"&Speed={target_speed}&SetCountDown={countdown_str}"
                    f"&u_id={self.user_id}&ShareMidno="
                    f"&Function={target_function}&Mac={self.mac}&Auto=&Mute="
                )
                async with session.post(
                    self._api_urls["control"], data=control_payload, headers=HEADERS,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as response:
                    text = await response.text()
                    _LOGGER.debug("Bath heater function edit: %s", text)

                return True
        except Exception as err:
            _LOGGER.warning("Bath heater control failed: %s", err)
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

    async def async_set_bath_heater_control(
        self,
        ispower: int | None = None,
        function: int | None = None,
        speed: int | None = None,
        countdown: int | None = None,
    ) -> bool:
        """Send control command to bath heater with retry."""
        for attempt in range(3):
            ok = await self._async_bath_heater_set_control(
                ispower=ispower, function=function, speed=speed, countdown=countdown,
            )
            if ok:
                await asyncio.sleep(1.0)
                await self.async_request_refresh()
                return True
            if not self._relogin_attempted and self._has_cloud_creds:
                self._relogin_attempted = True
                await self._async_relogin()
            await asyncio.sleep(0.5)
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
