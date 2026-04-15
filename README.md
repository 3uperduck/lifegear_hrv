# Lifegear HRV 樂奇全熱交換機 Home Assistant 整合

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/release/3uperduck/lifegear_hrv.svg)](https://github.com/3uperduck/lifegear_hrv/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Home Assistant 自訂整合，支援樂奇電器（Lifegear）多款設備。**目前支援**：智慧果 M8 / M8-E、隱藏式全熱交換機（M8-E HRV）、浴室暖風機 BD-125W、M8-E 牆面感測器。搭配可選的 `m8_local_server` add-on，可以進一步本地攔截裝置 ↔ 雲端流量、取得只在 firmware 內傳輸的風道溫度、並避免雲端延遲。

---

## 功能總覽

- ✅ **三層支援**：純雲端、雲端 + 本地 add-on MitM、純本地（M8 legacy）
- ✅ **三種登入方式**：帳號密碼（推薦）、本地、手動 u_id/AuthCode
- ✅ **多裝置自動發現**：登入帳號後自動建立帳號下所有設備
- ✅ **AuthCode 帳號級互鎖**：避免兩個整合 entry 同時 relogin 互踢造成 ping-pong（v4.3.1+）
- ✅ **風道溫度 + 熱回收效率**：透過 add-on MitM 取得，僅在 add-on reachable 時自動建立（v4.3.0+）
- ✅ **濾網提醒** + **M8-E 牆面感測器** + **浴室暖風機**（v4.2.0+）
- ✅ M8-E HRV 連線狀態偵測
- ✅ 手動重新登入按鈕

## 支援設備

| 設備 | App | 模式 | 風速 |
|------|-----|------|------|
| 智慧果 M8 | 樂奇智慧果 | 自動 / 淨化 / 全熱 | 1-4 檔 |
| 隱藏式全熱交換機 (M8-E HRV) | 樂奇淨流系統 | 淨化 / 新風 / 節能 | 1-4 檔 |
| 浴室暖風機 BD-125W | 樂奇淨流系統 | 涼風 / 換氣 / 乾燥-節電 / 乾燥-快速 / 暖房-沐浴 / 暖房-溫控 | 弱 / 中 / 強 |
| 智慧果 M8-E（牆面感測器）| 樂奇淨流系統 | — 純空品感測 | — |

## 架構速覽

```
                 ┌─ 樂奇 App ─────────────┐
                 │                        │
       手機 / HA │                        ▼
       ───────► dm03.e-giant.com.tw   (M8-E)
                 m8.daguan-tech.com.tw (legacy M8)
                       ▲                              ┌── HRV 主機 ──┐
                       │                              │ duct temps  │
       ┌───────────────┴──────────────┐               │             │
       │  m8_local_server add-on      │ ◀────[DNAT]───┤             │
       │  (optional, host_network:80) │               │             │
       │  + REST API on :8765         │               └── M8-E 牆感 ──┘
       └───────────────┬──────────────┘                  air quality
                       │
                       ▼
            HA `lifegear_hrv` 整合
            純雲端：直接打 dm03.e-giant.com.tw
            雲端+本地：另外 poll addon /api/sensor/by_mac
                      取得風道溫度 + 算熱回收效率
```

**M8-E HRV 是兩顆獨立 ESP**：HRV 本體（C4:D8:D5:xx:xx:xx，會 push duct temps）和選配的牆面 M8-E 感測器（push CO2/PM2.5/溫濕度）。雲端把兩者聚合到同一個帳號下；整合對應的兩個 device 在 HA 各自獨立。

## 安裝整合

### HACS 安裝（推薦）

1. HACS → 右上角 **⋮** → **Custom repositories**
2. 輸入 `3uperduck/lifegear_hrv`，類別 **Integration**
3. 安裝 → **重啟 Home Assistant**

### 手動安裝

把 `custom_components/lifegear_hrv/` 整個資料夾放到你的 `config/custom_components/`，重啟 HA。

## 設定整合

**設定** → **裝置與服務** → **新增整合** → 搜尋 **Lifegear** → 選設備型號，再選登入方式：

- **智慧果 M8**：legacy 版選這個
- **淨流系統設備**（M8-E / 暖風機 / M8-E 感測器）：選這個。登入後帳號下**所有設備自動建立**

### 登入方式：帳號密碼（推薦）

直接填樂奇 App 帳號密碼。整合自動處理 AuthCode 取得、過期重登、跨 entry 互鎖。

- **帳號**：M8 用 App 帳號名；M8-E 用手機號碼
- **密碼**：App 設定的密碼

> ⚠️ **同帳號同時只能一個 client 持有 AuthCode**。這個整合會在登入時把手機 App 踢掉。手機 App 重新登入會反過來踢掉 HA — 整合會在下次 poll 偵測到失效並自動重登（120 秒 cooldown 防互踢）。

### 登入方式：手動輸入

需要自己抓 `u_id` + `AuthCode`（用 Wireshark / Proxyman / HttpCanary 抓 `dm03.e-giant.com.tw` 的封包）。AuthCode 失效時要手動更新。一般使用者**不建議**用這條路。

### 登入方式：本地

僅 legacy M8 + 純本地模式適用，需搭配早期版本 add-on，新使用者可以略過。

---

## 進階：搭配 `m8_local_server` add-on（取得風道溫度）

雲端 API **只回傳合併後的 CO2/PM2.5/溫度/濕度**，不會回傳 HRV 風道內的 `TempOA` / `TempSA` / `TempRA` 三顆獨立溫度。要看到這三顆和**熱回收效率**，就要透過本 repo 提供的 add-on 攔截 device→cloud 的封包：

### 1. 安裝 add-on

把本 repo 的 `addon/` + 根目錄的 `m8_local_server.py` 複製到 HAOS 的 `/addons/local/m8_local_server/`，**設定 → 附加元件 → 檢查更新 → 安裝 M8 Local Server → 啟動**。詳細 README 在 [`addon/README.md`](addon/README.md)。

### 2. 設定 router DNAT

在你的 router 上加一條 destination NAT 規則（範例：UniFi UDM Pro）：

| 欄位 | 值 |
|---|---|
| Type | Destination NAT |
| Protocol | TCP |
| Source | IoT subnet（HRV / M8-E 所在的網段，例如 `192.168.10.0/24`）|
| Destination IP | `61.31.209.215` |
| Destination Port | `80` |
| Forward IP | Home Assistant 主機 IP |
| Forward Port | `80` |

### 3. 設定 firewall

允許 `IoT subnet → HA host IP` TCP 80，類型 **LAN In**，**Before Predefined** 勾選。

### 4. 重啟 HRV 和 M8-E ESP

第一次啟用 DNAT 後**務必把 HRV 和 M8-E 物理斷電重插**，否則韌體可能卡在舊的 TCP 狀態不會重連。

### 5. 確認

- `http://<HA-IP>:8765/api/sensor/by_mac` 應該看到兩個 MAC 的 slot，分別有 duct temps 和 air quality
- 重啟 lifegear_hrv 整合（或重啟 HA），HRV 裝置頁就會自動長出**外氣溫度 / 送風溫度 / 回風溫度 / 熱回收效率** 4 個 entity
- 整合有探測機制：addon 不可達或沒資料時這 4 個 entity **不會建立**，純雲端使用者完全看不到

---

## 實體說明

### M8-E HRV（隱藏式全熱交換機）

| 區 | Entity | 說明 |
|---|---|---|
| 控制 | `switch` 電源 | 開關機 |
| 控制 | `select` 模式 | 淨化 / 新風 / 節能 |
| 控制 | `number` 風速 | 1-4 檔 |
| 控制 | `button` 重新登入 | 手動刷新 AuthCode |
| 主要 | `sensor` 目前風速 / 目前模式 | 當前狀態 readout |
| 主要 | `binary_sensor` HRV 連線狀態 | 雲端最後 push 時間判斷 |
| 主要*（need add-on）| `sensor` 外氣溫度 / 送風溫度 / 回風溫度 | duct 溫度 |
| 主要*（need add-on）| `sensor` 熱回收效率 | `(SA−OA) / (RA−OA) × 100` |
| 組態 | `select` 高效 / 初效濾網更換提醒 | 提醒時數設定 |
| 組態 | `button` 高效 / 初效濾網重置 | 重置使用時數 |
| 診斷 | `sensor` 高效 / 初效濾網已使用 | 累計使用 hours |

> **註**：M8-E HRV 本體沒有內建空品感測器，HRV device 上看不到 CO2/PM2.5/溫度/濕度。這些值在「樂奇智慧果 M8-E 感測器」device 上（v4.3.1 開始去重）。

### M8-E 牆面感測器（樂奇智慧果 M8-E）

| Entity | 說明 |
|---|---|
| `sensor` CO2 / PM2.5 / 溫度 / 濕度 | 牆面位置量測 |
| `binary_sensor` M8-E 連線狀態 | 雲端 polling 健康狀態 |
| `button` 重新登入 | 手動刷新 AuthCode |

### 智慧果 M8（legacy）

完整空品 + 控制 + 連線狀態 entities（單一裝置內建感測器）。

### 浴室暖風機 BD-125W

電源、功能 select（涼風/換氣/乾燥/暖房 6 種）、風速 select（弱/中/強）、倒數關機 number、初效濾網提醒/重置/已使用、目前功能/風速 sensor、空品 sensor。

---

## 設計筆記

### 帳號級 relogin lock (v4.3.1)

樂奇雲端用 single-session AuthCode：發新 code 會把舊的作廢。如果同帳號有多個 config entry（例如 M8-E HRV + M8-E 牆感），它們同時啟動 / 同時 relogin 會互踢，造成 entry 反覆 setup_retry。

整合用 `asyncio.Lock()` 以帳號為 key 序列化 `_async_relogin`，第一個拿到鎖的真正打雲端，其他在鎖門外的醒來時直接從 `entry.data` 採用剛剛 propagate 過來的新 AuthCode，不用再打雲端。**結果就是同帳號全部裝置共用一份 AuthCode 而且不會打架。**

### Per-MAC sensor split (addon side)

HRV 主機和 M8-E 牆感是兩顆獨立 ESP，各自 push `PostAirIndex` 但欄位不同（HRV 只送 duct temps，M8-E 送空品）。早期版本把兩個寫進同一個 dict 互相覆寫；v3.2.1+ 改成 per-source-MAC 儲存，merged view 智能合併。

### HRV-only DeviceData filter (addon v3.2.2)

兩顆 ESP 都會 poll `GetDeviceData`，但雲端回給每個的 per-MAC record 不一樣 — M8-E 牆感的回應是 stub `Mode=1 Speed=1`，跟 HRV 真實狀態無關。addon 透過 `valveangle` / `Function` 欄位識別 HRV 的回應，忽略其他來源，避免 device_state 在兩個值之間 flip。

### 風道溫度只在 firmware → cloud 的封包裡

直接打 `dm03.e-giant.com.tw/AppV2/getDeviceAirIndex.asp` 拿到的 JSON 只有合併後 `co2/pm25/temp/rh`。完整的 `Temp / TempOA / TempSA / TempRA / TempEX / TempIN` 等欄位**只存在於 ESP→Cloud 的 PostAirIndex 加密 body 內**，要靠 add-on 攔截才看得到。`Temp` 欄位在 24 小時對照後確認等於 `TempRA`，是 firmware alias 不是另一個感測器（v4.3.1 移除了原本暫時的 HRV 主溫度 entity）。

---

## 更新日誌

### v4.3.1（2026-04）

**整合**
- 移除 M8-E HRV device 上的 CO2/PM2.5/濕度/溫度（這些是雲端從 M8-E 牆感聚合過來的，重複）
- `M8 連線狀態` → `HRV 連線狀態`
- 濾網用時數移到「診斷」區，濾網提醒/重置移到「組態」區，控制區只剩電源 / 模式 / 風速 / 重新登入
- 帳號級 relogin lock + AuthCode 跨 entry propagate
- Addon URL 多候選探測 + cache，無需手動設定
- 風道溫度顯示為整數（韌體不傳小數）
- 移除暫時的 HRV 主溫度 entity（驗證為 TempRA alias）
- v4 → v5 + 啟動 fixup 兩道清除舊 entity

**Addon v3.2.2**
- HRV-only DeviceData filter（用 `valveangle` 識別 HRV vs M8-E 牆感）

### v4.3.0（2026-04）

- 新增風道溫度 entity（外氣 / 送風 / 回風）+ 熱回收效率
- 整合自動偵測 add-on 可達性，cloud-only 使用者不會看到 unavailable 殘骸
- Addon v3.2.x 線：M8-E AppV2 ECB 協定支援、命令注入、per-MAC sensor split

### v4.2.x

- 浴室暖風機 BD-125W、M8-E 牆面感測器、多裝置自動發現、濾網提醒
- M8 連線偵測 timestamp 改回基於有/無值（避免時區誤判）

### v4.1.0

- M8-E 完整支援（淨化/新風/節能、電源獨立控制、感測器讀取、自動開機）
- M8-E API 不同欄位/結構/endpoint 的差異處理

### v4.0.0

- 新增 M8-E（淨流系統）支援，設定時選擇設備型號
- v3 → v4 config entry 自動遷移

### v3.2.0

- 本地模式支援帳密登入 + AuthCode 自動管理 + 重新登入 button
- 模式 select optimistic update + grace period

### v3.0.0

- 本地控制模式 + m8_local_server add-on（MitM proxy）
- M8 連線狀態 binary sensor

### v2.0.0

- 帳號密碼直接登入（不需手動抓 AuthCode）

### v1.x

- 首次發布、認證碼可在 UI 更新

## 問題回報

[GitHub Issues](https://github.com/3uperduck/lifegear_hrv/issues)

## 授權

MIT License

## 致謝

- [Anthropic Claude](https://www.anthropic.com/) 協助開發
- 邵先生提供 M8-E HRV / 牆面感測器 / 浴室暖風機測試環境
