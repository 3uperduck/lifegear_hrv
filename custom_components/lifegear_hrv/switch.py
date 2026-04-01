"""Switch platform for Lifegear HRV."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN, CONF_MAC, CONF_DEVICE_MODEL, DEVICE_MODEL_M8,
    DEVICE_MODEL_M8E, DEVICE_MODEL_BATH_HEATER, DEVICE_MODEL_M8E_SENSOR,
)
from .coordinator import LifegearHRVCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Lifegear HRV switch."""
    model = entry.data.get(CONF_DEVICE_MODEL, DEVICE_MODEL_M8)
    if model == DEVICE_MODEL_M8E_SENSOR:
        return  # Sensor-only device, no power switch
    coordinator: LifegearHRVCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([LifegearHRVPowerSwitch(coordinator, entry)])


class LifegearHRVPowerSwitch(CoordinatorEntity, SwitchEntity):
    """Power Switch for Lifegear HRV."""

    _attr_name = "電源"
    _attr_icon = "mdi:power"
    _attr_device_class = SwitchDeviceClass.SWITCH
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: LifegearHRVCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self._entry = entry
        self._mac = entry.data[CONF_MAC]
        self._attr_unique_id = f"{self._mac}_power"

    @property
    def device_info(self):
        """Return device info."""
        model = self._entry.data.get(CONF_DEVICE_MODEL, DEVICE_MODEL_M8)
        names = {
            DEVICE_MODEL_M8: ("樂奇全熱交換機", "智慧果 M8"),
            DEVICE_MODEL_M8E: ("樂奇全熱交換機", "智慧果 M8-E"),
            DEVICE_MODEL_BATH_HEATER: ("樂奇浴室暖風機", "BD-125W"),
        }
        name, model_name = names.get(model, ("樂奇設備", model))
        return {
            "identifiers": {(DOMAIN, self._mac)},
            "name": name,
            "manufacturer": "Lifegear 樂奇",
            "model": model_name,
        }

    @property
    def is_on(self) -> bool:
        """Return true if switch is on."""
        if self.coordinator.data:
            return self.coordinator.data.get("md_ispower") == 1
        return False

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        model = self._entry.data.get(CONF_DEVICE_MODEL, DEVICE_MODEL_M8)
        if model == DEVICE_MODEL_BATH_HEATER:
            await self.coordinator.async_set_bath_heater_control(ispower=1)
        else:
            await self.coordinator.async_set_control(ispower=1)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        model = self._entry.data.get(CONF_DEVICE_MODEL, DEVICE_MODEL_M8)
        if model == DEVICE_MODEL_BATH_HEATER:
            await self.coordinator.async_set_bath_heater_control(ispower=0)
        else:
            await self.coordinator.async_set_control(ispower=0)
