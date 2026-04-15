"""Lifegear HRV Integration."""
from __future__ import annotations

import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import (
    DOMAIN,
    CONF_USER_ID, CONF_AUTH_CODE, CONF_LOGIN_METHOD, CONF_LOCAL_SERVER,
    CONF_DEVICE_MODEL, CONF_MAC,
    LOGIN_METHOD_MANUAL,
    DEVICE_MODEL_M8, DEVICE_MODEL_M8E,
)
from .coordinator import LifegearHRVCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.SENSOR, Platform.SWITCH, Platform.SELECT, Platform.NUMBER]


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Migrate old entry to new version."""
    _LOGGER.debug("Migrating from version %s", config_entry.version)

    if config_entry.version == 1:
        # v1 -> v2: add login_method field (existing entries are manual)
        new_data = {**config_entry.data, CONF_LOGIN_METHOD: LOGIN_METHOD_MANUAL}
        hass.config_entries.async_update_entry(config_entry, data=new_data, version=2)
        _LOGGER.info("Migration to version 2 successful")

    if config_entry.version == 2:
        # v2 -> v3: no data changes needed (cloud entries stay as-is)
        hass.config_entries.async_update_entry(config_entry, version=3)
        _LOGGER.info("Migration to version 3 successful")

    if config_entry.version == 3:
        # v3 -> v4: add device_model field (existing entries are M8)
        new_data = {**config_entry.data, CONF_DEVICE_MODEL: DEVICE_MODEL_M8}
        hass.config_entries.async_update_entry(config_entry, data=new_data, version=4)
        _LOGGER.info("Migration to version 4 successful")

    if config_entry.version == 4:
        # v4 -> v5: drop proxied air-quality entities from M8-E HRV.
        # The HRV body has no native CO2/PM2.5/Humidity/Temperature
        # sensors; the cloud aggregates them from the paired M8-E wall
        # sensor (which already exposes them as its own device). Keeping
        # them on HRV duplicates entities and confuses history/energy.
        if config_entry.data.get(CONF_DEVICE_MODEL) == DEVICE_MODEL_M8E:
            from homeassistant.helpers import entity_registry as er
            ent_reg = er.async_get(hass)
            mac = config_entry.data.get(CONF_MAC)
            if mac:
                for suffix in ("_co2", "_pm25", "_humidity", "_temperature"):
                    eid = ent_reg.async_get_entity_id(
                        "sensor", DOMAIN, f"{mac}{suffix}"
                    )
                    if eid:
                        _LOGGER.info(
                            "Removing orphaned proxied entity %s", eid
                        )
                        ent_reg.async_remove(eid)
        hass.config_entries.async_update_entry(config_entry, version=5)
        _LOGGER.info("Migration to version 5 successful")

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Lifegear HRV from a config entry."""
    coordinator = LifegearHRVCoordinator(hass, entry)
    await coordinator.async_cloud_login()
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    await _async_fixup_entity_categories(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def _async_fixup_entity_categories(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Force-update entity_category on registry entries that predate category
    assignments. HA's registry intentionally preserves the existing
    entity_category across reloads, so newly-annotated class attributes are
    ignored for already-registered entries unless we push an explicit update.
    """
    if entry.data.get(CONF_DEVICE_MODEL) != DEVICE_MODEL_M8E:
        return
    mac = entry.data.get(CONF_MAC)
    if not mac:
        return
    from homeassistant.helpers import entity_registry as er
    from homeassistant.const import EntityCategory
    ent_reg = er.async_get(hass)
    # (domain, unique_id_suffix, target category)
    targets = [
        ("button", "_filter_1_reset", EntityCategory.CONFIG),
        ("button", "_filter_2_reset", EntityCategory.CONFIG),
        ("select", "_filter_1_alarm", EntityCategory.CONFIG),
        ("select", "_filter_2_alarm", EntityCategory.CONFIG),
    ]
    for domain, suffix, category in targets:
        eid = ent_reg.async_get_entity_id(domain, DOMAIN, f"{mac}{suffix}")
        if not eid:
            continue
        current = ent_reg.async_get(eid)
        if current and current.entity_category != category:
            ent_reg.async_update_entity(eid, entity_category=category)
            _LOGGER.info(
                "Updated entity_category for %s → %s",
                eid, category.value if hasattr(category, "value") else category,
            )

    # Remove the orphaned HRV body temp sensor (turned out to be a
    # bit-for-bit duplicate of TempRA — see 4.3.1 release notes).
    body_eid = ent_reg.async_get_entity_id(
        "sensor", DOMAIN, f"{mac}_hrv_body_temp"
    )
    if body_eid:
        _LOGGER.info("Removing orphan HRV body-temp entity %s", body_eid)
        ent_reg.async_remove(body_eid)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
