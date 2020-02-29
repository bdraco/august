"""Support for August sensors."""
import logging

from homeassistant.components.sensor import DEVICE_CLASS_BATTERY
from homeassistant.const import UNIT_PERCENTAGE
from homeassistant.core import callback
from homeassistant.helpers.entity import Entity

from .const import DATA_AUGUST, DOMAIN
from .entity import AugustEntityMixin

_LOGGER = logging.getLogger(__name__)


def _retrieve_device_battery_state(detail):
    """Get the latest state of the sensor."""
    return detail.battery_level


def _retrieve_linked_keypad_battery_state(detail):
    """Get the latest state of the sensor."""
    if detail.keypad is None:
        return None

    return detail.keypad.battery_percentage


SENSOR_TYPES_BATTERY = {
    "device_battery": {
        "name": "Battery",
        "state_provider": _retrieve_device_battery_state,
    },
    "linked_keypad_battery": {
        "name": "Keypad Battery",
        "state_provider": _retrieve_linked_keypad_battery_state,
    },
}


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the August sensors."""
    data = hass.data[DOMAIN][config_entry.entry_id][DATA_AUGUST]
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
            state_provider = SENSOR_TYPES_BATTERY[sensor_type]["state_provider"]
            detail = data.get_device_detail(device.device_id)
            state = state_provider(detail)
            sensor_name = SENSOR_TYPES_BATTERY[sensor_type]["name"]
            if state is None:
                _LOGGER.debug(
                    "Not adding battery sensor %s for %s because it is not present",
                    sensor_name,
                    device.device_name,
                )
            else:
                _LOGGER.debug(
                    "Adding battery sensor %s for %s", sensor_name, device.device_name,
                )
                devices.append(AugustBatterySensor(data, sensor_type, device))

    async_add_entities(devices, True)


class AugustBatterySensor(AugustEntityMixin, Entity):
    """Representation of an August sensor."""

    def __init__(self, data, sensor_type, device):
        """Initialize the sensor."""
        super().__init__(data, device)
        self._data = data
        self._sensor_type = sensor_type
        self._device = device
        self._state = None
        self._available = False
        self._update_from_data()

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
        return UNIT_PERCENTAGE

    @property
    def device_class(self):
        """Return the class of this device, from component DEVICE_CLASSES."""
        return DEVICE_CLASS_BATTERY

    @property
    def name(self):
        """Return the name of the sensor."""
        device_name = self._device.device_name
        sensor_name = SENSOR_TYPES_BATTERY[self._sensor_type]["name"]
        return f"{device_name} {sensor_name}"

    @callback
    def _update_from_data(self):
        """Get the latest state of the sensor."""
        state_provider = SENSOR_TYPES_BATTERY[self._sensor_type]["state_provider"]
        self._state = state_provider(self._detail)
        self._available = self._state is not None

    @property
    def unique_id(self) -> str:
        """Get the unique id of the device sensor."""
        return f"{self._device_id}_{self._sensor_type}"
