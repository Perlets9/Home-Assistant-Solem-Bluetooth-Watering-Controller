"""Switch setup for SOLEM BLIP Integration.

This module implements switch entities for controlling irrigation stations
as specified in the reverse engineering guide.
"""

from dataclasses import dataclass
import logging
import asyncio
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import EntityCategory

from . import MyConfigEntry
from .base import SolemBaseEntity
from .coordinator import SolemCoordinator
from .const import MIN_IRRIGATION_TIME

_LOGGER = logging.getLogger(__name__)


@dataclass
class SwitchTypeClass:
    """Class for holding switch type to switch class."""

    device_type: str
    switch_class: object


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: MyConfigEntry,
    async_add_entities: AddEntitiesCallback,
):
    """Set up the Switch entities."""
    coordinator: SolemCoordinator = config_entry.runtime_data.coordinator

    # Create switches for individual stations and all stations
    switches = []
    
    # Individual station switches
    for station_num in range(1, coordinator.num_stations + 1):
        switch_data = {
            "device_id": f"{coordinator.controller_mac_address}_station_{station_num}_switch",
            "device_name": f"Station {station_num}",
            "device_type": "STATION_SWITCH",
            "station_number": station_num,
            "software_version": "1.0",
            "icon": "mdi:sprinkler",
        }
        switches.append(StationSwitch(coordinator, switch_data))
    
    # All stations switch
    all_stations_data = {
        "device_id": f"{coordinator.controller_mac_address}_all_stations_switch",
        "device_name": "All Stations",
        "device_type": "ALL_STATIONS_SWITCH", 
        "station_number": 0,  # 0 means all stations
        "software_version": "1.0",
        "icon": "mdi:sprinkler-variant",
    }
    switches.append(AllStationsSwitch(coordinator, all_stations_data))

    async_add_entities(switches)


class SolemSwitchEntity(SolemBaseEntity, SwitchEntity):
    """Base class for SOLEM switch entities."""
    
    def __init__(
        self, coordinator: SolemCoordinator, device: dict[str, Any]
    ) -> None:
        """Initialize entity."""
        super().__init__(coordinator, device, None)
        self.station_number = device.get("station_number", 1)


class StationSwitch(SolemSwitchEntity):
    """Switch entity for controlling individual irrigation stations."""

    def __init__(self, coordinator: SolemCoordinator, device: dict[str, Any]):
        super().__init__(coordinator, device)
        self._attr_name = f"Station {self.station_number}"
        
    @property
    def is_on(self) -> bool:
        """Return true if the station is currently irrigating."""
        if hasattr(self.coordinator, 'device_status') and self.coordinator.device_status:
            mode = self.coordinator.device_status.get("mode", "idle")
            active = self.coordinator.device_status.get("active", False)
            
            # Check if this specific station is active
            if mode == "single_station_active" and active:
                # This is a limitation - we can't know which specific station is active
                # from the device status alone. For now, assume first station.
                return self.station_number == 1
            elif mode == "all_stations_active" and active:
                return True
                
        # Fallback to coordinator station state
        if self.station_number <= len(self.coordinator.stations):
            station_state = self.coordinator.stations[self.station_number - 1].state
            return station_state == "Sprinkling"
            
        return False

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the station irrigation on."""
        duration = self.coordinator.irrigation_manual_duration
        _LOGGER.info(f"Turning on station {self.station_number} for {duration} minutes")
        
        try:
            await self.coordinator.start_irrigation(self.station_number, duration)
        except Exception as ex:
            _LOGGER.error(f"Failed to start irrigation for station {self.station_number}: {ex}")
            raise

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the station irrigation off."""
        _LOGGER.info(f"Turning off station {self.station_number}")
        
        try:
            await self.coordinator.stop_irrigation()
        except Exception as ex:
            _LOGGER.error(f"Failed to stop irrigation: {ex}")
            raise
            
    @property
    def extra_state_attributes(self):
        """Return additional state attributes."""
        attrs = {}
        if hasattr(self.coordinator, 'device_status') and self.coordinator.device_status:
            attrs["device_mode"] = self.coordinator.device_status.get("mode", "unknown")
            attrs["timer_remaining"] = self.coordinator.device_status.get("timer_minutes", 0)
        
        # Station specific attributes
        if self.station_number <= len(self.coordinator.stations):
            station = self.coordinator.stations[self.station_number - 1]
            attrs["station_state"] = station.state
            
        attrs["duration_minutes"] = self.coordinator.irrigation_manual_duration
        return attrs


class AllStationsSwitch(SolemSwitchEntity):
    """Switch entity for controlling all irrigation stations."""

    def __init__(self, coordinator: SolemCoordinator, device: dict[str, Any]):
        super().__init__(coordinator, device)
        self._attr_name = "All Stations"
        
    @property
    def is_on(self) -> bool:
        """Return true if all stations are currently irrigating."""
        if hasattr(self.coordinator, 'device_status') and self.coordinator.device_status:
            mode = self.coordinator.device_status.get("mode", "idle")
            active = self.coordinator.device_status.get("active", False)
            return mode == "all_stations_active" and active
            
        # Fallback to checking if any station is active
        for station in self.coordinator.stations:
            if station.state == "Sprinkling":
                return True
        return False

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on all stations irrigation."""
        duration = self.coordinator.irrigation_manual_duration
        _LOGGER.info(f"Turning on all stations for {duration} minutes")
        
        try:
            await self.coordinator.api.start_irrigation_all_stations(duration)
            
            # Update coordinator states
            self.coordinator.controller.state = "Active - All Stations"
            for station in self.coordinator.stations:
                station.state = "Sprinkling"
                
            # Trigger coordinator update
            data = await self.coordinator.async_update_all_sensors()
            if data is not None:
                self.coordinator.async_set_updated_data(data)
                
        except Exception as ex:
            _LOGGER.error(f"Failed to start irrigation for all stations: {ex}")
            raise

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off all stations irrigation."""
        _LOGGER.info("Turning off all stations")
        
        try:
            await self.coordinator.stop_irrigation()
        except Exception as ex:
            _LOGGER.error(f"Failed to stop irrigation: {ex}")
            raise
            
    @property
    def extra_state_attributes(self):
        """Return additional state attributes."""
        attrs = {}
        if hasattr(self.coordinator, 'device_status') and self.coordinator.device_status:
            attrs["device_mode"] = self.coordinator.device_status.get("mode", "unknown")
            attrs["timer_remaining"] = self.coordinator.device_status.get("timer_minutes", 0)
        
        attrs["duration_minutes"] = self.coordinator.irrigation_manual_duration
        attrs["num_stations"] = self.coordinator.num_stations
        return attrs
