# Lifegear HRV 樂奇全熱交換機 Home Assistant 整合

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/release/3uperduck/lifegear_hrv.svg)](https://github.com/3uperduck/lifegear_hrv/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Home Assistant 自訂整合，支援樂奇電器（Lifegear）智慧果 M8 全熱交換機。

## 功能

- ✅ 即時讀取 CO2、PM2.5、溫度、濕度
- ✅ 遠端控制電源開關、模式切換（自動/淨化/全熱）、風速調整（1-4 檔）
- ✅ 繁體中文介面
- ✅ **三種連線模式**：本地控制 / 帳號密碼 / 手動輸入
- ✅ **本地控制模式**：透過 MitM proxy，感測器資料不經雲端（v3.0.0+）
- ✅ **AuthCode 自動管理**：啟動登入、過期自動重登（v3.2.0+）
- ✅ M8 裝置連線狀態偵測
- ✅ 手動重新登入按鈕

## 支援設備

- 樂奇智慧果 M8 (Smart Fruit M8)

## 安裝

### HACS 安裝（推薦）

1. 開啟 HACS
2. 點選右上角三個點 → **自訂儲存庫**
3. 輸入 `3uperduck/lifegear_hrv`，類別選擇 `整合`
4. 點選 **安裝**
5. 重啟 Home Assistant

### 手動安裝

1. 下載此儲存庫
2. 將 `custom_components/lifegear_hrv` 資料夾複製到你的 Home Assistant `config/custom_components/` 目錄
3. 重啟 Home Assistant

## 設定

前往 **設定** → **裝置與服務** → **新增整合** → 搜尋 **Lifegear** 或 **樂奇**，選擇登入方式：

### 方式一：本地控制（推薦）

搭配 `m8_local_server` add-on，感測器資料完全在本地讀取，不依賴雲端穩定性。

**前置需求**：在 HAOS 上安裝並啟動 [m8_local_server](https://github.com/3uperduck/lifegear_hrv/blob/main/m8_local_server.py) add-on，並透過 DNAT 將 M8 的流量導向 HAOS。

**設定欄位**：

| 欄位 | 說明 |
|------|------|
| 本地伺服器網址 | add-on 的 REST API 位址，例如 `http://127.0.0.1:8765` |
| 裝置 MAC | 選填，M8 的 MAC 位址 |
| 裝置 ID | 選填 |
| 帳號 | 選填，樂奇 App 帳號（填寫後可透過雲端 API 控制模式切換，更可靠） |
| 密碼 | 選填，樂奇 App 密碼 |

> 💡 **為什麼建議填帳密？**
> M8 韌體接受本地注入的風速變更，但模式切換需透過雲端 `getDeviceMod.asp` API。填寫帳密後，整合會自動登入取得 AuthCode，同時使用本地注入（風速）和雲端 API（模式）雙通道控制。

### 方式二：帳號密碼登入

直接輸入你的樂奇智慧果 App 帳號和密碼。整合會自動登入並管理 AuthCode。

- **帳號**：App 中註冊的帳號（非 Email、非手機號碼）
- **密碼**：App 中設定的密碼

> ⚠️ **同一帳號僅支援單一裝置登入。** 使用此方式後 App 會被登出。重新登入 App 後，HA 會在下次控制時自動重新連線。

### 方式三：手動輸入（進階）

透過封包擷取取得 `u_id` 和 `AuthCode` 後手動輸入。AuthCode 會在 App 重新登入時失效，需手動更新。

<details>
<summary>如何取得 u_id 和 AuthCode？</summary>

#### Windows + Wireshark

1. 在電腦上開啟 **行動熱點**
2. 將手機連接到電腦熱點
3. 使用 **Wireshark** 監聽熱點網路介面
4. 在手機上開啟樂奇智慧果 App 並操作設備
5. 過濾 `http` 封包，找到發送到 `m8.daguan-tech.com.tw` 的請求
6. 從請求內容中取得 `u_id` 和 `AuthCode`

#### macOS + Proxyman

1. 下載並安裝 **Proxyman**
2. 手機設定 WiFi Proxy 指向 Mac 的 IP:9090
3. 在手機上安裝 Proxyman 的 CA 憑證
4. 打開樂奇 App 操作設備
5. 在 Proxyman 查看封包取得 `u_id` 和 `AuthCode`

#### Android + HttpCanary

1. 安裝 **HttpCanary** App
2. 開啟抓包功能
3. 打開樂奇智慧果 App 並操作
4. 查看封包取得 `u_id` 和 `AuthCode`

</details>

### 更新認證資訊

前往 **設定** → **裝置與服務** → **Lifegear 樂奇全熱交換機** → **設定**（齒輪圖示），即可更新帳號密碼或認證碼。

## 實體說明

### 感測器

| 實體 | 說明 |
|------|------|
| CO2 | 二氧化碳濃度 (ppm) |
| PM2.5 | 細懸浮微粒濃度 (µg/m³) |
| 溫度 | 室內溫度 (°C) |
| 濕度 | 室內相對濕度 (%) |
| 目前風速 | 當前風速檔位 |
| 目前模式 | 當前運轉模式 |
| M8 連線狀態 | 裝置是否在線（本地模式） |

### 控制

| 實體 | 說明 |
|------|------|
| 電源 | 開關機控制 |
| 模式 | 自動/淨化/全熱 |
| 風速 | 1-4 檔風速調整 |
| 重新登入 | 手動刷新 AuthCode（有設定帳密時才出現） |

## 架構說明

### 本地控制模式

```
M8 裝置 ──HTTP──▶ m8_local_server add-on (port 80)
                       │
                       ├─ 攔截感測器資料 → 存本地 + 轉發雲端
                       ├─ 攔截 GetDeviceData → 注入風速指令
                       │
                       └─ REST API (port 8765) ◀── HA Integration
                                                       │
                                                       └─ 模式切換 → 雲端 getDeviceMod.asp
```

- **風速/電源**：透過 MitM 注入，即時生效
- **模式切換**：透過雲端 API（M8 韌體限制，需雲端確認後才生效，約 3-5 秒延遲）

### AuthCode 生命週期

1. HA 啟動時自動登入取得 AuthCode
2. 手機 App 登入會踢掉 HA 的 AuthCode
3. 下次控制時偵測到失效，自動重新登入（2 分鐘 cooldown 防互踢）
4. 也可按「重新登入」按鈕手動刷新

## 更新日誌

### v3.2.0
- 新增：本地模式支援帳密登入，透過雲端 API 控制模式切換
- 新增：AuthCode 自動管理（啟動登入 + 過期重登 + 2 分鐘 cooldown）
- 新增：「重新登入」按鈕 entity
- 改善：模式 select 加入 optimistic update + grace period，避免 UI 跳動

### v3.0.0
- 新增：本地控制模式，搭配 m8_local_server add-on
- 新增：M8 連線狀態 binary sensor
- 新增：設定流程支援三種登入方式（本地/帳密/手動）
- 架構：MitM proxy 攔截 M8 ↔ 雲端流量，感測器資料完全本地化

### v2.0.0
- 新增：帳號密碼直接登入，不需要再透過抓包取得 AuthCode
- 新增：AuthCode 失效時自動重新登入（帳密登入模式）
- 新增：config entry v1 → v2 自動遷移

### v1.1.0
- 新增：支援在設定中直接更新認證碼
- 新增：重新設定功能，無需刪除整合即可更新帳號資訊

### v1.0.0
- 首次發布
- 支援讀取 CO2、PM2.5、溫度、濕度
- 支援控制電源、模式、風速

## 問題回報

如果遇到問題，請在 [GitHub Issues](https://github.com/3uperduck/lifegear_hrv/issues) 回報。

## 授權

MIT License

## 致謝

- 感謝 [Anthropic Claude](https://www.anthropic.com/) 協助開發此整合
