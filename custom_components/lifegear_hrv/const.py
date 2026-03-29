"""Constants for Lifegear HRV."""

DOMAIN = "lifegear_hrv"

CONF_USER_ID = "user_id"
CONF_AUTH_CODE = "auth_code"
CONF_DEVICE_ID = "device_id"
CONF_MAC = "mac"
CONF_ACCOUNT = "account"
CONF_PASSWORD = "password"
CONF_LOGIN_METHOD = "login_method"

LOGIN_METHOD_CREDENTIALS = "credentials"
LOGIN_METHOD_MANUAL = "manual"

API_BASE_URL = "http://m8.daguan-tech.com.tw/app"
API_LOGIN = f"{API_BASE_URL}/login.asp"
API_GET_STATUS = f"{API_BASE_URL}/getHomeDeviceDetail.asp"
API_SET_CONTROL = f"{API_BASE_URL}/getDeviceMod.asp"

# AES encryption constants for login
AES_KEY = b"LifeGear85ls6IsY"
AES_IV = bytes([0x00, 0xFE, 0x00, 0x0A, 0x6A, 0x5D, 0x85, 0x98])

HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded",
    "Accept": "*/*",
    "User-Agent": "Sunon/1.0.15",
    "Accept-Language": "zh-TW,zh-Hant;q=0.9",
}

MODE_AUTO = 1
MODE_PURIFY = 2
MODE_HRV = 3

MODE_NAMES = {
    MODE_AUTO: "自動",
    MODE_PURIFY: "淨化",
    MODE_HRV: "全熱",
}

MODE_NAME_TO_VALUE = {v: k for k, v in MODE_NAMES.items()}
