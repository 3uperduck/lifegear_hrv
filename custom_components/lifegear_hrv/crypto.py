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
    API_LOGIN,
    API_GET_STATUS,
    HEADERS,
    CONF_DEVICE_ID,
    CONF_MAC,
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
) -> dict:
    """Login to Lifegear API and return u_id, auth_code, device_id, mac.

    Raises ValueError on auth failure, ConnectionError on network failure.
    """
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
            API_LOGIN,
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

    # Step 2: Call getHomeDeviceDetail.asp to get device info
    status_payload = f"u_id={account}&AuthCode={auth_code}"
    try:
        async with session.post(
            API_GET_STATUS,
            data=status_payload,
            headers=HEADERS,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as response:
            text = await response.text()
            _LOGGER.debug("Device detail response: %s", text)
            data = json.loads(text)  # noqa: F811

            if not data or len(data) == 0:
                raise ValueError("No device data received")

            device = data[0]
            if not device.get("mdid"):
                raise ValueError("No device found")

            return {
                "u_id": account,
                "auth_code": auth_code,
                CONF_DEVICE_ID: str(device.get("mdid")),
                CONF_MAC: device.get("md_mac"),
                "title": device.get("md_wisdom") or "樂奇全熱交換機",
            }
    except aiohttp.ClientError as err:
        raise ConnectionError(f"Connection error: {err}") from err
    except json.JSONDecodeError as err:
        raise ConnectionError(f"Invalid response: {err}") from err
