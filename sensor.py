"""Support for August sensors."""
import logging

from homeassistant.components.sensor import DEVICE_CLASS_BATTERY
from homeassistant.helpers.entity import Entity

from . import (
    DEFAULT_NAME,
    DOMAIN,
    MIN_TIME_BETWEEN_DETAIL_UPDATES,
    async_detail_provider,
)

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = MIN_TIME_BETWEEN_DETAIL_UPDATES


async def _async_retrieve_device_battery_state(detail):
    """Get the latest state of the sensor."""
    if detail is None:
        return None

    return detail.battery_level


async def _async_retrieve_linked_keypad_battery_state(detail):
    """Get the latest state of the sensor."""
    if detail is None:
        return None

    if detail.keypad is None:
        return None

    battery_level = detail.keypad.battery_level

    _LOGGER.debug("keypad battery level: %s %s", battery_level, battery_level.lower())

    if battery_level.lower() == "full":
        return 100
    if battery_level.lower() == "medium":
        return 60
    if battery_level.lower() == "low":
        return 10

    return 0


SENSOR_NAME = 0
SENSOR_DEVICE_CLASS = 1
SENSOR_STATE_PROVIDER = 2
SENSOR_UNIT_OF_MEASUREMENT = 3

# sensor_type: [name, device_class, async_state_provider, unit_of_measurement]
SENSOR_TYPES_BATTERY = {
    "device_battery": [
        "Battery",
        DEVICE_CLASS_BATTERY,
        _async_retrieve_device_battery_state,
        "%",
    ],
    "linked_keypad_battery": [
        "Keypad Battery",
        DEVICE_CLASS_BATTERY,
        _async_retrieve_linked_keypad_battery_state,
        "%",
    ],
}


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the August sensors."""
    data = hass.data[DOMAIN][config_entry.entry_id]
    devices = []

    batteries = {
        "device_battery": [],
        "linked_keypad_battery": [],
    }
    for device in data.doorbells:
        batteries["device_battery"].append(device)
    for device in data.locks:
        batteries["device_battery"].append(device)
        batteries["linked_keypad_battery"].append(device)

    for sensor_type in SENSOR_TYPES_BATTERY:
        for device in batteries[sensor_type]:
            async_state_provider = SENSOR_TYPES_BATTERY[sensor_type][
                SENSOR_STATE_PROVIDER
            ]
            detail = await async_detail_provider(data, device)
            state = await async_state_provider(detail)
            if state is None:
                _LOGGER.debug(
                    "Not adding battery sensor class %s for %s %s because it is not present",
                    SENSOR_TYPES_BATTERY[sensor_type][SENSOR_DEVICE_CLASS],
                    device.device_name,
                    SENSOR_TYPES_BATTERY[sensor_type][SENSOR_NAME],
                )
            else:
                _LOGGER.debug(
                    "Adding battery sensor class %s for %s %s",
                    SENSOR_TYPES_BATTERY[sensor_type][SENSOR_DEVICE_CLASS],
                    device.device_name,
                    SENSOR_TYPES_BATTERY[sensor_type][SENSOR_NAME],
                )
                devices.append(AugustBatterySensor(data, sensor_type, device))

    async_add_entities(devices, True)


class AugustBatterySensor(Entity):
    """Representation of an August sensor."""

    def __init__(self, data, sensor_type, device):
        """Initialize the sensor."""
        self._data = data
        self._sensor_type = sensor_type
        self._device = device
        self._state = None
        self._available = False
        self._model = None
        self._firmware_version = None

    @property
    def available(self):
        """Return the availability of this sensor."""
        return self._available

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._state

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement."""
        return SENSOR_TYPES_BATTERY[self._sensor_type][SENSOR_UNIT_OF_MEASUREMENT]

    @property
    def device_class(self):
        """Return the class of this device, from component DEVICE_CLASSES."""
        return SENSOR_TYPES_BATTERY[self._sensor_type][SENSOR_DEVICE_CLASS]

    @property
    def name(self):
        """Return the name of the sensor."""
        return "{} {}".format(
            self._device.device_name,
            SENSOR_TYPES_BATTERY[self._sensor_type][SENSOR_NAME],
        )

    async def async_update(self):
        """Get the latest state of the sensor."""
        async_state_provider = SENSOR_TYPES_BATTERY[self._sensor_type][
            SENSOR_STATE_PROVIDER
        ]
        detail = await async_detail_provider(self._data, self._device)
        self._state = await async_state_provider(detail)
        self._available = self._state is not None
        if detail is not None:
            self._firmware_version = detail.firmware_version
            self._model = detail.model

    @property
    def unique_id(self) -> str:
        """Get the unique id of the device sensor."""
        return "{:s}_{:s}".format(
            self._device.device_id,
            SENSOR_TYPES_BATTERY[self._sensor_type][SENSOR_NAME].lower(),
        )

    @property
    def device_info(self):
        """Return the device_info of the device."""
        return {
            "identifiers": {(DOMAIN, self._device.device_id)},
            "name": self._device.device_name,
            "manufacturer": DEFAULT_NAME,
            "model": self._model,
            "sw_version": self._firmware_version,
        }
