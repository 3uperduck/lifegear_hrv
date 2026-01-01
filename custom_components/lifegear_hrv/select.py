"""Select platform for Lifegear HRV."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MODE_NAMES, MODE_NAME_TO_VALUE, CONF_MAC
from .coordinator import LifegearHRVCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Lifegear HRV select."""
    coordinator: LifegearHRVCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([LifegearHRVModeSelect(coordinator, entry)])


class LifegearHRVModeSelect(CoordinatorEntity, SelectEntity):
    """Mode Select for Lifegear HRV."""

    _attr_name = "模式"
    _attr_icon = "mdi:air-filter"
    _attr_has_entity_name = True
    _attr_options = list(MODE_NAMES.values())

    def __init__(
        self,
        coordinator: LifegearHRVCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the select."""
        super().__init__(coordinator)
        self._entry = entry
        self._mac = entry.data[CONF_MAC]
        self._attr_unique_id = f"{self._mac}_mode"

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
    def current_option(self) -> str | None:
        """Return current mode."""
        if self.coordinator.data:
            mode = int(self.coordinator.data.get("md_mode", 1))
            return MODE_NAMES.get(mode, "自動")
        return None

    async def async_select_option(self, option: str) -> None:
        """Change the mode."""
        mode_value = MODE_NAME_TO_VALUE.get(option, 1)
        await self.coordinator.async_set_control(mode=mode_value)
