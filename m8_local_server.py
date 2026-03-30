#!/usr/bin/env python3
"""M8 HRV Local Control Server v3.0.0

Replaces m8.daguan-tech.com.tw for the M8 device, providing:
  - Local handling of all 4 device HTTP endpoints (port 80)
  - REST API for HA integration to read sensor data / send commands (port 8765)
  - No cloud dependency

M8 DNS setup: set DNS in M8 Web UI (admin/admin)
  STA設置 → DNS服务器地址 → set to this machine's IP

Device AES: key=MD5("LifeGear85ls6IsY"), IV=8a39b1993ec8c3dcde502975fd292c7b, CBC+PKCS7
"""
import base64
import hashlib
import json
import logging
import socket
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

try:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad, unpad
except ImportError:
    raise SystemExit("Install pycryptodome: pip3 install pycryptodome")

# ── AES constants ──────────────────────────────────────────────────────────────
DEVICE_KEY = hashlib.md5(b"LifeGear85ls6IsY").digest()
DEVICE_IV  = bytes.fromhex("8a39b1993ec8c3dcde502975fd292c7b")


def device_encrypt(plaintext: str) -> str:
    pt = pad(plaintext.encode("utf-8"), 16)
    cipher = AES.new(DEVICE_KEY, AES.MODE_CBC, DEVICE_IV)
    return base64.b64encode(cipher.encrypt(pt)).decode()


def device_decrypt(b64: str) -> dict | None:
    try:
        ct = base64.b64decode(b64)
        cipher = AES.new(DEVICE_KEY, AES.MODE_CBC, DEVICE_IV)
        pt = unpad(cipher.decrypt(ct), 16)
        return json.loads(pt.decode("utf-8"))
    except Exception as e:
        log.debug("Decrypt error: %s", e)
        return None


# ── Shared state ───────────────────────────────────────────────────────────────
_lock = threading.Lock()

_sensor: dict = {
    "co2":  None, "pm25": None,
    "temp": None, "rh":   None,
    "last_update": None,
}

_device_state: dict = {
    "ispower": None, "mode": None, "speed": None,
    "last_update": None,
}

# Pending command queued by HA.  None = no pending command (echo device state).
_pending_command: dict | None = None


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
        _device_state["ispower"] = data.get("Ispower") or data.get("IsPower")
        _device_state["mode"]    = data.get("Mode")
        _device_state["speed"]   = data.get("Speed")
        _device_state["last_update"] = datetime.now().isoformat()
    log.info("[State] Power=%s Mode=%s Speed=%s",
             _device_state["ispower"], _device_state["mode"], _device_state["speed"])


def _build_command_payload(mac: str) -> str:
    """Return encrypted command JSON for GetDeviceData response.

    If there's a pending HA command, use it (and clear it).
    Otherwise echo the device's reported state so it stays put.
    """
    global _pending_command
    with _lock:
        cmd = _pending_command
        _pending_command = None

    if cmd:
        ispower = bool(cmd.get("ispower", 1))
        mode    = str(cmd.get("mode",  _device_state.get("mode") or 3))
        speed   = str(cmd.get("speed", _device_state.get("speed") or 1))
        log.info("[Cmd→Device] Power=%s Mode=%s Speed=%s", ispower, mode, speed)
    else:
        # Echo current device state; if unknown, leave device as-is (power on, mode 3)
        ispower = bool(_device_state.get("ispower") if _device_state.get("ispower") is not None else True)
        mode    = str(_device_state.get("mode") or 3)
        speed   = str(_device_state.get("speed") or 1)

    payload = {
        "Mac":       mac,
        "Date":      datetime.now().strftime("%Y-%m-%d"),
        "IsPower":   ispower,
        "Mode":      mode,
        "Speed":     speed,
        "Co2":       "0",
        "PM25":      "0",
        "Temp":      "0",
        "RH":        "0",
        "IsReServe": False,
        "STime":     "0",
        "ETime":     "0",
        "IP":        None,
        "IsConnect": True,
        "Version":   "1.0.16",
        "IsUpdate":  False,
    }
    return device_encrypt(json.dumps(payload, separators=(",", ":")))


# ── HTTP handler – port 80 (device endpoints) ─────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
)
log = logging.getLogger("m8-local")


def _parse_form(body_bytes: bytes) -> dict:
    qs = parse_qs(body_bytes.decode("utf-8", errors="replace"), keep_blank_values=True)
    return {k: v[0] for k, v in qs.items()}


class M8Handler(BaseHTTPRequestHandler):
    """Handles all requests from the M8 device on port 80."""

    def log_message(self, fmt, *args):  # suppress default access log
        pass

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length) if length else b""

    def _send_json(self, obj, content_type="application/json", status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/app/getCloudTimes.asp":
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

    def do_POST(self):
        path = urlparse(self.path).path
        body = self._read_body()
        form = _parse_form(body)

        if path == "/api/App/PostDeviceStatus":
            data = device_decrypt(form.get("RA", ""))
            if data:
                _set_sensor(data)
            self._send_json({"ErrorMessage": None, "ResponseCode": 1000, "data": None})

        elif path == "/api/App/PostDeviceData":
            data = device_decrypt(form.get("RA", ""))
            if data:
                _set_device_state(data)
            self._send_json({"ErrorMessage": None, "ResponseCode": 1000, "data": None})

        elif path == "/api/App/GetDeviceData":
            mac_enc = form.get("Mac", "")
            mac_data = device_decrypt(mac_enc)
            mac = mac_data if isinstance(mac_data, str) else (
                str(mac_data) if mac_data else "UNKNOWN"
            )
            # mac_data from decrypting a single block returns the string directly
            try:
                raw = base64.b64decode(mac_enc)
                cipher = AES.new(DEVICE_KEY, AES.MODE_CBC, DEVICE_IV)
                pt = cipher.decrypt(raw)
                from Crypto.Util.Padding import unpad as _unpad
                mac = _unpad(pt, 16).decode("utf-8")
            except Exception:
                mac = "UNKNOWN"

            cmd_enc = _build_command_payload(mac)
            self._send_json({"ErrorMessage": None, "ResponseCode": 1000, "data": cmd_enc})

        else:
            # Unknown device endpoint
            log.warning("Unknown POST: %s", path)
            self._send_json({"ErrorMessage": "Not handled", "ResponseCode": 9999, "data": None})


# ── REST API – port 8765 (for HA integration) ─────────────────────────────────
class RestHandler(BaseHTTPRequestHandler):
    """Simple REST API on port 8765 for HA integration."""

    def log_message(self, fmt, *args):
        pass

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length) if length else b""

    def _send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
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
        elif path == "/api/state":
            with _lock:
                self._send_json(dict(_device_state))
        elif path == "/api/status":
            with _lock:
                self._send_json({
                    "sensor": dict(_sensor),
                    "state":  dict(_device_state),
                    "pending_command": _pending_command,
                })
        else:
            self._send_json({"error": "not found"}, status=404)

    def do_POST(self):
        global _pending_command
        path = urlparse(self.path).path
        if path == "/api/command":
            try:
                body = self._read_body()
                cmd = json.loads(body)
                with _lock:
                    _pending_command = {
                        "ispower": int(cmd.get("ispower", 1)),
                        "mode":    int(cmd.get("mode", 3)),
                        "speed":   int(cmd.get("speed", 1)),
                    }
                log.info("[HA→Cmd] Queued: %s", _pending_command)
                self._send_json({"ok": True})
            except Exception as e:
                self._send_json({"error": str(e)}, status=400)
        else:
            self._send_json({"error": "not found"}, status=404)


def _get_local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


# ── DNS Server ─────────────────────────────────────────────────────────────────
CAPTURE_DOMAIN = b"m8.daguan-tech.com.tw"
REAL_DNS = "8.8.8.8"


def _build_dns_response(data: bytes, redirect_ip: str) -> bytes | None:
    import struct
    tid = data[:2]
    offset = 12
    labels = []
    while data[offset] != 0:
        length = data[offset]
        offset += 1
        labels.append(data[offset:offset + length])
        offset += length
    offset += 1
    qname_end = offset
    qtype = struct.unpack("!H", data[offset:offset + 2])[0]
    domain = b".".join(labels).lower()

    if domain == CAPTURE_DOMAIN.lower() and qtype == 1:
        # Return redirect IP for m8.daguan-tech.com.tw
        resp = tid + b"\x81\x80" + data[4:6] + b"\x00\x01\x00\x00\x00\x00"
        resp += data[12:qname_end + 4]
        resp += b"\xc0\x0c\x00\x01\x00\x01"
        resp += struct.pack("!I", 60)
        resp += b"\x00\x04"
        resp += socket.inet_aton(redirect_ip)
        return resp
    else:
        # Forward all other DNS queries upstream
        try:
            fwd = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            fwd.settimeout(5)
            fwd.sendto(data, (REAL_DNS, 53))
            return fwd.recv(4096)
        except Exception as e:
            log.error("[DNS] Forward failed: %s", e)
            return tid + b"\x81\x82" + data[4:]
        finally:
            fwd.close()


def _dns_server(redirect_ip: str) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", 53))
    log.info("[DNS]      Listening on 0.0.0.0:53")
    while True:
        try:
            data, addr = sock.recvfrom(4096)
            resp = _build_dns_response(data, redirect_ip)
            if resp:
                sock.sendto(resp, addr)
        except Exception as e:
            log.error("[DNS] Error: %s", e)


if __name__ == "__main__":
    local_ip = _get_local_ip()
    log.info("=== M8 Local Control Server v3.0.0 ===")
    log.info("Local IP: %s", local_ip)
    log.info("")
    log.info("Setup: M8 Web UI → STA設置 → DNS服务器地址 → %s", local_ip)
    log.info("       Then reboot M8")
    log.info("")
    log.info("HA integration: set local_server_url = http://%s:8765", local_ip)
    log.info("")

    dns_thread = threading.Thread(target=_dns_server, args=(local_ip,), daemon=True)
    dns_thread.start()

    rest_server = HTTPServer(("0.0.0.0", 8765), RestHandler)
    rest_thread = threading.Thread(target=rest_server.serve_forever, daemon=True)
    rest_thread.start()
    log.info("[REST API] Listening on 0.0.0.0:8765")

    device_server = HTTPServer(("0.0.0.0", 80), M8Handler)
    log.info("[Device]   Listening on 0.0.0.0:80")
    log.info(">>> Ready! <<<")
    log.info("")
    device_server.serve_forever()
