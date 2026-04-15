# M8 / M8-E HRV Local Server

MitM proxy add-on for Lifegear HRV systems. Intercepts the ESP-based HRV (and the optional M8-E wall sensor) talking to the manufacturer cloud (`m8.daguan-tech.com.tw` / `dm03.e-giant.com.tw`, both `61.31.209.215`) so Home Assistant can:

- Read the duct temperatures (`TempOA` / `TempSA` / `TempRA`) that the public cloud API strips out
- Inject control commands directly into the device's cloud-poll response without waiting for a cloud round-trip
- Keep working when the cloud is down (the original mobile app keeps working too — traffic is transparently proxied)

Designed to pair with the [Lifegear HRV HA integration](https://github.com/3uperduck/lifegear_hrv) but the REST API is plain JSON so any client can use it.

## How it works

```
                                    61.31.209.215:80
                                  ┌──────────────────┐
HRV ESP-07 ──┐                    │ m8.daguan-tech   │  legacy M8
             ├─ [UDM DNAT] ──→    │ dm03.e-giant     │  M8-E
M8-E ESP ────┘                    └──────────────────┘
              src=192.168.x.0/24            ▲
              dst=61.31.209.215:80          │  Host header decides
              redirect → HA:80              │  which virtual host
                                            │
                                            │  forwarded by addon
                                ┌───────────┴────────────┐
                                │  this addon (host_net) │
                                │  :80   M8 protocol     │
                                │  :8765 REST API        │
                                └───────────┬────────────┘
                                            │
                                            ▼
                                    HA `lifegear_hrv` integration
                                    polls /api/sensor/by_mac
```

The addon listens on host port 80, forwards every request unchanged to the real cloud at `61.31.209.215`, decodes the AES-encrypted body so HA can see real values, and rewrites the encrypted response on the fly when HA has a pending control command.

### Supported protocols

- **Legacy M8 (智慧果)** — `/api/App/PostDeviceStatus`, `/api/App/PostDeviceData`, `/api/App/GetDeviceData` — AES-CBC with the original IV
- **M8-E (隱藏式 HRV / 樂奇智慧果 M8-E sensor)** — `/api/AppV2/PostAirIndex`, `/PostDeviceConsumablesTime`, `/PostDeviceData`, `/GetDeviceData`, `/GetAirIndex`, `/GetDeviceConsumablesTime`, `/GetAppointment` — AES-ECB with zero padding

Both share the same MD5(`LifeGear85ls6IsY`) AES key. The addon auto-detects which mode applies and falls back gracefully.

### Per-MAC sensor split

Under the M8-E pairing model, the HRV unit and a wall-mounted M8-E air sensor are *two separate ESP devices* with their own MACs. The HRV pushes only duct temperatures (`Temp` / `TempOA` / `TempSA` / `TempRA`); the M8-E wall sensor pushes only `Co2` / `PM25` / `Temp` / `RH`. The cloud merges them by user account; the addon stores them per-MAC and rebuilds a merged view for `/api/sensor` while exposing the raw slots at `/api/sensor/by_mac`.

### HRV-only device state filter

Both the HRV main unit and the M8-E sensor module poll the same `GetDeviceData` endpoint. The cloud serves each one a different per-MAC record — the M8-E sensor's response is a stub with default Mode/Speed values that has nothing to do with the HRV's real state. The addon detects HRV by the presence of `valveangle` / `Function` in the decrypted response and ignores the rest, so the stored device state stays coherent and command-injection state matching keeps working.

## Prerequisites

1. **Layer-3 router with destination NAT** capable of redirecting TCP traffic by source-subnet + destination-IP. UDM Pro / UniFi Network is what this was developed against, but anything with iptables-style DNAT works.

2. **DNAT rule**:
   - Protocol: TCP
   - Source: the IoT subnet your HRV/M8-E live in (e.g. `192.168.10.0/24`)
   - Destination IP: `61.31.209.215` port `80`
   - Translated to: Home Assistant host IP port `80`

3. **Firewall**: allow `IoT subnet → HA host` on TCP/80 (the post-NAT direction). On UniFi this is a `LAN In` accept rule with **Before Predefined** checked.

4. **DNS**: leave it alone. Devices resolve `m8.daguan-tech.com.tw` / `dm03.e-giant.com.tw` to `61.31.209.215` via their normal upstream — the addon does not run a DNS server.

5. **Two ESPs reset state if the cloud answers garbage.** After enabling the DNAT rule for the first time (or after addon code that changes response handling), power-cycle the HRV and M8-E to clear any stuck TCP state in the device firmware.

## REST API (port 8765)

| Endpoint | Method | Description |
|---|---|---|
| `/api/sensor` | GET | Merged view: best-of duct temps + air quality across all sources |
| `/api/sensor/by_mac` | GET | Raw per-MAC slots (debug + clients that want to know the source) |
| `/api/state` | GET | HRV control state (power / mode / speed) decoded from cloud responses |
| `/api/status` | GET | Everything: sensor + sensor_by_mac + state + pending_command |
| `/api/device_info` | GET | Last seen MAC + auth status |
| `/api/auth` | GET | Captured cloud `u_id` / `AuthCode` (auto-extracted from app traffic) |
| `/api/command` | POST | Queue a control command for HRV (see below) |
| `/api/command/clear` | POST | Drop the pending command without sending it |

### `/api/sensor` response

```json
{
  "co2": "920",       "pm25": "5",
  "temp": "27",       "rh": "62",
  "temp_oa": "28",    "temp_sa": "27",
  "temp_ra": "27",    "temp_ex": null,
  "last_update": "2026-04-15T01:23:45"
}
```

### `/api/sensor/by_mac` response

```json
{
  "AA:BB:CC:11:22:33": {  "temp": "27", "temp_oa": "28", "temp_sa": "27", "temp_ra": "27",
                          "co2": null, "pm25": null, "rh": null, "...": null },
  "AA:BB:CC:44:55:66": {  "co2": "920", "pm25": "5", "temp": "33", "rh": "62",
                          "temp_oa": null, "temp_sa": null, "temp_ra": null, "...": null }
}
```

### Command format

```json
{ "ispower": 1, "mode": 2, "speed": 3 }
```

| Field | Legacy M8 values | M8-E values |
|---|---|---|
| `ispower` | `0` off, `1` on | `0` off, `1` on |
| `mode` | `1` 自動 `2` 淨化 `3` 全熱 | `1` 淨化 `2` 新風 `3` 節能 |
| `speed` | `1` 弱 `2` 中 `3` 強 `4` 最大 | same |

The addon stores it as the pending command, then on the next `GetDeviceData` poll:

1. Forwards the device's request to the real cloud.
2. Decrypts the cloud's encrypted `data` field (the device's "next desired state" payload).
3. Rewrites `IsPower` / `Mode` / `Speed` to match the pending command.
4. Re-encrypts and ships the modified envelope back to the device.
5. Once the device's actual state matches the request, clears the pending flag.

## Compatible with

- [Lifegear HRV HA integration](https://github.com/3uperduck/lifegear_hrv) v4.3.0+
- Lifegear 智慧果 / 樂奇智慧家庭 mobile app (iOS / Android) — pass-through
- Standalone M8 智慧果 (legacy CBC protocol)
- M8-E HRV main unit (隱藏式全熱交換機, ECB protocol)
- M8-E wall-mounted air sensor (paired with the above)
