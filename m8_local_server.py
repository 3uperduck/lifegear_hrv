#!/usr/bin/env python3
"""M8 HRV Local Control Server v2.1.1 (M8 + M8-E MitM + per-MAC sensor split)

Replaces m8.daguan-tech.com.tw for the M8 device, providing:
  - Local handling of all 4 device HTTP endpoints (port 80)
  - REST API for HA integration to read sensor data / send commands (port 8765)
  - Built-in DNS server to redirect M8 traffic (port 53)
  - No cloud dependency

Network setup:
  Option A: UDM Pro DNAT rule — redirect M8's port 53 UDP to this machine
  Option B: Set DNS in M8 Web UI (admin/admin) STA設置 → DNS服务器地址

Device AES: key=MD5("LifeGear85ls6IsY"), IV=8a39b1993ec8c3dcde502975fd292c7b, CBC+PKCS7
Cloud command format (from reverse engineering):
  {"IsPower":bool,"Mode":"str","Speed":"str","IsReServe":bool,
   "STime":"str","ETime":"str","Version":"str","IsUpdate":bool,"FirmwareURL":"str"}
"""
import base64
import hashlib
import http.client
import json
import logging
import re
import socket
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

try:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad, unpad
except ImportError:
    raise SystemExit("Install pycryptodome: pip3 install pycryptodome")

# ── AES constants ──────────────────────────────────────────────────────────────
DEVICE_KEY = hashlib.md5(b"LifeGear85ls6IsY").digest()
DEVICE_IV  = bytes.fromhex("8a39b1993ec8c3dcde502975fd292c7b")


def _zero_pad(b: bytes) -> bytes:
    rem = len(b) % 16
    return b if rem == 0 else b + b"\x00" * (16 - rem)


def _zero_unpad(b: bytes) -> bytes:
    return b.rstrip(b"\x00")


def device_encrypt(plaintext: str) -> str:
    """Old M8 CBC+PKCS7 encryption (used for legacy /api/App/* responses)."""
    pt = pad(plaintext.encode("utf-8"), 16)
    cipher = AES.new(DEVICE_KEY, AES.MODE_CBC, DEVICE_IV)
    return base64.b64encode(cipher.encrypt(pt)).decode()


def device_encrypt_ecb(plaintext: str) -> str:
    """M8-E ECB+ZeroPad encryption (used for /api/AppV2/* responses)."""
    pt = _zero_pad(plaintext.encode("utf-8"))
    cipher = AES.new(DEVICE_KEY, AES.MODE_ECB)
    return base64.b64encode(cipher.encrypt(pt)).decode()


def device_decrypt(b64: str) -> dict | None:
    """Decrypt and parse as JSON dict (tries ECB then CBC)."""
    text = device_decrypt_raw(b64)
    if text:
        try:
            return json.loads(text)
        except Exception:
            return None
    return None


def device_decrypt_raw(b64: str) -> str | None:
    """Decrypt base64 AES ciphertext. Tries ECB+ZeroPad (M8-E) first, then CBC+PKCS7 (M8)."""
    try:
        ct = base64.b64decode(b64)
        if len(ct) % 16 != 0:
            return None
        # Try M8-E: ECB + ZeroPad
        try:
            pt = AES.new(DEVICE_KEY, AES.MODE_ECB).decrypt(ct)
            text = _zero_unpad(pt).decode("utf-8", errors="replace")
            if _looks_valid(text):
                return text
        except Exception:
            pass
        # Fallback to M8: CBC + PKCS7
        pt = AES.new(DEVICE_KEY, AES.MODE_CBC, DEVICE_IV).decrypt(ct)
        try:
            pt = unpad(pt, 16)
        except ValueError:
            pt = _zero_unpad(pt)
        text = pt.decode("utf-8", errors="replace")
        return text if _looks_valid(text) else None
    except Exception as e:
        log.debug("Decrypt error: %s", e)
        return None


def _looks_valid(text: str) -> bool:
    """Heuristic: valid if JSON-ish or printable MAC/ID string."""
    if not text:
        return False
    # JSON object
    if "{" in text and "}" in text:
        return True
    # MAC address pattern
    if ":" in text and all(c in "0123456789ABCDEFabcdef:" for c in text.strip("\x00").strip()):
        return True
    # Mostly printable ASCII
    printable = sum(1 for c in text if c.isprintable() or c in "\n\r\t")
    return printable / max(len(text), 1) > 0.8


# ── Shared state ───────────────────────────────────────────────────────────────
_lock = threading.Lock()

# Per-MAC sensor state. Each device pushes its own PostAirIndex with different
# fields. The legacy M8 HRV (daguan cloud) and the new M8-E HRV both send CO2,
# PM2.5, Temp, RH. But under the M8-E pairing model, a single "HRV" in the cloud
# is actually TWO independent ESP devices: the HRV unit itself only pushes duct
# temperatures (Temp / TempOA / TempSA / TempRA), and a separate wall-mounted
# M8-E sensor pushes air quality (Co2 / PM25 / Temp / RH). The cloud merges them
# by user account so the app sees a single combined record.
#
# Keep one entry per source MAC so readers can tell where each field came from.
_SENSOR_TEMPLATE = {
    "co2": None, "pm25": None,
    "temp": None, "rh": None,
    "temp_oa": None,   # outside air intake
    "temp_sa": None,   # supply air (post heat-exchange, into room)
    "temp_ra": None,   # return air (from room)
    "temp_ex": None,   # exhaust air (post heat-exchange, to outside)
    "last_update": None,
}
_sensor_by_mac: dict[str, dict] = {}

# Back-compat: merged view across all sources. Populated by _rebuild_merged_sensor().
# Field selection: duct temps come from whichever MAC pushes them (the HRV ESP);
# air quality (co2/pm25/rh) and wall Temp come from the "richest" source.
_sensor: dict = dict(_SENSOR_TEMPLATE)

_device_state: dict = {
    "ispower": None, "mode": None, "speed": None,
    "last_update": None,
}

_device_info: dict = {
    "device_id": None, "mac": None,
}

_cloud_auth: dict = {
    "u_id": None, "auth_code": None,
    "captured_at": None,
}

_pending_command: dict | None = None
_pending_command_time: float = 0



def _set_sensor(data: dict) -> None:
    with _lock:
        _sensor["co2"]  = data.get("Co2")
        _sensor["pm25"] = data.get("PM25")
        _sensor["temp"] = data.get("Temp")
        _sensor["rh"]   = data.get("RH")
        _sensor["last_update"] = datetime.now().isoformat()
    log.info("[Status] CO2=%s PM2.5=%s Temp=%s RH=%s",
             _sensor["co2"], _sensor["pm25"], _sensor["temp"], _sensor["rh"])


def _set_device_state(data: dict) -> None:
    with _lock:
        _device_state["ispower"] = data.get("Ispower") if "Ispower" in data else data.get("IsPower")
        _device_state["mode"]    = data.get("Mode")
        _device_state["speed"]   = data.get("Speed")
        _device_state["last_update"] = datetime.now().isoformat()
    log.info("[State] Power=%s Mode=%s Speed=%s",
             _device_state["ispower"], _device_state["mode"], _device_state["speed"])


def _build_command_payload() -> str:
    """Return encrypted command JSON for GetDeviceData response.

    Uses cloud-compatible format discovered via reverse engineering.
    If there's a pending HA command, send it repeatedly until device confirms.
    Otherwise echo a safe default state.
    """
    global _pending_command
    with _lock:
        cmd = _pending_command

    if cmd:
        ispower = bool(cmd.get("ispower", 1))
        mode    = int(cmd.get("mode", 3))
        speed   = int(cmd.get("speed", 1))
        # Use device's actual mode value if it matches the requested mode category
        # M8 internal modes: 17=自動, 18=淨化, 19=全熱 (alternates with 1/2/3)
        dev_mode = _device_state.get("mode")
        dev_speed = _device_state.get("speed")
        if dev_speed == speed and dev_mode is not None:
            # Check if device mode matches requested mode (either cloud or internal value)
            dev_mode_int = int(dev_mode) if dev_mode is not None else None
            mode_matches = dev_mode_int in (mode, mode + 16)  # 1↔17, 2↔18, 3↔19
            if mode_matches:
                _pending_command = None
                log.info("[Cmd✓] Device confirmed: Power=%s Mode=%s Speed=%s", ispower, dev_mode_int, speed)
            else:
                log.info("[Cmd→Device] Power=%s Mode=%s Speed=%s (dev_mode=%s)", ispower, mode, speed, dev_mode_int)
        else:
            log.info("[Cmd→Device] Power=%s Mode=%s Speed=%s (dev_speed=%s)", ispower, mode, speed, dev_speed)
    else:
        ispower = bool(_device_state.get("ispower") if _device_state.get("ispower") is not None else True)
        # Echo device's current mode (use cloud-style value)
        dev_mode = _device_state.get("mode")
        if dev_mode is not None:
            mode = int(dev_mode)
        else:
            mode = 3
        speed   = _device_state.get("speed") or 1

    payload = {
        "IsPower":     ispower,
        "Mode":        str(mode),
        "Speed":       str(speed),
        "IsReServe":   False,
        "STime":       "0",
        "ETime":       "0",
        "Version":     "1.0.16",
        "IsUpdate":    False,
        "FirmwareURL": "http://m8.daguan-tech.com.tw/Firmware/LIFEGEAR_v1.0.16.bin",
    }
    return device_encrypt(json.dumps(payload, separators=(",", ":")))


# ── HTTP handler – port 80 (device endpoints) ─────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
)
log = logging.getLogger("m8-local")


CLOUD_HOST = "61.31.209.215"
CLOUD_BASE = f"http://{CLOUD_HOST}"
CLOUD_HOST_M8  = "m8.daguan-tech.com.tw"   # legacy M8 vhost
CLOUD_HOST_M8E = "dm03.e-giant.com.tw"     # new M8-E vhost (same IP)


def _forward_to_cloud(method: str, path: str, body: bytes = b"",
                       headers: dict | None = None,
                       host_header: str = CLOUD_HOST_M8) -> bytes | None:
    """Forward a request to the real cloud server, return raw response body.

    `host_header` picks the virtual host — CLOUD_HOST_M8 for legacy /api/App/*
    paths, CLOUD_HOST_M8E for M8-E /api/AppV2/* paths.
    """
    try:
        conn = http.client.HTTPConnection(CLOUD_HOST, 80, timeout=5)
        hdrs = {"Host": host_header}
        if headers:
            hdrs.update(headers)
        conn.request(method, path, body=body, headers=hdrs)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        log.debug("[Proxy] %s %s (%s) → %d (%d bytes)",
                  method, path, host_header, resp.status, len(data))
        return data
    except Exception as e:
        log.warning("[Proxy] Forward failed: %s %s → %s", method, path, e)
        return None


def _set_sensor_m8e(data: dict, mac: str | None = None) -> None:
    """Update per-MAC sensor state from a PostAirIndex plaintext payload.

    Each paired device only reports the fields it has wired: the HRV unit
    pushes duct temperatures, a wall-mounted M8-E sensor pushes CO2/PM25/RH.
    We store both under their own MAC and rebuild a merged view for readers.
    """
    mac_key = (mac or data.get("Mac") or "unknown").upper()
    with _lock:
        slot = _sensor_by_mac.setdefault(mac_key, dict(_SENSOR_TEMPLATE))
        for in_key, out_key in (
            ("Co2", "co2"), ("PM25", "pm25"),
            ("Temp", "temp"), ("RH", "rh"),
            ("TempOA", "temp_oa"), ("TempSA", "temp_sa"),
            ("TempRA", "temp_ra"), ("TempEX", "temp_ex"),
        ):
            if in_key in data:
                slot[out_key] = data.get(in_key)
        slot["last_update"] = datetime.now().isoformat()
        _rebuild_merged_sensor()
    log.info("[AirIndex %s] CO2=%s PM2.5=%s Temp=%s RH=%s OA=%s SA=%s RA=%s EX=%s",
             mac_key[-8:] if mac_key != "UNKNOWN" else "unknown",
             data.get("Co2"), data.get("PM25"), data.get("Temp"), data.get("RH"),
             data.get("TempOA"), data.get("TempSA"), data.get("TempRA"), data.get("TempEX"))


def _rebuild_merged_sensor() -> None:
    """Combine all per-MAC slots into the legacy `_sensor` view.

    Caller must already hold `_lock`.

    Field selection priority:
      * duct temps (temp_oa/sa/ra/ex) — take from whichever slot has them
      * co2/pm25/rh — take from whichever slot has them (wall sensor usually)
      * temp — prefer a slot that also has TempRA (HRV duct reading); fall back
        to any slot with a temp value
      * last_update — most recent across all contributing slots
    """
    merged = dict(_SENSOR_TEMPLATE)
    best_temp_slot = None
    latest_ts = None
    for slot in _sensor_by_mac.values():
        for k in ("co2", "pm25", "rh",
                  "temp_oa", "temp_sa", "temp_ra", "temp_ex"):
            if slot.get(k) is not None:
                merged[k] = slot[k]
        if slot.get("temp") is not None:
            if slot.get("temp_ra") is not None or best_temp_slot is None:
                merged["temp"] = slot["temp"]
                best_temp_slot = slot
        ts = slot.get("last_update")
        if ts and (latest_ts is None or ts > latest_ts):
            latest_ts = ts
    merged["last_update"] = latest_ts
    _sensor.clear()
    _sensor.update(merged)


def _set_device_state_m8e(data: dict) -> None:
    """Update shared device state from M8-E GetDeviceData plaintext."""
    with _lock:
        _device_state["ispower"] = data.get("IsPower")
        _device_state["mode"]    = data.get("Mode")
        _device_state["speed"]   = data.get("Speed")
        _device_state["last_update"] = datetime.now().isoformat()
    log.info("[DeviceData] Power=%s Mode=%s Speed=%s Func=%s Auto=%s valve=%s",
             data.get("IsPower"), data.get("Mode"), data.get("Speed"),
             data.get("Function"), data.get("Auto"), data.get("valveangle"))


def _inject_appv2_command(cloud_resp: bytes) -> bytes:
    """If a pending HA command exists, replace the encrypted data field in
    the cloud's GetDeviceData response with an ECB-encrypted modified version.
    Leaves the outer cloud JSON envelope intact."""
    global _pending_command
    with _lock:
        cmd = _pending_command
    if not cmd:
        return cloud_resp
    try:
        j = json.loads(cloud_resp)
    except Exception:
        return cloud_resp
    enc_data = j.get("data") if isinstance(j, dict) else None
    if not isinstance(enc_data, str) or not enc_data:
        return cloud_resp
    plain = device_decrypt_raw(enc_data)
    if not plain:
        return cloud_resp
    try:
        obj = json.loads(plain)
    except Exception:
        return cloud_resp
    # Apply overrides
    obj["IsPower"] = "1" if int(cmd.get("ispower", 1)) else "0"
    obj["Mode"]    = str(int(cmd.get("mode", 2)))
    obj["Speed"]   = str(int(cmd.get("speed", 1)))
    new_plain = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
    new_enc = device_encrypt_ecb(new_plain)
    # Replace encrypted data value in raw bytes preserving envelope
    injected = cloud_resp.replace(
        enc_data.encode("utf-8"), new_enc.encode("utf-8")
    )
    log.info("[HA→M8-E] Inject IsPower=%s Mode=%s Speed=%s (plain=%s)",
             obj["IsPower"], obj["Mode"], obj["Speed"], new_plain)
    # Clear pending if current device state already matches
    with _lock:
        ds = _device_state
        if (str(ds.get("ispower")) == obj["IsPower"] and
            str(ds.get("mode"))    == obj["Mode"] and
            str(ds.get("speed"))   == obj["Speed"]):
            _pending_command = None
            log.info("[Cmd✓] Device matches target, cleared pending")
    return injected


def _parse_form(body_bytes: bytes) -> dict:
    qs = parse_qs(body_bytes.decode("utf-8", errors="replace"), keep_blank_values=True)
    return {k: v[0] for k, v in qs.items()}


class M8Handler(BaseHTTPRequestHandler):
    """Handles all requests from the M8 device on port 80."""
    protocol_version = "HTTP/1.1"
    timeout = 30

    def log_message(self, fmt, *args):
        pass

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length) if length else b""

    def _send_json(self, obj, content_type="application/json", status=200):
        body = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _proxy_response(self, method, path, body=b""):
        """Forward to cloud and send response back to M8."""
        ct = self.headers.get("Content-Type", "")
        cloud_resp = _forward_to_cloud(method, path, body,
                                        {"Content-Type": ct} if ct else None)
        if cloud_resp:
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(cloud_resp)))
            self.end_headers()
            self.wfile.write(cloud_resp)
        else:
            # Cloud unreachable, fall back to local response
            return False
        return True

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/app/getCloudTimes.asp":
            # Try cloud first, fall back to local
            body = self._read_body()
            if not self._proxy_response("GET", self.path):
                now = datetime.now()
                resp = [{
                    "message": "99.取值成功!",
                    "success": True,
                    "result": [{
                        "CloudDate": now.strftime("%Y/%m/%d"),
                        "CloudTime": now.strftime("%H:%M"),
                    }],
                }]
                self._send_json(resp, content_type="text/html")
        else:
            self.send_response(404)
            self.end_headers()

    def _proxy_or_local(self, method, path, body):
        """Forward to cloud; if cloud is down, return local OK response."""
        cloud_resp = _forward_to_cloud(method, path, body,
                                        {"Content-Type": "application/x-www-form-urlencoded"})
        if cloud_resp:
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(cloud_resp)))
            self.end_headers()
            self.wfile.write(cloud_resp)
        else:
            self._send_json({"ErrorMessage":"OK","ResponseCode":200,"data":None})

    def _save_device_info(self, form: dict) -> None:
        """Extract device_id, mac, and cloud auth from POST form data."""
        # Device info from M8
        mdid = form.get("mdid") or form.get("device_id") or form.get("DeviceId")
        mac = form.get("md_mac") or form.get("Mac") or form.get("mac")
        if mdid or mac:
            with _lock:
                if mdid:
                    _device_info["device_id"] = mdid
                if mac:
                    _device_info["mac"] = mac
            log.info("[DeviceInfo] device_id=%s mac=%s", mdid, mac)

        # Cloud auth from APP requests (u_id + AuthCode)
        u_id = form.get("u_id")
        auth_code = form.get("AuthCode")
        if u_id and auth_code:
            with _lock:
                _cloud_auth["u_id"] = u_id
                _cloud_auth["auth_code"] = auth_code
                _cloud_auth["captured_at"] = datetime.now().isoformat()
            log.info("[Auth] Captured u_id=%s AuthCode=%s...", u_id, auth_code[:8])

    def do_POST(self):
        path = urlparse(self.path).path
        body = self._read_body()
        form = _parse_form(body)
        self._save_device_info(form)

        if path == "/api/App/PostDeviceStatus":
            data = device_decrypt(form.get("RA", ""))
            if data:
                _set_sensor(data)
            self._proxy_or_local("POST", path, body)

        elif path == "/api/App/PostDeviceData":
            data = device_decrypt(form.get("RA", ""))
            if data:
                _set_device_state(data)
            self._proxy_or_local("POST", path, body)

        elif path == "/api/App/GetDeviceData":
            global _pending_command
            # Always get cloud response first
            cloud_resp = _forward_to_cloud("POST", path, body,
                                            {"Content-Type": "application/x-www-form-urlencoded"})
            cloud_data_enc = None  # encrypted data field from cloud
            if cloud_resp:
                try:
                    cloud_json = json.loads(cloud_resp)
                    cloud_data_enc = cloud_json.get("data")
                    if cloud_data_enc:
                        cloud_dec = device_decrypt(cloud_data_enc)
                        log.info("[Cloud→M8] %s", cloud_dec)
                except Exception:
                    pass

            with _lock:
                cmd = _pending_command

            if cmd and cloud_resp and cloud_data_enc:
                # Inject: replace "data" value in cloud's RAW response bytes
                # This preserves exact cloud JSON format (compact, key order, etc.)
                cloud_raw_text = device_decrypt_raw(cloud_data_enc)
                if cloud_raw_text:
                    modified = cloud_raw_text
                    modified = re.sub(r'"Speed"\s*:\s*"[^"]*"',
                                      f'"Speed":"{cmd.get("speed", 1)}"', modified)
                    modified = re.sub(r'"Mode"\s*:\s*"[^"]*"',
                                      f'"Mode":"{cmd.get("mode", 3)}"', modified)
                    power_str = "true" if cmd.get("ispower", 1) else "false"
                    modified = re.sub(r'"IsPower"\s*:\s*(true|false)',
                                      f'"IsPower":{power_str}', modified)
                    new_enc = device_encrypt(modified)
                    # Replace data value in cloud's raw bytes, preserving outer format
                    injected = cloud_resp.replace(
                        cloud_data_enc.encode() if isinstance(cloud_data_enc, str)
                        else cloud_data_enc,
                        new_enc.encode(),
                    )
                    log.info("[HA→M8] Injecting speed=%s mode=%s (len %d→%d)",
                             cmd.get("speed"), cmd.get("mode"),
                             len(cloud_resp), len(injected))
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(injected)))
                    self.end_headers()
                    self.wfile.write(injected)
                else:
                    # Can't decrypt cloud data, send raw cloud response
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(cloud_resp)))
                    self.end_headers()
                    self.wfile.write(cloud_resp)
                # Log device state (pending stays until replaced by new command)
                dev_speed = _device_state.get("speed")
                dev_mode = _device_state.get("mode")
                log.debug("[Cmd] Injecting: target speed=%s mode=%s, device speed=%s mode=%s",
                          cmd.get("speed"), cmd.get("mode"), dev_speed, dev_mode)
            elif cmd:
                # Cloud unreachable + HA command → pure local mode
                cmd_enc = _build_command_payload()
                self._send_json({"ErrorMessage":"OK","ResponseCode":200,"data":cmd_enc})
            elif cloud_resp:
                # No HA command → pass through cloud response exactly
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(cloud_resp)))
                self.end_headers()
                self.wfile.write(cloud_resp)
            else:
                # Cloud unreachable + no command → echo current state
                cmd_enc = _build_command_payload()
                self._send_json({"ErrorMessage":"OK","ResponseCode":200,"data":cmd_enc})

        elif path.startswith("/api/AppV2/"):
            self._handle_appv2(path, body, form)

        else:
            # Unknown → proxy to legacy M8 cloud as best effort
            log.info("[Proxy] Unknown: %s", path)
            if not self._proxy_response("POST", path, body):
                self._send_json({"ErrorMessage": "Not handled", "ResponseCode": 9999, "data": None})

    def _handle_appv2(self, path: str, body: bytes, form: dict) -> None:
        """Handle M8-E device HTTP requests (AES-ECB + dm03.e-giant.com.tw).

        Pipeline:
          1. Decrypt incoming RA/Mac, update shared sensor/device state.
          2. Forward original body to dm03 cloud with correct Host header.
          3. For GetDeviceData, decode cloud response and optionally inject
             pending HA command by rewriting the encrypted data field.
          4. Return (possibly modified) cloud response to the device.
        """
        global _pending_command
        endpoint = path.rsplit("/", 1)[-1]

        # 1. Incoming payload
        ra_b64 = form.get("RA")
        mac_b64 = form.get("Mac")
        req_obj = None
        if ra_b64:
            req_obj = device_decrypt(ra_b64)
            if req_obj is None:
                log.debug("[AppV2 REQ] %s RA undecryptable: %s", path, ra_b64[:40])
        # Source MAC resolution: prefer encrypted Mac form field (Get* requests),
        # fall back to Mac inside the decrypted RA body (Post* requests).
        source_mac: str | None = None
        if mac_b64:
            mac_plain = device_decrypt_raw(mac_b64)
            if mac_plain:
                source_mac = mac_plain.strip()
                with _lock:
                    _device_info["mac"] = source_mac
        if source_mac is None and isinstance(req_obj, dict):
            raw_mac = req_obj.get("Mac")
            if isinstance(raw_mac, str) and raw_mac:
                source_mac = raw_mac.strip()

        if endpoint == "PostAirIndex" and req_obj:
            _set_sensor_m8e(req_obj, mac=source_mac)
        elif endpoint == "PostDeviceConsumablesTime" and req_obj:
            log.info("[Consumables %s] %s",
                     (source_mac or "unknown")[-8:], req_obj)
        elif endpoint == "PostDeviceData" and req_obj:
            _set_device_state_m8e(req_obj)

        # 2. Forward to dm03 cloud
        cloud_resp = _forward_to_cloud(
            "POST", path, body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            host_header=CLOUD_HOST_M8E,
        )

        # 3. Decode cloud response to update state (GetDeviceData) & inject
        if cloud_resp:
            try:
                jr = json.loads(cloud_resp)
                data_enc = jr.get("data") if isinstance(jr, dict) else None
                if isinstance(data_enc, str) and data_enc:
                    plain = device_decrypt(data_enc)
                    if plain and endpoint == "GetDeviceData":
                        _set_device_state_m8e(plain)
            except Exception:
                pass

            if endpoint == "GetDeviceData":
                cloud_resp = _inject_appv2_command(cloud_resp)

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(cloud_resp)))
            self.end_headers()
            self.wfile.write(cloud_resp)
        else:
            # Cloud unreachable → minimal OK envelope so device keeps functioning
            self._send_json({"ErrorMessage": "OK", "ResponseCode": 200, "data": None})


# ── REST API – port 8765 (for HA integration) ─────────────────────────────────
class RestHandler(BaseHTTPRequestHandler):
    """Simple REST API on port 8765 for HA integration."""

    def log_message(self, fmt, *args):
        pass

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length) if length else b""

    def _send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/sensor":
            with _lock:
                self._send_json(dict(_sensor))
        elif path == "/api/sensor/by_mac":
            with _lock:
                self._send_json({mac: dict(slot) for mac, slot in _sensor_by_mac.items()})
        elif path == "/api/state":
            with _lock:
                self._send_json(dict(_device_state))
        elif path == "/api/status":
            with _lock:
                self._send_json({
                    "sensor": dict(_sensor),
                    "sensor_by_mac": {mac: dict(slot) for mac, slot in _sensor_by_mac.items()},
                    "state":  dict(_device_state),
                    "pending_command": _pending_command,
                })
        elif path == "/api/device_info":
            with _lock:
                self._send_json({
                    **_device_info,
                    "auth_available": bool(_cloud_auth.get("u_id")),
                })
        elif path == "/api/auth":
            with _lock:
                self._send_json(dict(_cloud_auth))
        else:
            self._send_json({"error": "not found"}, status=404)

    @staticmethod
    def _send_cloud_command(target: dict) -> bool:
        """Send command to cloud via getDeviceMod.asp using captured auth."""
        with _lock:
            u_id = _cloud_auth.get("u_id")
            auth_code = _cloud_auth.get("auth_code")
            device_id = _device_info.get("device_id")
            mac = _device_info.get("mac")
        if not u_id or not auth_code:
            return False
        payload = (
            f"u_id={u_id}&AuthCode={auth_code}"
            f"&mdid={device_id or ''}&md_mac={mac or ''}"
            f"&md_ispower={target['ispower']}&md_isconnect=1"
            f"&md_mode={target['mode']}&md_speed={target['speed']}"
            f"&md_isreserve=1&md_stime=255&md_etime=255&md_isUse=1"
        )
        resp = _forward_to_cloud(
            "POST", "/app/getDeviceMod.asp", payload.encode(),
            {"Content-Type": "application/x-www-form-urlencoded"},
        )
        if resp:
            log.info("[HA→Cloud] getDeviceMod ok: power=%s mode=%s speed=%s",
                     target["ispower"], target["mode"], target["speed"])
            return True
        log.warning("[HA→Cloud] getDeviceMod failed")
        return False

    def do_POST(self):
        global _pending_command, _pending_command_time
        path = urlparse(self.path).path
        if path == "/api/command":
            try:
                body = self._read_body()
                cmd = json.loads(body)
                target = {
                    "ispower": int(cmd.get("ispower", 1)),
                    "mode":    int(cmd.get("mode", 3)),
                    "speed":   int(cmd.get("speed", 1)),
                }
                # Try cloud API first (if auth is available)
                cloud_ok = self._send_cloud_command(target)
                if not cloud_ok:
                    # Fallback: inject via GetDeviceData
                    with _lock:
                        _pending_command = target
                        _pending_command_time = time.time()
                    log.info("[HA→Cmd] No auth/cloud, using injection: %s", target)
                self._send_json({"ok": True, "cloud": cloud_ok})
            except Exception as e:
                self._send_json({"error": str(e)}, status=400)
        elif path == "/api/command/clear":
            with _lock:
                _pending_command = None
            log.info("[HA→Cmd] Cleared pending command")
            self._send_json({"ok": True})
        else:
            self._send_json({"error": "not found"}, status=404)


if __name__ == "__main__":
    log.info("=== M8 Local Control Server v2.1.1 (M8 + M8-E MitM + per-MAC sensor split) ===")

    rest_server = ThreadingHTTPServer(("0.0.0.0", 8765), RestHandler)
    rest_thread = threading.Thread(target=rest_server.serve_forever, daemon=True)
    rest_thread.start()
    log.info("[REST API] Listening on 0.0.0.0:8765")

    device_server = ThreadingHTTPServer(("0.0.0.0", 80), M8Handler)
    log.info("[Device]   Listening on 0.0.0.0:80")
    log.info(">>> Ready! <<<")
    log.info("")
    device_server.serve_forever()
