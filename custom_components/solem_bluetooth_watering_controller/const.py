"""Constants for SOLEM BLIP integration."""

DOMAIN = "solem_bluetooth_watering_controller"

DEFAULT_SCAN_INTERVAL = 30
MIN_SCAN_INTERVAL = 10
CONTROLLER_MAC_ADDRESS = "controller_mac_address"
NUM_STATIONS = "num_stations"
SPRINKLE_WITH_RAIN = "sprinkle_with_rain"
OPEN_WEATHER_MAP_API_KEY = "open_weather_map_api_key"
SOIL_MOISTURE_SENSOR = "soil_moisture_sensor"
SOIL_MOISTURE_THRESHOLD = "soil_moisture_threshold"
DEFAULT_SOIL_MOISTURE = 40
MAX_SPRINKLES_PER_DAY = 5
MONTHS = [
    "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"
]

# SOLEM BLIP BLE Protocol Constants
SERVICE_UUID = "108b0001-eab5-bc09-d0ea-0b8f467ce8ee"
WRITE_UUID = "108b0002-eab5-bc09-d0ea-0b8f467ce8ee"
NOTIFY_UUID = "108b0003-eab5-bc09-d0ea-0b8f467ce8ee"

# Device Discovery Patterns
DEVICE_NAME_PATTERNS = ["BL1IP", "BLIP"]

# Protocol Commands
COMMIT_COMMAND = "3b00"
STATUS_CHECK_COMMAND = "3105a000010000"  # ON command - recommended for status check
STOP_COMMAND = "31051500ff0000"

# Device Status Codes
DEVICE_STATUS_IDLE = 0x40
DEVICE_STATUS_ALL_STATIONS = 0x41
DEVICE_STATUS_SINGLE_STATION = 0x42
DEVICE_STATUS_PROGRAMMED_OFF = 0x02

# Bluetooth Configuration
BLUETOOTH_TIMEOUT = "bluetooth_timeout"
BLUETOOTH_MIN_TIMEOUT = 5
BLUETOOTH_DEFAULT_TIMEOUT = 15
BLUETOOTH_SCAN_TIMEOUT = 10

# Protocol Constraints
MIN_IRRIGATION_TIME = 1  # minutes
MAX_IRRIGATION_TIME = 720  # 12 hours

OPEN_WEATHER_MAP_FORECAST_URL = "https://api.openweathermap.org/data/2.5/forecast?units=metric&"
OPEN_WEATHER_MAP_CURRENT_URL = "https://api.openweathermap.org/data/2.5/weather?"
OPEN_WEATHER_MAP_API_CACHE_TIMEOUT = "openweathermap_api_cache_timeout"
OPEN_WEATHER_MAP_API_CACHE_MIN_TIMEOUT = 1
OPEN_WEATHER_MAP_API_CACHE_DEFAULT_TIMEOUT = 5
SOLEM_API_MOCK = "solem_api_mock"