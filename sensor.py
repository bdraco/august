"""Support for August sensors."""
import logging

from homeassistant.components.sensor import DEVICE_CLASS_BATTERY
from homeassistant.helpers.entity import Entity

from . import DATA_AUGUST, MIN_TIME_BETWEEN_DETAIL_UPDATES

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = MIN_TIME_BETWEEN_DETAIL_UPDATES


async def _async_retrieve_battery_state(data, doorbell):
    """Get the latest state of the sensor."""
    detail = await data.async_get_doorbell_detail(doorbell.device_id)

    if detail is None:
        return None

    return detail.battery_level


SENSOR_NAME = 0
SENSOR_DEVICE_CLASS = 1
SENSOR_STATE_PROVIDER = 2
SENSOR_UNIT_OF_MEASUREMENT = 3

# sensor_type: [name, device_class, async_state_provider, unit_of_measurement]
SENSOR_TYPES_DOORBELL = {
    "doorbell_battery": [
        "Battery",
        DEVICE_CLASS_BATTERY,
        _async_retrieve_battery_state,
        "%",
    ],
}


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up the August sensors."""
    data = hass.data[DATA_AUGUST][config_entry.entry_id]
    devices = []

    for doorbell in data.doorbells:
        for sensor_type in SENSOR_TYPES_DOORBELL:
            async_state_provider = SENSOR_TYPES_DOORBELL[sensor_type][
                SENSOR_STATE_PROVIDER
            ]
            state = await async_state_provider(data, doorbell)
            if state is None:
                _LOGGER.debug(
                    "Not adding doorbell sensor class %s for %s because it is not present",
                    SENSOR_TYPES_DOORBELL[sensor_type][SENSOR_DEVICE_CLASS],
                    doorbell.device_name,
                )
            else:
                _LOGGER.debug(
                    "Adding doorbell sensor class %s for %s",
                    SENSOR_TYPES_DOORBELL[sensor_type][SENSOR_DEVICE_CLASS],
                    doorbell.device_name,
                )
                devices.append(AugustDoorbellSensor(data, sensor_type, doorbell))

    async_add_entities(devices, True)


class AugustDoorbellSensor(Entity):
    """Representation of an August sensor."""

    def __init__(self, data, sensor_type, doorbell):
        """Initialize the sensor."""
        self._data = data
        self._sensor_type = sensor_type
        self._doorbell = doorbell
        self._state = None
        self._available = False

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
        return SENSOR_TYPES_DOORBELL[self._sensor_type][SENSOR_UNIT_OF_MEASUREMENT]

    @property
    def device_class(self):
        """Return the class of this device, from component DEVICE_CLASSES."""
        return SENSOR_TYPES_DOORBELL[self._sensor_type][SENSOR_DEVICE_CLASS]

    @property
    def name(self):
        """Return the name of the sensor."""
        return "{} {}".format(
            self._doorbell.device_name,
            SENSOR_TYPES_DOORBELL[self._sensor_type][SENSOR_NAME],
        )

    async def async_update(self):
        """Get the latest state of the sensor."""
        async_state_provider = SENSOR_TYPES_DOORBELL[self._sensor_type][
            SENSOR_STATE_PROVIDER
        ]
        self._state = await async_state_provider(self._data, self._doorbell)
        self._available = self._state is not None

    @property
    def unique_id(self) -> str:
        """Get the unique id of the doorbell sensor."""
        return "{:s}_{:s}".format(
            self._doorbell.device_id,
            SENSOR_TYPES_DOORBELL[self._sensor_type][SENSOR_NAME].lower(),
        )
