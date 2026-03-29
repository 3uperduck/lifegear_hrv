# Lifegear HRV 樂奇全熱交換機 Home Assistant 整合

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/release/3uperduck/lifegear_hrv.svg)](https://github.com/3uperduck/lifegear_hrv/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Home Assistant 自訂整合，支援樂奇電器（Lifegear）智慧果 M8 全熱交換機。

## 功能

- ✅ 即時讀取 CO2、PM2.5、溫度、濕度
- ✅ 遠端控制電源開關
- ✅ 切換模式（自動/淨化/全熱）
- ✅ 調整風速（1-4 檔）
- ✅ 自動重試機制確保指令送達
- ✅ 繁體中文介面
- ✅ **帳號密碼直接登入（v2.0.0+）**
- ✅ AuthCode 自動刷新（v2.0.0+）
- ✅ 支援重新設定認證碼（v1.1.0+）

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

1. 前往 **設定** → **裝置與服務**
2. 點選 **新增整合**
3. 搜尋 **Lifegear** 或 **樂奇**
4. 選擇登入方式：

### 方式一：帳號密碼登入（推薦）

直接輸入你的樂奇智慧果 App 帳號和密碼即可完成設定。

- **帳號**：你在 App 中註冊的帳號（非 Email、非手機號碼）
- **密碼**：你在 App 中設定的密碼

> **⚠️ 注意：同一帳號僅支援單一裝置登入。**
> 使用此方式後，App 將被登出，需重新登入 App。反之，重新登入 App 後 HA 會在 30 秒內自動重新連線。

### 方式二：手動輸入（進階）

透過封包擷取取得 `u_id` 和 `AuthCode` 後手動輸入。此方式不會與 App 互相衝突，但 AuthCode 會在 App 重新登入時失效，需手動更新。

<details>
<summary>如何取得 u_id 和 AuthCode？</summary>

#### Windows + Wireshark

1. 在電腦上開啟 **行動熱點**
2. 將智慧果 M8 連接到電腦熱點
3. 將手機也連接到電腦熱點
4. 使用 **Wireshark** 監聽熱點網路介面
5. 在手機上開啟樂奇智慧果 App 並操作設備
6. 在 Wireshark 中過濾 `http` 封包
7. 找到發送到 `m8.daguan-tech.com.tw` 的請求
8. 從請求內容中取得 `u_id` 和 `AuthCode`

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

1. 前往 **設定** → **裝置與服務**
2. 找到 **Lifegear 樂奇全熱交換機**
3. 點選 **設定**（齒輪圖示）
4. 輸入新的帳號密碼或認證碼
5. 儲存

## 實體說明

此整合會建立以下實體：

### 感測器
| 實體 | 說明 |
|------|------|
| CO2 | 二氧化碳濃度 (ppm) |
| PM2.5 | 細懸浮微粒濃度 (µg/m³) |
| 溫度 | 室內溫度 (°C) |
| 濕度 | 室內相對濕度 (%) |
| 目前風速 | 當前風速檔位 |
| 目前模式 | 當前運轉模式 |

### 控制
| 實體 | 說明 |
|------|------|
| 電源 | 開關機控制 |
| 模式 | 自動/淨化/全熱 |
| 風速 | 1-4 檔風速調整 |

## 更新日誌

### v2.0.0
- 新增：帳號密碼直接登入，不需要再透過抓包取得 AuthCode
- 新增：AuthCode 失效時自動重新登入（帳密登入模式）
- 新增：設定流程支援選擇登入方式（帳號密碼 / 手動輸入）
- 新增：v1 → v2 config entry 自動遷移
- 注意：同一帳號僅支援單一裝置登入

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

