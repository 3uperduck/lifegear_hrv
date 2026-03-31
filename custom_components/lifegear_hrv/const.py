"""Constants for Lifegear HRV."""
import hashlib

DOMAIN = "lifegear_hrv"

CONF_USER_ID = "user_id"
CONF_AUTH_CODE = "auth_code"
CONF_DEVICE_ID = "device_id"
CONF_MAC = "mac"
CONF_ACCOUNT = "account"
CONF_PASSWORD = "password"
CONF_LOGIN_METHOD = "login_method"
CONF_LOCAL_SERVER = "local_server_url"

CONF_DEVICE_MODEL = "device_model"

LOGIN_METHOD_CREDENTIALS = "credentials"
LOGIN_METHOD_MANUAL = "manual"
LOGIN_METHOD_LOCAL = "local"

DEVICE_MODEL_M8 = "m8"
DEVICE_MODEL_M8E = "m8e"

# M8 (智慧果) API
API_BASE_URL = "http://m8.daguan-tech.com.tw/app"
API_LOGIN = f"{API_BASE_URL}/login.asp"
API_GET_STATUS = f"{API_BASE_URL}/getHomeDeviceDetail.asp"
API_SET_CONTROL = f"{API_BASE_URL}/getDeviceMod.asp"

# M8-E (淨流系統) API
API_BASE_URL_M8E = "http://dm03.e-giant.com.tw/AppV2"
API_LOGIN_M8E = f"{API_BASE_URL_M8E}/login.asp"
API_LIST_DEVICES_M8E = f"{API_BASE_URL_M8E}/getHomeMainDeviceList.asp"
API_GET_STATUS_M8E = f"{API_BASE_URL_M8E}/getHomeDeviceDetail.asp"
API_SET_CONTROL_M8E = f"{API_BASE_URL_M8E}/getDeviceFunctionEdit.asp"
API_SET_POWER_M8E = f"{API_BASE_URL_M8E}/getDevicePower.asp"


def get_api_urls(model: str = DEVICE_MODEL_M8) -> dict:
    """Return API URLs for the given device model."""
    if model == DEVICE_MODEL_M8E:
        return {
            "login": API_LOGIN_M8E,
            "list": API_LIST_DEVICES_M8E,
            "status": API_GET_STATUS_M8E,
            "control": API_SET_CONTROL_M8E,
            "power": API_SET_POWER_M8E,
        }
    return {
        "login": API_LOGIN,
        "list": API_GET_STATUS,
        "status": API_GET_STATUS,
        "control": API_SET_CONTROL,
    }

# AES encryption constants for login (phone app)
AES_KEY = b"LifeGear85ls6IsY"
AES_IV = bytes([0x00, 0xFE, 0x00, 0x0A, 0x6A, 0x5D, 0x85, 0x98])

# AES encryption constants for device firmware (PostDeviceData/Status, GetDeviceData)
DEVICE_AES_KEY = hashlib.md5(b"LifeGear85ls6IsY").digest()
DEVICE_AES_IV  = bytes.fromhex("8a39b1993ec8c3dcde502975fd292c7b")

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

# M8-E modes (different from M8)
MODE_M8E_PURIFY = 1   # 淨化
MODE_M8E_FRESH = 2    # 新風
MODE_M8E_ECO = 3      # 節能

MODE_NAMES_M8E = {
    MODE_M8E_PURIFY: "淨化",
    MODE_M8E_FRESH: "新風",
    MODE_M8E_ECO: "節能",
}


def normalize_mode(raw) -> int:
    """Convert M8 internal mode (17/18/19) to cloud mode (1/2/3)."""
    try:
        m = int(raw)
        return m - 16 if m >= 17 else m
    except (TypeError, ValueError):
        return 3

MODE_NAME_TO_VALUE = {v: k for k, v in MODE_NAMES.items()}
MODE_NAME_TO_VALUE_M8E = {v: k for k, v in MODE_NAMES_M8E.items()}


def get_mode_config(model: str = DEVICE_MODEL_M8) -> tuple[dict, dict]:
    """Return (mode_names, name_to_value) for the given model."""
    if model == DEVICE_MODEL_M8E:
        return MODE_NAMES_M8E, MODE_NAME_TO_VALUE_M8E
    return MODE_NAMES, MODE_NAME_TO_VALUE
