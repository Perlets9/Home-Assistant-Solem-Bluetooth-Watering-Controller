"""SOLEM BLIP API implementation following reverse engineering specifications.

This module implements the complete BLE protocol for SOLEM BLIP irrigation controllers
including device discovery, command sending, status monitoring, and notification handling.
"""
import logging
import struct
import asyncio
from typing import Any, Optional, Callable
from datetime import datetime, timedelta, timezone
from homeassistant.util.dt import as_local
from homeassistant.util import dt as dt_util
from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from tenacity import retry, stop_after_attempt, wait_exponential

from .const import (
    SERVICE_UUID, WRITE_UUID, NOTIFY_UUID,
    COMMIT_COMMAND, STATUS_CHECK_COMMAND, STOP_COMMAND,
    DEVICE_STATUS_IDLE, DEVICE_STATUS_ALL_STATIONS, 
    DEVICE_STATUS_SINGLE_STATION, DEVICE_STATUS_PROGRAMMED_OFF,
    DEVICE_NAME_PATTERNS, BLUETOOTH_SCAN_TIMEOUT,
    MIN_IRRIGATION_TIME, MAX_IRRIGATION_TIME,
    OPEN_WEATHER_MAP_FORECAST_URL, OPEN_WEATHER_MAP_CURRENT_URL
)

import aiohttp

_LOGGER = logging.getLogger(__name__)


class SolemAPI:
    """SOLEM BLIP BLE API implementing the correct protocol from reverse engineering."""

    def __init__(self, device_address: Optional[str] = None, bluetooth_timeout: int = 15) -> None:
        """Initialize the SOLEM BLIP API.
        
        Args:
            device_address: BLE device address (can be None for discovery)
            bluetooth_timeout: Connection timeout in seconds
        """
        self.device_address = device_address
        self.bluetooth_timeout = bluetooth_timeout
        self.mock = False
        self.client: Optional[BleakClient] = None
        self._last_notification: Optional[bytes] = None
        self._notification_event = asyncio.Event()
        self._current_status = {
            "active": False,
            "mode": "idle",
            "timer_remaining": 0,
            "timer_minutes": 0,
            "timer_seconds": 0,
            "sub_status_code": None,
            "raw_response": None
        }

    @staticmethod
    async def discover_devices(timeout: int = BLUETOOTH_SCAN_TIMEOUT) -> list[BLEDevice]:
        """Discover SOLEM BLIP devices via BLE scan.
        
        Returns:
            List of discovered SOLEM BLIP devices
        """
        _LOGGER.debug("Scanning for SOLEM BLIP devices...")
        devices = await BleakScanner.discover(timeout=timeout)
        
        solem_devices = []
        for device in devices:
            # Look for SOLEM devices by name patterns
            if device.name and any(pattern.lower() in device.name.lower() 
                                  for pattern in DEVICE_NAME_PATTERNS):
                _LOGGER.debug(f"Found SOLEM device: {device.name} ({device.address})")
                solem_devices.append(device)
        
        _LOGGER.info(f"Discovered {len(solem_devices)} SOLEM BLIP devices")
        return solem_devices

    async def connect(self) -> bool:
        """Establish connection to the SOLEM device.
        
        Returns:
            True if connection successful
            
        Raises:
            APIConnectionError: If connection fails
        """
        if self.mock:
            _LOGGER.debug("Mock mode enabled, skipping connection")
            return True
            
        if not self.device_address:
            raise APIConnectionError("No device address specified")

        try:
            return await self._connect_with_retries()
        except Exception as ex:
            _LOGGER.error(f"Failed to connect to device {self.device_address}: {ex}")
            raise APIConnectionError(f"Failed to connect to device: {ex}")

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def _connect_with_retries(self) -> bool:
        """Connect to device with retries."""
        if self.client and self.client.is_connected:
            return True
            
        try:
            self.client = BleakClient(self.device_address, timeout=self.bluetooth_timeout)
            await self.client.connect()
            
            if not self.client.is_connected:
                raise APIConnectionError("Failed to establish connection")
                
            # Verify that the device has the correct service
            services = await self.client.get_services()
            if SERVICE_UUID not in [str(service.uuid) for service in services]:
                raise APIConnectionError("Device does not support SOLEM BLIP service")
                
            _LOGGER.debug(f"Successfully connected to {self.device_address}")
            return True
            
        except Exception as ex:
            if self.client:
                try:
                    await self.client.disconnect()
                except:
                    pass
                self.client = None
            raise APIConnectionError(f"Connection failed: {ex}")

    async def disconnect(self):
        """Disconnect from the device."""
        if self.client and self.client.is_connected:
            try:
                # Disable notifications first
                await self.client.stop_notify(NOTIFY_UUID)
            except:
                pass
            
            try:
                await self.client.disconnect()
            except:
                pass
        
        self.client = None
        _LOGGER.debug("Disconnected from device")

    def _notification_handler(self, sender: str, data: bytes):
        """Handle incoming BLE notifications."""
        _LOGGER.debug(f"Received notification: {data.hex()}")
        self._last_notification = data
        self._notification_event.set()

    async def _send_command(self, command_hex: str) -> bool:
        """Send a command to the device.
        
        Args:
            command_hex: Hex string of command to send
            
        Returns:
            True if command sent successfully
        """
        if self.mock:
            _LOGGER.debug(f"Mock mode: would send command {command_hex}")
            return True
            
        try:
            # Ensure we have a connection
            if not self.client or not self.client.is_connected:
                _LOGGER.debug("No connection, attempting to connect...")
                success = await self.connect()
                if not success:
                    _LOGGER.error("Failed to establish connection")
                    return False
            
            # Verify connection is still good
            if not self.client.is_connected:
                _LOGGER.error("Client not connected after connection attempt")
                return False
            
            # Send the main command
            command_bytes = bytes.fromhex(command_hex)
            await self.client.write_gatt_char(WRITE_UUID, command_bytes, response=False)
            await asyncio.sleep(0.1)
            
            # Always send commit
            commit_bytes = bytes.fromhex(COMMIT_COMMAND)
            await self.client.write_gatt_char(WRITE_UUID, commit_bytes, response=False)
            
            _LOGGER.debug(f"Sent command: {command_hex} + commit")
            return True
            
        except Exception as ex:
            _LOGGER.error(f"Failed to send command {command_hex}: {ex}")
            # Try to disconnect and clean up on error
            try:
                if self.client:
                    await self.client.disconnect()
                self.client = None
            except:
                pass
            return False

    def _parse_status_notification(self, data: bytes) -> dict:
        """Parse device status from notification.
        
        Args:
            data: Raw notification data
            
        Returns:
            Dictionary with parsed status information
        """
        if len(data) < 18:
            _LOGGER.warning(f"Notification too short: {len(data)} bytes")
            return self._get_default_status()
            
        # Check if this is the first packet (main status)
        if data[2] != 0x02:
            _LOGGER.debug(f"Skipping non-status packet type: {data[2]:02x}")
            return self._current_status
            
        sub_status = data[3]  # Sub-status byte
        
        # Extract timer from bytes 13-14 (big-endian)
        timer_remaining = 0
        if len(data) >= 15:
            timer_bytes = data[13:15]
            if len(timer_bytes) == 2:
                timer_remaining = struct.unpack(">H", timer_bytes)[0]
        
        # Determine status based on sub_status byte
        if sub_status == DEVICE_STATUS_SINGLE_STATION:
            mode = "single_station_active"
            active = True
        elif sub_status == DEVICE_STATUS_ALL_STATIONS:
            mode = "all_stations_active"
            active = True
        elif sub_status == DEVICE_STATUS_IDLE:
            mode = "idle"
            active = False
        elif sub_status == DEVICE_STATUS_PROGRAMMED_OFF:
            mode = "programmed_off"
            active = False
        else:
            mode = f"unknown_{sub_status:02x}"
            active = False
        
        status = {
            "active": active,
            "mode": mode,
            "timer_remaining": timer_remaining,
            "timer_minutes": timer_remaining // 60,
            "timer_seconds": timer_remaining % 60,
            "sub_status_code": sub_status,
            "raw_response": data.hex()
        }
        
        self._current_status = status
        _LOGGER.debug(f"Parsed status: {status}")
        return status

    def _get_default_status(self) -> dict:
        """Get default status when no valid response."""
        return {
            "active": False,
            "mode": "no_response",
            "timer_remaining": 0,
            "timer_minutes": 0,
            "timer_seconds": 0,
            "sub_status_code": None,
            "raw_response": None
        }

    async def get_status(self) -> dict:
        """Get current device status using non-intrusive ON command.
        
        Returns:
            Dictionary with current status information
        """
        if self.mock:
            return {
                "active": False,
                "mode": "idle",
                "timer_remaining": 0,
                "timer_minutes": 0,
                "timer_seconds": 0,
                "sub_status_code": DEVICE_STATUS_IDLE,
                "raw_response": "mock_response"
            }
            
        if not self.client or not self.client.is_connected:
            await self.connect()
            
        try:
            # Set up notification handler to capture status
            self._last_notification = None
            self._notification_event.clear()
            
            await self.client.start_notify(NOTIFY_UUID, self._notification_handler)
            
            # Send status check command (ON command - non-intrusive)
            success = await self._send_command(STATUS_CHECK_COMMAND)
            
            if success:
                # Wait for notification response (up to 5 seconds)
                try:
                    await asyncio.wait_for(self._notification_event.wait(), timeout=5.0)
                    if self._last_notification:
                        status = self._parse_status_notification(self._last_notification)
                        _LOGGER.debug(f"Status retrieved: {status}")
                        return status
                except asyncio.TimeoutError:
                    _LOGGER.warning("Timeout waiting for status response")
                    
        except Exception as ex:
            _LOGGER.error(f"Failed to get status: {ex}")
            
        return self._get_default_status()

    async def start_irrigation_station(self, station: int, duration_minutes: int) -> bool:
        """Start irrigation for a specific station.
        
        Args:
            station: Station number (1, 2, or 3)
            duration_minutes: Duration in minutes (minimum 1)
            
        Returns:
            True if command sent successfully, False otherwise
        """
        try:
            if not (1 <= station <= 3):
                _LOGGER.error("Station must be between 1 and 3")
                return False
                
            if duration_minutes < MIN_IRRIGATION_TIME:
                _LOGGER.error(f"Minimum irrigation time is {MIN_IRRIGATION_TIME} minute(s)")
                return False
                
            if duration_minutes > MAX_IRRIGATION_TIME:
                _LOGGER.error(f"Maximum irrigation time is {MAX_IRRIGATION_TIME} minutes")
                return False
            
            # Convert to seconds
            duration_seconds = duration_minutes * 60
            
            # Format: 3105 12 [STATION] 00 [SECONDS_HEX] (big-endian)
            # Using struct.pack(">HBBBH", 0x3105, 0x12, station, 0x00, seconds)
            command = struct.pack(">HBBBH", 0x3105, 0x12, station, 0x00, duration_seconds)
            command_hex = command.hex()
            
            _LOGGER.info(f"Starting irrigation: Station {station} for {duration_minutes} minutes ({duration_seconds}s)")
            _LOGGER.debug(f"Command: {command_hex}")
            
            return await self._send_command(command_hex)
            
        except Exception as ex:
            _LOGGER.error(f"Error in start_irrigation_station: {ex}")
            return False

    async def start_irrigation_all_stations(self, duration_minutes: int) -> bool:
        """Start irrigation for all stations.
        
        Args:
            duration_minutes: Duration in minutes (minimum 1)
            
        Returns:
            True if command sent successfully, False otherwise
        """
        try:
            if duration_minutes < MIN_IRRIGATION_TIME:
                _LOGGER.error(f"Minimum irrigation time is {MIN_IRRIGATION_TIME} minute(s)")
                return False
                
            if duration_minutes > MAX_IRRIGATION_TIME:
                _LOGGER.error(f"Maximum irrigation time is {MAX_IRRIGATION_TIME} minutes")
                return False
            
            # Convert to seconds
            duration_seconds = duration_minutes * 60
            
            # Format: 3105 11 0000 [SECONDS_HEX] (big-endian)
            # Using struct.pack(">HBHH", 0x3105, 0x11, 0x0000, seconds)
            command = struct.pack(">HBHH", 0x3105, 0x11, 0x0000, duration_seconds)
            command_hex = command.hex()
            
            _LOGGER.info(f"Starting irrigation: All stations for {duration_minutes} minutes ({duration_seconds}s)")
            _LOGGER.debug(f"Command: {command_hex}")
            
            return await self._send_command(command_hex)
            
        except Exception as ex:
            _LOGGER.error(f"Error in start_irrigation_all_stations: {ex}")
            return False

    async def stop_irrigation(self) -> bool:
        """Stop all irrigation immediately.
        
        Returns:
            True if command sent successfully, False otherwise
        """
        try:
            _LOGGER.info("Stopping irrigation")
            return await self._send_command(STOP_COMMAND)
        except Exception as ex:
            _LOGGER.error(f"Error in stop_irrigation: {ex}")
            return False

    # Legacy method names for compatibility
    async def sprinkle_station_x_for_y_minutes(self, station: int, minutes: int):
        """Legacy method - use start_irrigation_station instead."""
        return await self.start_irrigation_station(station, minutes)

    async def sprinkle_all_stations_for_y_minutes(self, minutes: int):
        """Legacy method - use start_irrigation_all_stations instead."""
        return await self.start_irrigation_all_stations(minutes)

    async def stop_manual_sprinkle(self):
        """Legacy method - use stop_irrigation instead."""
        return await self.stop_irrigation()

    # Deprecated methods - keeping for compatibility but logging warnings
    async def turn_on(self):
        """Deprecated: This method sends ON command which is used for status checking."""
        _LOGGER.warning("turn_on() is deprecated - ON command is used for status checking")
        return await self._send_command(STATUS_CHECK_COMMAND)

    async def turn_off_permanent(self):
        """Turn off controller permanently."""
        _LOGGER.warning("turn_off_permanent() may change device state - use with caution")
        # OFF command: struct.pack(">HBHH", 0x3105, 0xc0, 0x0000, 0x0000)
        command = struct.pack(">HBHH", 0x3105, 0xc0, 0x0000, 0x0000)
        return await self._send_command(command.hex())

    async def turn_off_x_days(self, days: int):
        """Turn off controller for X days."""
        _LOGGER.warning("turn_off_x_days() may change device state - use with caution")
        command = struct.pack(">HBHH", 0x3105, 0xc0, 0x0000, days & 0xFF)
        return await self._send_command(command.hex())

    async def run_program_x(self, program: int):
        """Run a specific program."""
        _LOGGER.warning("run_program_x() may trigger unintended program execution")
        command = struct.pack(">HBHH", 0x3105, 0x14, 0x0000, program & 0xFF)
        return await self._send_command(command.hex())

    async def list_characteristics(self):
        """List available BLE characteristics for debugging."""
        if self.mock:
            _LOGGER.debug("Mock mode: would list characteristics")
            return
            
        if not self.client or not self.client.is_connected:
            await self.connect()
            
        try:
            services = await self.client.get_services()
            for service in services:
                _LOGGER.info(f"Service: {service.uuid}")
                for char in service.characteristics:
                    properties = ", ".join(char.properties)
                    _LOGGER.info(f"  Characteristic: {char.uuid} ({properties})")
        except Exception as ex:
            _LOGGER.error(f"Failed to list characteristics: {ex}")

    # Keep scan_bluetooth for compatibility but mark as deprecated
    async def scan_bluetooth(self):
        """Deprecated: Use discover_devices() instead."""
        _LOGGER.warning("scan_bluetooth() is deprecated, use discover_devices() instead")
        devices = await BleakScanner.discover()
        return devices


class OpenWeatherMapAPI:
    """Class for OpenWeatherMap API."""

    def __init__(self, api_key: str, latitude: str, longitude: str, timeout: int) -> None:
        """Initialise."""
        self.api_key = api_key
        self.latitude = latitude
        self.longitude = longitude
        self.timeout = timeout
        # Initialise the forecast cache as an empty list to avoid iteration
        # errors before any data is fetched from the API.
        self._cache_forecast = []
        self._cache_current = None
        self._last_forecast_fetch_time = None
        self.last_forecast_date = datetime.now().date()
        self._last_current_fetch_time = None
        

    async def get_current_weather(self) -> Any:
        now = dt_util.now()  # Usa datetime com timezone
    
        if self._cache_current and self._last_current_fetch_time and now - self._last_current_fetch_time < timedelta(minutes=self.timeout):
            _LOGGER.debug("Returning cached data.")
            return self._cache_current
    
        weather_url = f"{OPEN_WEATHER_MAP_CURRENT_URL}appid={self.api_key}&lat={self.latitude}&lon={self.longitude}"
        _LOGGER.debug("Getting current weather at : %s", weather_url)
    
        async with aiohttp.ClientSession() as session:
            async with session.get(weather_url) as response:
                try:
                    data = await response.json()
                    _LOGGER.debug("Current Weather Data: %s", data)
    
                    if "dt" in data:
                        utc_dt = datetime.fromtimestamp(data["dt"], tz=timezone.utc)
                        local_dt = as_local(utc_dt)
                        data["dt_txt"] = local_dt.strftime('%Y-%m-%d %H:%M:%S')
                        
                        _LOGGER.debug(
                            f"UTC time from API: {utc_dt.strftime('%Y-%m-%d %H:%M:%S')}, "
                            f"Local time after as_local: {local_dt.strftime('%Y-%m-%d %H:%M:%S')}"
                        )
    
                    self._cache_current = data
                    self._last_current_fetch_time = now
                except Exception as ex:
                    _LOGGER.error("Error processing Current Weather data: JSON format invalid!")
                    raise APIConnectionError("Error processing Current Weather data: JSON format invalid!")
    
        return self._cache_current


    async def is_raining(self) -> dict:
        current_weather = await self.get_current_weather()
        
        return {
            "is_raining": "rain" in current_weather,
            "current": current_weather
        }


    async def get_forecast(self) -> list:
        """Obtains and preserves data from 00h till 00h of the next day."""
        now = datetime.now()
    
        # If data is recent returns what is on the cache
        if self._cache_forecast and self._last_forecast_fetch_time and now - self._last_forecast_fetch_time < timedelta(minutes=self.timeout):
            _LOGGER.debug("Returning cached data.")
            return self._cache_forecast
    
        temp_cache = self._cache_forecast.copy() if self._cache_forecast else []
    
        # If it is a new day, resets and preserves the block from 0h to 3h obtained yesterday
        if self.last_forecast_date != now.date():
            _LOGGER.debug(f"Day changed, will get 00h forecast to new day...")
            last_00_03_forecast = None
    
            for forecast in self._cache_forecast:
                forecast_time_str = forecast["dt_txt"]
                forecast_dt = datetime.strptime(forecast_time_str, "%Y-%m-%d %H:%M:%S")
                if forecast_dt.hour == 0:
                    _LOGGER.debug(f"Found 00h block: {forecast_time_str}")
                    last_00_03_forecast = forecast
                    break
    
            self._cache_forecast = []
            self.last_forecast_date = now.date()
    
            if last_00_03_forecast:
                self._cache_forecast.append(last_00_03_forecast)
                _LOGGER.debug(f"Inserting 00h block in new cache: {last_00_03_forecast}")
    
        current_hour = now.hour
        forecast_hours = [h for h in range(0, 21, 3) if h >= current_hour]
        forecast_hours.append(0)
        items = len(forecast_hours)
    
        weather_url = f"{OPEN_WEATHER_MAP_FORECAST_URL}&appid={self.api_key}&lat={self.latitude}&lon={self.longitude}&cnt={items}"
        _LOGGER.debug("Getting forecast at: %s", weather_url)
    
        async with aiohttp.ClientSession() as session:
            async with session.get(weather_url) as response:
                try:
                    data = await response.json()
                    _LOGGER.debug("Forecast Weather Data: %s", data)
    
                    for item in data["list"]:
                        # Mantém dt_txt tal como está (já está em hora local)
                        forecast_time_str = item["dt_txt"]
    
                        _LOGGER.debug(
                            f"Forecast timestamp from API (dt_txt): {forecast_time_str}"
                        )
    
                        existing_index = next(
                            (index for index, forecast in enumerate(self._cache_forecast)
                             if forecast["dt_txt"] == forecast_time_str),
                            None
                        )
    
                        if existing_index is not None:
                            _LOGGER.debug(f"Replacing block for {forecast_time_str}")
                            self._cache_forecast[existing_index] = item
                        else:
                            _LOGGER.debug(f"Appending item {forecast_time_str} to _cache_forecast")
                            self._cache_forecast.append(item)
    
                    self._last_forecast_fetch_time = now
    
                except Exception as ex:
                    _LOGGER.error("Error processing Forecast Weather data: JSON format invalid!", exc_info=True)
    
                    if not self._cache_forecast:
                        self._cache_forecast = temp_cache
    
                    raise APIConnectionError("Error processing Forecast Weather data: JSON format invalid!")
    
        _LOGGER.debug(f"self._cache_forecast={self._cache_forecast}")
        return self._cache_forecast


    async def will_it_rain(self) -> dict:
        """Verifies if it will rain for the rest of the day."""
        forecast = await self.get_forecast()
    
        now = dt_util.now()  # Hora local garantida
        today_str = now.strftime("%Y-%m-%d")
        current_hour = now.hour
    
        block_hours = [h for h in range(0, 21, 3)]
        current_block = max([h for h in block_hours if h <= current_hour])
    
        relevant_forecasts = []
        for item in forecast:
            forecast_time_str = item["dt_txt"]  # já está em hora local
            forecast_date, forecast_hour_minute = forecast_time_str.split(" ")
            forecast_hour, _, _ = forecast_hour_minute.split(":")
            forecast_hour = int(forecast_hour)
    
            if forecast_date == today_str and forecast_hour >= current_block:
                relevant_forecasts.append(item)
    
        will_rain = any(item.get("pop", 0) > 0.50 for item in relevant_forecasts)
    
        return {
            "will_rain": will_rain,
            "forecast": forecast
        }
        

    async def get_total_rain_forecast_for_today(self) -> float:
        """Calculates total amount of rain predicted (mm) for the rest of the day."""
    
        will_it_rain_result = await self.will_it_rain()
        forecasts = will_it_rain_result.get("forecast", [])
    
        now = dt_util.now()
        current_time = now.hour * 60 + now.minute
        today_str = now.strftime("%Y-%m-%d")
        total_rain_mm = 0.0
    
        for item in forecasts:
            forecast_time_str = item["dt_txt"]  # já está em hora local
            forecast_date, forecast_hour_minute = forecast_time_str.split(" ")
            forecast_hour, _, _ = forecast_hour_minute.split(":")
            forecast_hour = int(forecast_hour)
    
            rain_data = item.get("rain", {})
            rain_mm = rain_data.get("3h", 0.0)
    
            # Apenas considera previsões do dia atual
            if forecast_date != today_str:
                continue
    
            forecast_start_minute = forecast_hour * 60
            forecast_end_minute = forecast_start_minute + 180
    
            # Se o bloco já passou, ignorar
            if forecast_end_minute <= current_time:
                continue
    
            # Se estamos dentro do bloco atual, calcular a fração exata de tempo restante
            if forecast_start_minute <= current_time < forecast_end_minute:
                remaining_minutes = forecast_end_minute - current_time
                rain_mm = (remaining_minutes / 180) * rain_mm  # Ajuste proporcional
    
            total_rain_mm += rain_mm
    
        return total_rain_mm


class APIConnectionError(Exception):
    """Exception class for connection error."""
