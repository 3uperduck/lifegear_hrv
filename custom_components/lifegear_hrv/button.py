"""Button platform for Lifegear HRV."""
from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN, CONF_MAC, CONF_DEVICE_MODEL, DEVICE_MODEL_M8,
    DEVICE_MODEL_M8E, DEVICE_MODEL_BATH_HEATER, DEVICE_MODEL_M8E_SENSOR,
    is_m8e_platform,
)
from .coordinator import LifegearHRVCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Lifegear HRV buttons."""
    coordinator: LifegearHRVCoordinator = hass.data[DOMAIN][entry.entry_id]
    model = entry.data.get(CONF_DEVICE_MODEL, DEVICE_MODEL_M8)
    entities: list[ButtonEntity] = []

    if coordinator._has_cloud_creds:
        entities.append(LifegearHRVReloginButton(coordinator, entry))

    # Filter reset buttons (M8-E platform, except sensor-only)
    if is_m8e_platform(model) and model != DEVICE_MODEL_M8E_SENSOR:
        if model == DEVICE_MODEL_BATH_HEATER:
            # Bath heater: only primary filter (FilterType=1)
            entities.append(LifegearFilterResetButton(coordinator, entry, 1, "初效濾網"))
        else:
            # HRV: both filters
            entities.append(LifegearFilterResetButton(coordinator, entry, 2, "高效濾網"))
            entities.append(LifegearFilterResetButton(coordinator, entry, 1, "初效濾網"))

    if entities:
        async_add_entities(entities)


class LifegearHRVReloginButton(CoordinatorEntity, ButtonEntity):
    """Button to manually re-login and refresh AuthCode."""

    _attr_name = "重新登入"
    _attr_icon = "mdi:login"
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: LifegearHRVCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator)
        mac = entry.data.get(CONF_MAC, "unknown")
        self._attr_unique_id = f"{mac}_relogin"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, mac)},
        }

    async def async_press(self) -> None:
        """Handle button press."""
        success = await self.coordinator.async_manual_relogin()
        if success:
            _LOGGER.info("Manual re-login successful")
        else:
            _LOGGER.warning("Manual re-login failed")


class LifegearFilterResetButton(CoordinatorEntity, ButtonEntity):
    """Button to reset filter usage counter."""

    _attr_icon = "mdi:filter-remove"
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: LifegearHRVCoordinator,
        entry: ConfigEntry,
        filter_type: int,
        filter_name: str,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator)
        mac = entry.data.get(CONF_MAC, "unknown")
        self._filter_type = filter_type
        self._attr_name = f"{filter_name}重置"
        self._attr_unique_id = f"{mac}_filter_{filter_type}_reset"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, mac)},
        }

    async def async_press(self) -> None:
        """Reset filter counter."""
        success = await self.coordinator.async_filter_reset(self._filter_type)
        if success:
            _LOGGER.info("Filter type %d reset successful", self._filter_type)
        else:
            _LOGGER.warning("Filter type %d reset failed", self._filter_type)
