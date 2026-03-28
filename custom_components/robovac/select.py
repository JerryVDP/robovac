"""Select platform for Eufy Robovac — exposes cleaning type as a selectable entity.

HomeKit/Matter exposes HA select entities as dropdown controls, making cleaning
type (vacuum only, vacuum + mop, etc.) directly controllable from the Home app
without needing to use Developer Tools.

The entity shares the vacuum's existing local connection (via hass.data) rather
than opening a second TCP socket to the device.
"""
from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_DESCRIPTION, CONF_ID, CONF_MAC, CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_VACS, DOMAIN
from .vacuums.base import RobovacCommand
from .tuyalocalapi import TuyaException
from .vacuum import CLEANING_TYPE_UPDATED_SIGNAL

if TYPE_CHECKING:
    from .vacuum import RoboVacEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up cleaning-type select entities from a config entry."""
    entities = [
        CleaningTypeSelectEntity(item)
        for item in config_entry.data[CONF_VACS].values()
    ]
    async_add_entities(entities)


class CleaningTypeSelectEntity(SelectEntity):
    """Select entity for vacuum cleaning type (vacuum / mop / both).

    Sends commands via the vacuum entity's existing RoboVac connection so no
    second TCP socket is opened to the device.

    Display labels match HomeKit Matter Hub requirements for automatic detection.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False

    # HomeKit-compatible display labels (match Matter RVC Clean Mode cluster)
    CLEANING_TYPE_LABELS = {
        "vacuum_only": "Vacuum",
        "mop_only": "Mop",
        "vacuum_and_mop": "Vacuum and mop",
    }

    def __init__(self, item: dict[str, Any]) -> None:
        self._device_id: str = item[CONF_ID]
        self._attr_unique_id = f"{item[CONF_ID]}_cleaning_type"
        self._attr_name = "Cleaning Type"
        self._attr_options: list[str] = []
        self._attr_current_option: str | None = None
        self._label_to_key: dict[str, str] = {}

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, item[CONF_ID])},
            name=item[CONF_NAME],
            manufacturer="Eufy",
            model=item[CONF_DESCRIPTION],
            connections={(CONNECTION_NETWORK_MAC, item[CONF_MAC])},
        )

    def _get_vacuum_entity(self) -> "RoboVacEntity | None":
        return self.hass.data.get(DOMAIN, {}).get(CONF_VACS, {}).get(self._device_id)

    def _build_options(self) -> bool:
        """Populate options from the vacuum model. Returns True if successful."""
        ve = self._get_vacuum_entity()
        if ve is None or ve.vacuum is None:
            return False
        cleaning_types = ve.vacuum.getCleaningTypes()
        if not cleaning_types:
            return False
        # Use HomeKit-compatible labels
        self._attr_options = [self.CLEANING_TYPE_LABELS.get(ct, ct) for ct in cleaning_types]
        self._label_to_key = {self.CLEANING_TYPE_LABELS.get(ct, ct): ct for ct in cleaning_types}
        if self._attr_current_option is None:
            self._attr_current_option = self._attr_options[0]
        return True

    async def async_added_to_hass(self) -> None:
        self._build_options()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                CLEANING_TYPE_UPDATED_SIGNAL.format(self._device_id),
                self._handle_cleaning_type_update,
            )
        )

    @callback
    def _handle_cleaning_type_update(self, cleaning_type: str) -> None:
        if not self._attr_options:
            self._build_options()
        label = self.CLEANING_TYPE_LABELS.get(cleaning_type, cleaning_type)
        if label in self._attr_options and label != self._attr_current_option:
            self._attr_current_option = label
            self.async_write_ha_state()

    async def async_select_option(self, option: str) -> None:
        """Send the chosen cleaning type to the device."""
        ve = self._get_vacuum_entity()
        if ve is None or ve.vacuum is None:
            _LOGGER.error("Cannot set cleaning type: vacuum entity not available")
            return
        if not self._attr_options:
            self._build_options()
        raw_key = self._label_to_key.get(option)
        if raw_key is None:
            _LOGGER.error("Unknown cleaning type option: %s", option)
            return
        dps_code = ve.get_dps_code("CLEANING_TYPE")
        command_value = ve.vacuum.getRoboVacCommandValue(RobovacCommand.CLEANING_TYPE, raw_key)
        _LOGGER.debug("Setting cleaning type %s → DPS %s = %s", raw_key, dps_code, command_value)
        try:
            await ve.vacuum.async_set({dps_code: command_value})
            self._attr_current_option = option
            self.async_write_ha_state()
        except TuyaException as e:
            _LOGGER.error("Failed to set cleaning type: %s", e)
