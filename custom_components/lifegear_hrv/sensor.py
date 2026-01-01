"""Sensor platform for Lifegear HRV."""
from __future__ import annotations

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

from .const import DOMAIN, MODE_NAMES, CONF_MAC
from .coordinator import LifegearHRVCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Lifegear HRV sensors."""
    coordinator: LifegearHRVCoordinator = hass.data[DOMAIN][entry.entry_id]

    sensors = [
        LifegearHRVCO2Sensor(coordinator, entry),
        LifegearHRVPM25Sensor(coordinator, entry),
        LifegearHRVTemperatureSensor(coordinator, entry),
        LifegearHRVHumiditySensor(coordinator, entry),
        LifegearHRVSpeedSensor(coordinator, entry),
        LifegearHRVModeSensor(coordinator, entry),
    ]

    async_add_entities(sensors)


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
        return {
            "identifiers": {(DOMAIN, self._mac)},
            "name": "樂奇全熱交換機",
            "manufacturer": "Lifegear 樂奇",
            "model": "智慧果 M8",
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
            return int(self.coordinator.data.get("md_co2", 0))
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
            return int(self.coordinator.data.get("md_pm25", 0))
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
            return int(self.coordinator.data.get("md_temp", 0))
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
            return int(self.coordinator.data.get("md_rh", 0))
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
            return int(self.coordinator.data.get("md_speed", 0))
        return None


class LifegearHRVModeSensor(LifegearHRVBaseSensor):
    """Mode Sensor."""

    _attr_name = "目前模式"
    _attr_icon = "mdi:air-filter"

    def __init__(self, coordinator: LifegearHRVCoordinator, entry: ConfigEntry) -> None:
        """Initialize."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{self._mac}_mode_sensor"

    @property
    def native_value(self):
        """Return the state."""
        if self.coordinator.data:
            mode = int(self.coordinator.data.get("md_mode", 1))
            return MODE_NAMES.get(mode, "未知")
        return None
