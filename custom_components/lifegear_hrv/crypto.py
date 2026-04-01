"""AES encryption utilities for Lifegear HRV login."""
from __future__ import annotations

import base64
import json
import logging
import random
from datetime import datetime
from urllib.parse import quote

import aiohttp

from .const import (
    AES_KEY,
    AES_IV,
    HEADERS,
    CONF_DEVICE_ID,
    CONF_MAC,
    DEVICE_MODEL_M8,
    get_api_urls,
    is_m8e_platform,
    detect_device_model,
)

_LOGGER = logging.getLogger(__name__)


def generate_auth_code() -> str:
    """Generate a random 10-digit auth code (digits 1-9)."""
    return "".join(str(random.randint(1, 9)) for _ in range(10))


def encrypt_ra(password: str, auth_code: str) -> str:
    """Encrypt password and auth code into RA parameter for login."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    date_str = datetime.now().strftime("%Y%m%d")
    plaintext = f"LifeGearPJ;;{password};;{auth_code};;{date_str}"

    pt_bytes = plaintext.encode("utf-8")
    # ZeroPadding to multiple of 16
    pad_len = (16 - len(pt_bytes) % 16) % 16
    pt_padded = pt_bytes + b"\x00" * pad_len

    # AES-CBC encryption
    iv_padded = AES_IV + b"\x00" * (16 - len(AES_IV))
    cipher = Cipher(algorithms.AES(AES_KEY), modes.CBC(iv_padded))
    encryptor = cipher.encryptor()
    ct = encryptor.update(pt_padded) + encryptor.finalize()

    return base64.b64encode(ct).decode("utf-8")


async def async_login(
    session: aiohttp.ClientSession,
    account: str,
    password: str,
    model: str = DEVICE_MODEL_M8,
) -> dict:
    """Login to Lifegear API and return u_id, auth_code, device_id, mac.

    Raises ValueError on auth failure, ConnectionError on network failure.
    """
    urls = get_api_urls(model)
    auth_code = generate_auth_code()
    _LOGGER.debug("Generated AuthCode: %s", auth_code)
    try:
        ra = encrypt_ra(password, auth_code)
        _LOGGER.debug("Encrypted RA: %s", ra)
    except Exception as err:
        _LOGGER.error("Encryption failed: %s", err)
        raise ConnectionError(f"Encryption failed: {err}") from err

    # Step 1: Call login.asp (URL-encode RA since it contains +/=/  chars)
    ra_encoded = quote(ra, safe="")
    login_payload = (
        f"os=HomeAssistant&mpMobile={account}"
        f"&mpPhoneType=HA&Vers=2.0.0"
        f"&mpDeviceID=&mpIMEI="
        f"&u_id={account}&mpPhoneSize=0"
        f"&RA={ra_encoded}"
    )

    _LOGGER.debug("Login payload (first 100): %s", login_payload[:100])
    try:
        async with session.post(
            urls["login"],
            data=login_payload.encode("utf-8"),
            headers=HEADERS,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as response:
            text = await response.text()
            _LOGGER.warning("Login response: %s", text)
            result = json.loads(text)

            if not result or len(result) == 0:
                raise ValueError("Empty login response")

            login_data = result[0]
            if not login_data.get("success", False):
                msg = login_data.get("message", "Unknown error")
                raise ValueError(f"Login failed: {msg}")
    except aiohttp.ClientError as err:
        raise ConnectionError(f"Connection error: {err}") from err
    except json.JSONDecodeError as err:
        raise ConnectionError(f"Invalid response: {err}") from err

    # Step 2: Get device list
    _LOGGER.warning("async_login step2: model=%s, is_m8e=%s", model, is_m8e_platform(model))
    if is_m8e_platform(model):
        # M8-E platform: use getDeviceList to get all devices, return first one
        devices = await async_get_device_list(session, account, auth_code, model)
        _LOGGER.warning("getDeviceList returned %d devices: %s", len(devices), [d.get("MachineTitle") for d in devices])
        if not devices:
            raise ValueError("No device found")
        device = devices[0]
        return {
            "u_id": account,
            "auth_code": auth_code,
            CONF_DEVICE_ID: str(device["mdid"]),
            CONF_MAC: device["Mac"],
            "title": device["MachineTitle"],
            "devices": devices,
        }
    else:
        # M8: use getHomeDeviceDetail
        list_payload = f"u_id={account}&AuthCode={auth_code}&ShareMidno="
        try:
            async with session.post(
                urls["list"],
                data=list_payload,
                headers=HEADERS,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                text = await response.text()
                _LOGGER.debug("Device list response: %s", text)
                data = json.loads(text)

                if not data or len(data) == 0:
                    raise ValueError("No device data received")

                device = data[0]
                if not device.get("mdid"):
                    raise ValueError("No device found")

                mac = device.get("md_mac")
                return {
                    "u_id": account,
                    "auth_code": auth_code,
                    CONF_DEVICE_ID: str(device.get("mdid")),
                    CONF_MAC: mac,
                    "title": device.get("md_wisdom") or "樂奇全熱交換機",
                }
        except aiohttp.ClientError as err:
            raise ConnectionError(f"Connection error: {err}") from err
        except json.JSONDecodeError as err:
            raise ConnectionError(f"Invalid response: {err}") from err


async def async_get_device_list(
    session: aiohttp.ClientSession,
    account: str,
    auth_code: str,
    model: str = DEVICE_MODEL_M8,
) -> list[dict]:
    """Get all devices from getDeviceList.asp (M8-E platform).

    Returns list of device dicts with keys: mdid, Mac, MachineTitle, MachineNo, MachineType, GroupIsMain, isOnLine, IsPower.
    """
    urls = get_api_urls(model)
    payload = f"u_id={account}&ShareMidno=&AuthCode={auth_code}"
    try:
        async with session.post(
            urls["device_list"],
            data=payload,
            headers=HEADERS,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as response:
            text = await response.text()
            _LOGGER.debug("getDeviceList response: %s", text)
            data = json.loads(text)

            if not data or len(data) == 0:
                raise ValueError("Empty device list response")

            entry = data[0]
            if not entry.get("success"):
                raise ValueError(f"Device list failed: {entry.get('message')}")

            return entry.get("result", [])
    except aiohttp.ClientError as err:
        raise ConnectionError(f"Connection error: {err}") from err
    except json.JSONDecodeError as err:
        raise ConnectionError(f"Invalid response: {err}") from err
