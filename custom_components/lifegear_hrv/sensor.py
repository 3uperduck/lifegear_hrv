"""Sensor platform for Lifegear HRV."""
from __future__ import annotations

import logging

import aiohttp
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
    CONCENTRATION_PARTS_PER_MILLION,
    PERCENTAGE,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from homeassistant.const import UnitOfTime

_LOGGER = logging.getLogger(__name__)

from .const import (
    DOMAIN, CONF_MAC, CONF_DEVICE_MODEL, DEVICE_MODEL_M8, DEVICE_MODEL_M8E,
    DEVICE_MODEL_BATH_HEATER, DEVICE_MODEL_M8E_SENSOR,
    normalize_mode, get_mode_config, is_m8e_platform,
    FUNC_NAMES_BATH,
)
from .coordinator import LifegearHRVCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Lifegear HRV sensors."""
    coordinator: LifegearHRVCoordinator = hass.data[DOMAIN][entry.entry_id]
    model = entry.data.get(CONF_DEVICE_MODEL, DEVICE_MODEL_M8)

    # All device types have air quality sensors
    sensors: list[SensorEntity] = [
        LifegearHRVCO2Sensor(coordinator, entry),
        LifegearHRVPM25Sensor(coordinator, entry),
        LifegearHRVHumiditySensor(coordinator, entry),
    ]

    # Temperature: all except bath heater (returns empty temp)
    if model != DEVICE_MODEL_BATH_HEATER:
        sensors.append(LifegearHRVTemperatureSensor(coordinator, entry))

    # Speed/Mode sensors: HRV devices only (M8, M8-E)
    if model in (DEVICE_MODEL_M8, DEVICE_MODEL_M8E):
        sensors.append(LifegearHRVSpeedSensor(coordinator, entry))
        sensors.append(LifegearHRVModeSensor(coordinator, entry))

    # Bath heater: function sensor
    if model == DEVICE_MODEL_BATH_HEATER:
        sensors.append(LifegearBathFunctionSensor(coordinator, entry))
        sensors.append(LifegearBathSpeedSensor(coordinator, entry))

    # Filter alarm sensors
    if model == DEVICE_MODEL_M8E:
        sensors.append(LifegearFilterSensor(coordinator, entry, "high", "高效濾網"))
        sensors.append(LifegearFilterSensor(coordinator, entry, "primary", "初效濾網"))
    elif model == DEVICE_MODEL_BATH_HEATER:
        sensors.append(LifegearFilterSensor(coordinator, entry, "primary", "初效濾網"))

    # Duct temperatures + heat recovery efficiency — only if the local
    # m8_local_server addon is reachable. Without the addon running MitM,
    # the cloud API doesn't expose per-duct temperatures, so the sensors
    # would be permanently unavailable for most users. Probe once at setup.
    if model == DEVICE_MODEL_M8E and await _async_addon_duct_temps_available(
        hass, coordinator
    ):
        sensors.append(LifegearHRVDuctTempSensor(coordinator, entry, "oa", "外氣溫度"))
        sensors.append(LifegearHRVDuctTempSensor(coordinator, entry, "sa", "送風溫度"))
        sensors.append(LifegearHRVDuctTempSensor(coordinator, entry, "ra", "回風溫度"))
        sensors.append(LifegearHRVEfficiencySensor(coordinator, entry))

    async_add_entities(sensors)


async def _async_addon_duct_temps_available(
    hass: HomeAssistant, coordinator: LifegearHRVCoordinator
) -> bool:
    """Return True only if the local m8_local_server addon is reachable AND
    currently has duct-temperature data for this HRV's MAC.

    Checking for *data* (not just a responding server) avoids registering the
    entities on users who have the addon installed but haven't enabled the
    DNAT rule — in that state the server returns null and the entities would
    still be useless.
    """
    if not coordinator.mac:
        return False
    try:
        async with aiohttp.ClientSession() as session:
            base = await coordinator._async_resolve_addon_base_url(session)
            if not base:
                return False
            async with session.get(
                f"{base}/api/sensor/by_mac",
                timeout=aiohttp.ClientTimeout(total=3),
            ) as response:
                if response.status != 200:
                    return False
                data = await response.json()
    except Exception as err:
        _LOGGER.debug("Addon duct-temp probe failed: %s", err)
        return False
    slot = data.get(coordinator.mac.upper()) or data.get(coordinator.mac) or {}
    has_any = any(
        slot.get(k) is not None for k in ("temp_oa", "temp_sa", "temp_ra")
    )
    if has_any:
        _LOGGER.info(
            "m8_local_server addon detected with duct temps for %s — "
            "registering OA/SA/RA + efficiency sensors",
            coordinator.mac,
        )
    return has_any


class LifegearHRVBaseSensor(CoordinatorEntity, SensorEntity):
    """Base class for Lifegear HRV sensors."""

    def __init__(
        self,
        coordinator: LifegearHRVCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_has_entity_name = True
        self._mac = entry.data[CONF_MAC]

    @property
    def device_info(self):
        """Return device info."""
        model = self._entry.data.get(CONF_DEVICE_MODEL, DEVICE_MODEL_M8)
        names = {
            DEVICE_MODEL_M8: ("樂奇全熱交換機", "智慧果 M8"),
            DEVICE_MODEL_M8E: ("樂奇全熱交換機", "智慧果 M8-E"),
            DEVICE_MODEL_BATH_HEATER: ("樂奇浴室暖風機", "BD-125W"),
            DEVICE_MODEL_M8E_SENSOR: ("樂奇智慧果 M8-E", "M8-E 感測器"),
        }
        name, model_name = names.get(model, ("樂奇設備", model))
        return {
            "identifiers": {(DOMAIN, self._mac)},
            "name": name,
            "manufacturer": "Lifegear 樂奇",
            "model": model_name,
        }


class LifegearHRVCO2Sensor(LifegearHRVBaseSensor):
    """CO2 Sensor."""

    _attr_name = "CO2"
    _attr_device_class = SensorDeviceClass.CO2
    _attr_native_unit_of_measurement = CONCENTRATION_PARTS_PER_MILLION
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: LifegearHRVCoordinator, entry: ConfigEntry) -> None:
        """Initialize."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{self._mac}_co2"

    @property
    def native_value(self):
        """Return the state."""
        if self.coordinator.data:
            val = self.coordinator.data.get("md_co2", 0)
            return int(val) if val != "" else None
        return None


class LifegearHRVPM25Sensor(LifegearHRVBaseSensor):
    """PM2.5 Sensor."""

    _attr_name = "PM2.5"
    _attr_device_class = SensorDeviceClass.PM25
    _attr_native_unit_of_measurement = CONCENTRATION_MICROGRAMS_PER_CUBIC_METER
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: LifegearHRVCoordinator, entry: ConfigEntry) -> None:
        """Initialize."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{self._mac}_pm25"

    @property
    def native_value(self):
        """Return the state."""
        if self.coordinator.data:
            val = self.coordinator.data.get("md_pm25", 0)
            return int(val) if val != "" else None
        return None


class LifegearHRVTemperatureSensor(LifegearHRVBaseSensor):
    """Temperature Sensor."""

    _attr_name = "溫度"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: LifegearHRVCoordinator, entry: ConfigEntry) -> None:
        """Initialize."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{self._mac}_temperature"

    @property
    def native_value(self):
        """Return the state."""
        if self.coordinator.data:
            val = self.coordinator.data.get("md_temp", 0)
            return int(val) if val != "" else None
        return None


class LifegearHRVHumiditySensor(LifegearHRVBaseSensor):
    """Humidity Sensor."""

    _attr_name = "濕度"
    _attr_device_class = SensorDeviceClass.HUMIDITY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: LifegearHRVCoordinator, entry: ConfigEntry) -> None:
        """Initialize."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{self._mac}_humidity"

    @property
    def native_value(self):
        """Return the state."""
        if self.coordinator.data:
            val = self.coordinator.data.get("md_rh", 0)
            return int(val) if val != "" else None
        return None


class LifegearHRVSpeedSensor(LifegearHRVBaseSensor):
    """Speed Sensor."""

    _attr_name = "目前風速"
    _attr_icon = "mdi:fan"

    def __init__(self, coordinator: LifegearHRVCoordinator, entry: ConfigEntry) -> None:
        """Initialize."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{self._mac}_speed_sensor"

    @property
    def native_value(self):
        """Return the state."""
        if self.coordinator.data:
            val = self.coordinator.data.get("md_speed", 0)
            return int(val) if val != "" else None
        return None


class LifegearHRVModeSensor(LifegearHRVBaseSensor):
    """Mode Sensor."""

    _attr_name = "目前模式"
    _attr_icon = "mdi:air-filter"

    def __init__(self, coordinator: LifegearHRVCoordinator, entry: ConfigEntry) -> None:
        """Initialize."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{self._mac}_mode_sensor"
        model = entry.data.get(CONF_DEVICE_MODEL, DEVICE_MODEL_M8)
        self._mode_names, _ = get_mode_config(model)

    @property
    def native_value(self):
        """Return the state."""
        if self.coordinator.data:
            val = self.coordinator.data.get("md_mode", 1)
            if val == "":
                return None
            return self._mode_names.get(normalize_mode(val), "未知")
        return None


class LifegearBathFunctionSensor(LifegearHRVBaseSensor):
    """Bath heater current function sensor."""

    _attr_name = "目前功能"
    _attr_icon = "mdi:heat-wave"

    def __init__(self, coordinator: LifegearHRVCoordinator, entry: ConfigEntry) -> None:
        """Initialize."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{self._mac}_function_sensor"

    @property
    def native_value(self):
        """Return the state."""
        if self.coordinator.data:
            val = self.coordinator.data.get("md_function")
            if val is None:
                return None
            return FUNC_NAMES_BATH.get(int(val), "未知")
        return None


class LifegearBathSpeedSensor(LifegearHRVBaseSensor):
    """Bath heater current speed sensor."""

    _attr_name = "目前風速"
    _attr_icon = "mdi:fan"

    def __init__(self, coordinator: LifegearHRVCoordinator, entry: ConfigEntry) -> None:
        """Initialize."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{self._mac}_speed_sensor"

    @property
    def native_value(self):
        """Return the state."""
        if self.coordinator.data:
            from .const import SPEED_NAMES_BATH
            val = self.coordinator.data.get("md_speed")
            if val is None or val == "":
                return None
            return SPEED_NAMES_BATH.get(int(val), str(val))
        return None


class LifegearFilterSensor(LifegearHRVBaseSensor):
    """Filter usage sensor (高效濾網 / 初效濾網)."""

    _attr_icon = "mdi:air-filter"
    _attr_native_unit_of_measurement = UnitOfTime.HOURS
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: LifegearHRVCoordinator,
        entry: ConfigEntry,
        filter_type: str,
        filter_name: str,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator, entry)
        self._filter_type = filter_type  # "high" or "primary"
        self._attr_name = f"{filter_name}已使用"
        self._attr_unique_id = f"{self._mac}_filter_{filter_type}_used"

    @property
    def native_value(self) -> int | None:
        """Return hours used."""
        if self.coordinator.data:
            val = self.coordinator.data.get(f"filter_{self._filter_type}_used")
            if val is not None:
                return int(val)
        return None

    @property
    def extra_state_attributes(self) -> dict:
        """Return alarm threshold and reset time."""
        if not self.coordinator.data:
            return {}
        prefix = f"filter_{self._filter_type}"
        attrs = {}
        alarm = self.coordinator.data.get(f"{prefix}_alarm")
        if alarm is not None:
            attrs["更換提醒時數"] = int(alarm)
            used = self.coordinator.data.get(f"{prefix}_used")
            if used is not None:
                remaining = int(alarm) - int(used)
                attrs["剩餘時數"] = max(0, remaining)
                attrs["已到期"] = remaining <= 0
        reset = self.coordinator.data.get(f"{prefix}_reset")
        if reset:
            attrs["上次重置"] = reset
        return attrs


class LifegearHRVDuctTempSensor(LifegearHRVBaseSensor):
    """M8-E HRV duct temperature sensor (OA / SA / RA), sourced from addon MitM."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: LifegearHRVCoordinator,
        entry: ConfigEntry,
        duct: str,   # "oa" | "sa" | "ra"
        name: str,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator, entry)
        self._duct = duct
        self._attr_name = name
        self._attr_unique_id = f"{self._mac}_temp_{duct}"

    @property
    def native_value(self):
        """Return the duct temperature from the addon-sourced fields."""
        if not self.coordinator.data:
            return None
        val = self.coordinator.data.get(f"md_temp_{self._duct}")
        if val is None:
            return None
        try:
            return float(val)
        except (TypeError, ValueError):
            return None

    @property
    def available(self) -> bool:
        """Unavailable when addon/MitM isn't providing data."""
        if not self.coordinator.last_update_success:
            return False
        if not self.coordinator.data:
            return False
        return self.coordinator.data.get(f"md_temp_{self._duct}") is not None


class LifegearHRVEfficiencySensor(LifegearHRVBaseSensor):
    """Heat recovery efficiency: (TempSA - TempOA) / (TempRA - TempOA) × 100."""

    _attr_name = "熱回收效率"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:gauge"

    def __init__(self, coordinator: LifegearHRVCoordinator, entry: ConfigEntry) -> None:
        """Initialize."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{self._mac}_hrv_efficiency"

    @property
    def native_value(self):
        """Return efficiency from coordinator-computed field."""
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("md_hrv_efficiency")

    @property
    def available(self) -> bool:
        """Unavailable when gradient is too small or addon isn't providing data."""
        if not self.coordinator.last_update_success:
            return False
        if not self.coordinator.data:
            return False
        return self.coordinator.data.get("md_hrv_efficiency") is not None
