"""Number platform for Lifegear HRV."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, CONF_MAC
from .coordinator import LifegearHRVCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Lifegear HRV number."""
    coordinator: LifegearHRVCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([LifegearHRVSpeedNumber(coordinator, entry)])


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
        return {
            "identifiers": {(DOMAIN, self._mac)},
            "name": "樂奇全熱交換機",
            "manufacturer": "Lifegear 樂奇",
            "model": "智慧果 M8",
        }

    @property
    def native_value(self) -> float | None:
        """Return current speed."""
        if self.coordinator.data:
            speed = int(self.coordinator.data.get("md_speed", 1))
            return max(1, speed)  # 確保最小值為 1
        return None

    async def async_set_native_value(self, value: float) -> None:
        """Set the speed."""
        await self.coordinator.async_set_control(speed=int(value))
