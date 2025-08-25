"""Base entity which all other entity platform classes can inherit.

As all entity types have a common set of properties, you can
create a base entity like this and inherit it in all your entity platforms.

This just makes your code more efficient and is totally optional.

See each entity platform (ie sensor.py, switch.py) for how this is inheritted
and what additional properties and methods you need to add for each entity type.

"""

import logging
from typing import Any

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, CONTROLLER_MAC_ADDRESS
from .coordinator import SolemCoordinator

_LOGGER = logging.getLogger(__name__)


class SolemBaseEntity(CoordinatorEntity):
    """Base Entity Class.

    This inherits a CoordinatorEntity class to register your entites to be updated
    by your DataUpdateCoordinator when async_update_data is called, either on the scheduled
    interval or by forcing an update.
    """

    coordinator: SolemCoordinator

    # ----------------------------------------------------------------------------
    # Using attr_has_entity_name = True causes HA to name your entities with the
    # device name and entity name.  Ie if your name property of your entity is
    # Voltage and this entity belongs to a device, Lounge Socket, this will name
    # your entity to be sensor.lounge_socket_voltage
    #
    # It is highly recommended (by me) to use this to give a good name structure
    # to your entities.  However, totally optional.
    # ----------------------------------------------------------------------------
    _attr_has_entity_name = True

    def __init__(
        self, coordinator: SolemCoordinator, device: dict[str, Any], parameter: str
    ) -> None:
        """Initialise entity."""
        super().__init__(coordinator)
        self.device = device
        self.device_id = device["device_id"]
        self.parameter = parameter

    @callback
    def _handle_coordinator_update(self) -> None:
        """Update sensor with latest data from coordinator."""
        # This method is called by your DataUpdateCoordinator when a successful update runs.
        self.device = self.coordinator.get_device(self.device_id)
        
        if self.device is None:
            _LOGGER.warning(
                "Device data not available for device_id: %s. Coordinator data may be incomplete.",
                self.device_id,
            )
            # Still try to update state, but entities will use fallback values
        
        device_name = self.coordinator.get_device_parameter(self.device_id, "device_name") or "Unknown Device"
        _LOGGER.debug(
            "Updating device: %s, %s",
            self.device_id,
            device_name,
        )
        
        try:
            self.async_write_ha_state()
        except Exception as ex:
            _LOGGER.error(
                "Failed to update state for device %s: %s",
                self.device_id,
                ex,
                exc_info=True
            )

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""

        # ----------------------------------------------------------------------------
        # Identifiers are what group entities into the same device.
        # If your device is created elsewhere, you can just specify the indentifiers
        # parameter to link an entity to a device.
        # If your device connects via another device, add via_device parameter with
        # the indentifiers of that device.
        #
        # Device identifiers should be unique, so use your integration name (DOMAIN)
        # and a device uuid, mac address or some other unique attribute.
        # ----------------------------------------------------------------------------
        return DeviceInfo(
            name=self.coordinator.controller_mac_address,
            manufacturer="Solem",
            model=self.coordinator.config_entry.data[CONTROLLER_MAC_ADDRESS].split('-')[0],
            sw_version="1.0",
            identifiers={
                (
                    DOMAIN,
                    self.coordinator.controller_mac_address,
                )
            },
        )

    @property
    def icon(self) -> str:
        """Return the name of the sensor."""
        return self.device["icon"] if self.device else "mdi:help-circle"

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        #return self.parameter.replace("_", " ").title()
        if self.device:
            return self.device["device_name"]
        else:
            # Fallback to parameter name if device data is not available
            return self.parameter.replace("_", " ").title()

    @property
    def unique_id(self) -> str:
        """Return unique id."""

        # ----------------------------------------------------------------------------
        # All entities must have a unique id across your whole Home Assistant server -
        # and that also goes for anyone using your integration who may have many other
        # integrations loaded.
        #
        # Think carefully what you want this to be as changing it later will cause HA
        # to create new entities.
        #
        # It is recommended to have your integration name (DOMAIN), some unique id
        # from your device such as a UUID, MAC address etc (not IP address) and then
        # something unique to your entity (like name - as this would be unique on a
        # device)
        #
        # If in your situation you have some hub that connects to devices which then
        # you want to create multiple sensors for each device, you would do something
        # like.
        #
        # f"{DOMAIN}-{HUB_MAC_ADDRESS}-{DEVICE_UID}-{ENTITY_NAME}""
        #
        # This is even more important if your integration supports multiple instances.
        # ----------------------------------------------------------------------------
        device_uid = self.coordinator.get_device_parameter(self.device_id, "device_uid") or "unknown"
        return f"{DOMAIN}-{self.coordinator.controller_mac_address}-{device_uid}-{self.parameter}"

    @property
    def extra_state_attributes(self):
        """Return the extra state attributes."""
        # Add any additional attributes you want on your sensor.
        attrs = {}
        return attrs