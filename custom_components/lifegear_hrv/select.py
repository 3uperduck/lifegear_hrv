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
    DEVICE_MODEL_M8E, DEVICE_MODEL_BATH_HEATER, DEVICE_MODEL_M8E_SENSOR,
    normalize_mode, get_mode_config, is_m8e_platform,
    FUNC_NAMES_BATH, FUNC_NAME_TO_VALUE_BATH,
    SPEED_NAMES_BATH, SPEED_NAME_TO_VALUE_BATH,
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
    model = entry.data.get(CONF_DEVICE_MODEL, DEVICE_MODEL_M8)

    entities: list[SelectEntity] = []
    if model in (DEVICE_MODEL_M8, DEVICE_MODEL_M8E):
        entities.append(LifegearHRVModeSelect(coordinator, entry))
    elif model == DEVICE_MODEL_BATH_HEATER:
        entities.append(LifegearBathFunctionSelect(coordinator, entry))
        entities.append(LifegearBathSpeedSelect(coordinator, entry))

    # Filter alarm time selects
    if is_m8e_platform(model) and model != DEVICE_MODEL_M8E_SENSOR:
        if model == DEVICE_MODEL_BATH_HEATER:
            entities.append(LifegearFilterAlarmSelect(
                coordinator, entry, 1, "初效濾網", ["720", "1440", "2160"],
            ))
        else:
            entities.append(LifegearFilterAlarmSelect(
                coordinator, entry, 2, "高效濾網", ["3000", "4000", "5000", "6000", "7000", "8000"],
            ))
            entities.append(LifegearFilterAlarmSelect(
                coordinator, entry, 1, "初效濾網", ["720", "1440", "2160"],
            ))

    if entities:
        async_add_entities(entities)


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


class LifegearBathFunctionSelect(CoordinatorEntity, SelectEntity):
    """Function Select for bath heater."""

    _attr_name = "功能"
    _attr_icon = "mdi:heat-wave"
    _attr_has_entity_name = True

    def __init__(self, coordinator: LifegearHRVCoordinator, entry: ConfigEntry) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._entry = entry
        self._mac = entry.data.get(CONF_MAC, "")
        self._attr_options = list(FUNC_NAMES_BATH.values())
        self._attr_unique_id = f"{self._mac}_function"
        self._command_time: float = 0
        self._target_option: str | None = None
        self._update_from_coordinator()

    @property
    def device_info(self):
        """Return device info."""
        return {
            "identifiers": {(DOMAIN, self._mac)},
            "name": "樂奇浴室暖風機",
            "manufacturer": "Lifegear 樂奇",
            "model": "BD-125W",
        }

    def _update_from_coordinator(self) -> None:
        """Update from coordinator data."""
        if self.coordinator.data:
            val = self.coordinator.data.get("md_function")
            if val is not None:
                self._attr_current_option = FUNC_NAMES_BATH.get(int(val))
            else:
                self._attr_current_option = None
        else:
            self._attr_current_option = None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data."""
        if self._target_option and (time.monotonic() - self._command_time < _OPTIMISTIC_GRACE):
            if self.coordinator.data:
                val = self.coordinator.data.get("md_function")
                if val is not None and FUNC_NAMES_BATH.get(int(val)) == self._target_option:
                    self._target_option = None
            super()._handle_coordinator_update()
            return
        self._target_option = None
        self._update_from_coordinator()
        super()._handle_coordinator_update()

    async def async_select_option(self, option: str) -> None:
        """Change the function."""
        func_value = FUNC_NAME_TO_VALUE_BATH.get(option)
        if func_value is None:
            return
        self._target_option = option
        self._command_time = time.monotonic()
        self._attr_current_option = option
        self.async_write_ha_state()
        await self.coordinator.async_set_bath_heater_control(function=func_value)


class LifegearBathSpeedSelect(CoordinatorEntity, SelectEntity):
    """Speed Select for bath heater."""

    _attr_name = "風速"
    _attr_icon = "mdi:fan"
    _attr_has_entity_name = True

    def __init__(self, coordinator: LifegearHRVCoordinator, entry: ConfigEntry) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._entry = entry
        self._mac = entry.data.get(CONF_MAC, "")
        self._attr_options = list(SPEED_NAMES_BATH.values())
        self._attr_unique_id = f"{self._mac}_speed"
        self._command_time: float = 0
        self._target_option: str | None = None
        self._update_from_coordinator()

    @property
    def device_info(self):
        """Return device info."""
        return {
            "identifiers": {(DOMAIN, self._mac)},
            "name": "樂奇浴室暖風機",
            "manufacturer": "Lifegear 樂奇",
            "model": "BD-125W",
        }

    def _update_from_coordinator(self) -> None:
        """Update from coordinator data."""
        if self.coordinator.data:
            val = self.coordinator.data.get("md_speed")
            if val is not None and val != "":
                self._attr_current_option = SPEED_NAMES_BATH.get(int(val))
            else:
                self._attr_current_option = None
        else:
            self._attr_current_option = None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data."""
        if self._target_option and (time.monotonic() - self._command_time < _OPTIMISTIC_GRACE):
            if self.coordinator.data:
                val = self.coordinator.data.get("md_speed")
                if val is not None and val != "" and SPEED_NAMES_BATH.get(int(val)) == self._target_option:
                    self._target_option = None
            super()._handle_coordinator_update()
            return
        self._target_option = None
        self._update_from_coordinator()
        super()._handle_coordinator_update()

    async def async_select_option(self, option: str) -> None:
        """Change the speed."""
        speed_value = SPEED_NAME_TO_VALUE_BATH.get(option)
        if speed_value is None:
            return
        self._target_option = option
        self._command_time = time.monotonic()
        self._attr_current_option = option
        self.async_write_ha_state()
        await self.coordinator.async_set_bath_heater_control(speed=speed_value)


class LifegearFilterAlarmSelect(CoordinatorEntity, SelectEntity):
    """Filter alarm time select (更換提醒時數)."""

    _attr_icon = "mdi:filter-cog"
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: LifegearHRVCoordinator,
        entry: ConfigEntry,
        filter_type: int,
        filter_name: str,
        options: list[str],
    ) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._mac = entry.data.get(CONF_MAC, "")
        self._filter_type = filter_type
        self._filter_key = "high" if filter_type == 2 else "primary"
        self._attr_name = f"{filter_name}更換提醒"
        self._attr_unique_id = f"{self._mac}_filter_{filter_type}_alarm"
        self._attr_options = options
        self._update_from_coordinator()

    @property
    def device_info(self):
        """Return device info."""
        return {"identifiers": {(DOMAIN, self._mac)}}

    def _update_from_coordinator(self) -> None:
        """Update from coordinator data."""
        if self.coordinator.data:
            val = self.coordinator.data.get(f"filter_{self._filter_key}_alarm")
            if val is not None:
                self._attr_current_option = str(int(val))
            else:
                self._attr_current_option = None
        else:
            self._attr_current_option = None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data."""
        self._update_from_coordinator()
        super()._handle_coordinator_update()

    async def async_select_option(self, option: str) -> None:
        """Change the alarm time."""
        self._attr_current_option = option
        self.async_write_ha_state()
        await self.coordinator.async_filter_set_alarm_time(
            self._filter_type, int(option),
        )
