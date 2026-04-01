"""Number platform for Lifegear HRV."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN, CONF_MAC, CONF_DEVICE_MODEL, DEVICE_MODEL_M8,
    DEVICE_MODEL_M8E, DEVICE_MODEL_BATH_HEATER, DEVICE_MODEL_M8E_SENSOR,
    FUNC_BATH_WITH_COUNTDOWN,
)
from .coordinator import LifegearHRVCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Lifegear HRV number."""
    model = entry.data.get(CONF_DEVICE_MODEL, DEVICE_MODEL_M8)
    coordinator: LifegearHRVCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[NumberEntity] = []
    if model in (DEVICE_MODEL_M8, DEVICE_MODEL_M8E):
        entities.append(LifegearHRVSpeedNumber(coordinator, entry))
    elif model == DEVICE_MODEL_BATH_HEATER:
        entities.append(LifegearBathCountdownNumber(coordinator, entry))

    if entities:
        async_add_entities(entities)


class LifegearHRVSpeedNumber(CoordinatorEntity, NumberEntity):
    """Speed Number for Lifegear HRV."""

    _attr_name = "風速"
    _attr_icon = "mdi:fan"
    _attr_has_entity_name = True
    _attr_native_min_value = 1
    _attr_native_max_value = 4
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER

    def __init__(
        self,
        coordinator: LifegearHRVCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the number."""
        super().__init__(coordinator)
        self._entry = entry
        self._mac = entry.data[CONF_MAC]
        self._attr_unique_id = f"{self._mac}_speed"

    @property
    def device_info(self):
        """Return device info."""
        model = self._entry.data.get(CONF_DEVICE_MODEL, DEVICE_MODEL_M8)
        model_name = "智慧果 M8-E" if model == DEVICE_MODEL_M8E else "智慧果 M8"
        return {
            "identifiers": {(DOMAIN, self._mac)},
            "name": "樂奇全熱交換機",
            "manufacturer": "Lifegear 樂奇",
            "model": model_name,
        }

    @property
    def native_value(self) -> float | None:
        """Return current speed."""
        if self.coordinator.data:
            val = self.coordinator.data.get("md_speed")
            if val is None:
                return None
            return max(1, int(val))
        return None

    async def async_set_native_value(self, value: float) -> None:
        """Set the speed."""
        await self.coordinator.async_set_control(speed=int(value))


class LifegearBathCountdownNumber(CoordinatorEntity, NumberEntity):
    """Countdown timer for bath heater (minutes)."""

    _attr_name = "倒數關機"
    _attr_icon = "mdi:timer-outline"
    _attr_has_entity_name = True
    _attr_native_min_value = 5
    _attr_native_max_value = 480
    _attr_native_step = 5
    _attr_mode = NumberMode.BOX
    _attr_native_unit_of_measurement = "min"

    def __init__(
        self,
        coordinator: LifegearHRVCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._entry = entry
        self._mac = entry.data[CONF_MAC]
        self._attr_unique_id = f"{self._mac}_countdown"

    @property
    def device_info(self):
        """Return device info."""
        return {
            "identifiers": {(DOMAIN, self._mac)},
            "name": "樂奇浴室暖風機",
            "manufacturer": "Lifegear 樂奇",
            "model": "BD-125W",
        }

    @property
    def native_max_value(self) -> float:
        """Max 180 for 乾燥快速/暖房沐浴/暖房溫控, 480 for others."""
        if self.coordinator.data:
            func = self.coordinator.data.get("md_function")
            if func is not None and int(func) in FUNC_BATH_WITH_COUNTDOWN:
                return 180
        return 480

    @property
    def native_value(self) -> float | None:
        """Return current countdown setting (minutes)."""
        if self.coordinator.data:
            val = self.coordinator.data.get("md_set_countdown")
            if val is not None and val != "":
                return int(val)
        return None

    async def async_set_native_value(self, value: float) -> None:
        """Set countdown time."""
        await self.coordinator.async_set_bath_heater_control(countdown=int(value))
