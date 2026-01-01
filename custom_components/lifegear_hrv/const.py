"""Constants for Lifegear HRV."""

DOMAIN = "lifegear_hrv"

CONF_USER_ID = "user_id"
CONF_AUTH_CODE = "auth_code"
CONF_DEVICE_ID = "device_id"
CONF_MAC = "mac"

API_BASE_URL = "http://m8.daguan-tech.com.tw/app"
API_GET_STATUS = f"{API_BASE_URL}/getHomeDeviceDetail.asp"
API_SET_CONTROL = f"{API_BASE_URL}/getDeviceMod.asp"

MODE_AUTO = 1
MODE_PURIFY = 2
MODE_HRV = 3

MODE_NAMES = {
    MODE_AUTO: "自動",
    MODE_PURIFY: "淨化",
    MODE_HRV: "全熱",
}

MODE_NAME_TO_VALUE = {v: k for k, v in MODE_NAMES.items()}
