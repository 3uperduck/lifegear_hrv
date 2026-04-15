# Changelog

## 3.2.2

- **HRV-only DeviceData filter** — the addon was storing both the HRV
  main unit's GetDeviceData response *and* the paired M8-E sensor
  module's stub response into the same shared device state, causing
  Mode/Speed to flip between the user-set value and `Mode=1 Speed=1`
  every poll. Detect HRV via presence of `valveangle` / `Function` in
  the decoded payload and ignore non-HRV responses.

## 3.2.1

- **Per-MAC sensor split** — under the M8-E pairing model the HRV unit
  and the M8-E wall sensor are independent ESPs that push different
  fields of `PostAirIndex`. Storing them in a single dict caused them
  to overwrite each other. Now stored per source MAC and merged on
  read; raw slots exposed at `/api/sensor/by_mac`.

## 3.2.0

- M8-E (`/api/AppV2/*`) protocol handler with AES-ECB + zero padding.
- Forwarding now picks the right cloud virtual host
  (`m8.daguan-tech.com.tw` for legacy paths,
  `dm03.e-giant.com.tw` for AppV2).
- Duct temperatures (`TempOA` / `TempSA` / `TempRA`) captured from
  HRV's PostAirIndex and exposed via `/api/sensor`.
- Command injection adapted to AppV2 GetDeviceData (encrypted JSON
  rewritten and re-encrypted).

## 1.0.0

- Initial MitM proxy mode for legacy M8 (智慧果): forwards device
  ↔ cloud traffic and saves sensor data locally while keeping the
  official mobile app working.
- Command injection via GetDeviceData response rewriting.
- Pass-through of APP cloud commands.
- Local fallback when the cloud is unreachable.
- REST API: `/api/status`, `/api/command`, `/api/device_info`.
- Removed DNS server (replaced by HTTP-only DNAT approach).
