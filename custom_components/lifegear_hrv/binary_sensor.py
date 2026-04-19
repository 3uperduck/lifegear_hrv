"""Binary sensor platform for Lifegear HRV (M8 connectivity)."""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
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
    """Set up Lifegear HRV binary sensors."""
    coordinator: LifegearHRVCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([LifegearHRVConnectivity(coordinator, entry)])


class LifegearHRVConnectivity(CoordinatorEntity, BinarySensorEntity):
    """M8 device connectivity sensor."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_icon = "mdi:wifi-check"
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: LifegearHRVCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator)
        mac = entry.data.get(CONF_MAC, "unknown")
        # Friendly name is always just "連線狀態" — HA's device page already
        # groups entities under the owning device, so prefixing with the
        # device model (M8 / M8-E / HRV / 暖風機) would just duplicate
        # information already visible in the card header.
        self._attr_name = "連線狀態"
        self._attr_unique_id = f"lifegear_hrv_{mac}_connectivity"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, mac)},
        }

    @property
    def is_on(self) -> bool | None:
        """Return True if M8 is online."""
        if not self.coordinator.data:
            return False
        data = self.coordinator.data

        # Local mode: check if M8 pushed data recently
        if data.get("_local"):
            return bool(data.get("_m8_online", False))

        # Cloud mode: use isconnect field
        isconnect = data.get("md_isconnect")
        if isconnect is None:
            return None
        return bool(int(isconnect))

    @property
    def extra_state_attributes(self) -> dict:
        """Return last seen timestamp."""
        if not self.coordinator.data:
            return {}
        data = self.coordinator.data
        attrs = {}
        if data.get("_sensor_ts"):
            attrs["last_data_received"] = data["_sensor_ts"]
        return attrs
