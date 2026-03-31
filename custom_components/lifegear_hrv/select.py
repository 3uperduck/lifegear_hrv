"""Select platform for Lifegear HRV."""
from __future__ import annotations

import time

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN, CONF_MAC, CONF_DEVICE_MODEL, DEVICE_MODEL_M8,
    normalize_mode, get_mode_config,
)
from .coordinator import LifegearHRVCoordinator

# Seconds to ignore coordinator updates after a command (let cloud + M8 settle)
_OPTIMISTIC_GRACE = 8


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

    def __init__(
        self,
        coordinator: LifegearHRVCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the select."""
        super().__init__(coordinator)
        self._entry = entry
        self._mac = entry.data.get(CONF_MAC, "")
        model = entry.data.get(CONF_DEVICE_MODEL, DEVICE_MODEL_M8)
        self._mode_names, self._mode_name_to_value = get_mode_config(model)
        self._attr_options = list(self._mode_names.values())
        self._attr_unique_id = f"{self._mac}_mode"
        self._command_time: float = 0
        self._target_option: str | None = None
        self._update_from_coordinator()

    @property
    def device_info(self):
        """Return device info."""
        from .const import DEVICE_MODEL_M8E
        model = self._entry.data.get(CONF_DEVICE_MODEL, DEVICE_MODEL_M8)
        model_name = "智慧果 M8-E" if model == DEVICE_MODEL_M8E else "智慧果 M8"
        return {
            "identifiers": {(DOMAIN, self._mac)},
            "name": "樂奇全熱交換機",
            "manufacturer": "Lifegear 樂奇",
            "model": model_name,
        }

    def _update_from_coordinator(self) -> None:
        """Update _attr_current_option from coordinator data."""
        if self.coordinator.data:
            val = self.coordinator.data.get("md_mode", 1)
            if val == "":
                self._attr_current_option = None
            else:
                mode_int = normalize_mode(val)
                default = list(self._mode_names.values())[0] if self._mode_names else None
                self._attr_current_option = self._mode_names.get(mode_int, default)
        else:
            self._attr_current_option = None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if self._target_option and (time.monotonic() - self._command_time < _OPTIMISTIC_GRACE):
            if self.coordinator.data:
                val = self.coordinator.data.get("md_mode", 1)
                mode_int = normalize_mode(val)
                actual = self._mode_names.get(mode_int, None)
                if actual == self._target_option:
                    self._target_option = None
                    self._attr_current_option = actual
            super()._handle_coordinator_update()
            return

        self._target_option = None
        self._update_from_coordinator()
        super()._handle_coordinator_update()

    async def async_select_option(self, option: str) -> None:
        """Change the mode."""
        mode_value = self._mode_name_to_value.get(option, 1)
        self._target_option = option
        self._command_time = time.monotonic()
        self._attr_current_option = option
        self.async_write_ha_state()
        await self.coordinator.async_set_control(mode=mode_value)
