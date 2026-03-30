"""Button platform for Lifegear HRV."""
from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, CONF_MAC
from .coordinator import LifegearHRVCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Lifegear HRV buttons."""
    coordinator: LifegearHRVCoordinator = hass.data[DOMAIN][entry.entry_id]
    if coordinator._has_cloud_creds:
        async_add_entities([LifegearHRVReloginButton(coordinator, entry)])


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
