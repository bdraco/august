"""Support for August lock."""
from datetime import timedelta
import logging

from august.activity import ActivityType, DoorOperationActivity
from august.lock import LockStatus
from august.util import update_lock_detail_from_activity

from homeassistant.components.lock import LockDevice
from homeassistant.const import ATTR_BATTERY_LEVEL

from . import DEFAULT_NAME, DOMAIN, find_linked_doorsense_unique_id

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(seconds=5)


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up August locks."""
    data = hass.data[DOMAIN][config_entry.entry_id]
    devices = []

    for lock in data.locks:
        _LOGGER.debug("Adding lock for %s", lock.device_name)
        devices.append(AugustLock(data, lock))

    async_add_entities(devices, True)


class AugustLock(LockDevice):
    """Representation of an August lock."""

    def __init__(self, data, lock):
        """Initialize the lock."""
        self._data = data
        self._lock = lock
        self._lock_status = None
        self._lock_detail = None
        self._changed_by = None
        self._available = False
        self._firmware_version = None
        self._model = None

    async def async_lock(self, **kwargs):
        """Lock the device."""
        await self._call_lock_operation(self._data.lock)

    async def async_unlock(self, **kwargs):
        """Unlock the device."""
        await self._call_lock_operation(self._data.unlock)

    async def _linked_doorsense_entity_id(self):
        if self._data.lock_has_doorsense(self._lock.device_id):
            entity_registry = (
                await self.hass.helpers.entity_registry.async_get_registry()
            )
            return entity_registry.async_get_entity_id(
                "binary_sensor",
                DOMAIN,
                find_linked_doorsense_unique_id(self._lock.device_id),
            )
        return None

    async def _call_lock_operation(self, lock_operation):
        activities = await self.hass.async_add_executor_job(
            lock_operation, self._lock.device_id
        )
        for lock_activity in activities:
            update_lock_detail_from_activity(self._lock_detail, lock_activity)
            if isinstance(lock_activity, DoorOperationActivity):
                linked_doorsense_entity_id = await self._linked_doorsense_entity_id()
                if linked_doorsense_entity_id is not None:
                    await self.hass.helpers.entity_component.async_update_entity(
                        linked_doorsense_entity_id
                    )

        if self._update_lock_status_from_detail():
            self.schedule_update_ha_state()

    def _update_lock_status_from_detail(self):
        detail = self._lock_detail
        lock_status = None
        self._available = False

        if detail is not None:
            lock_status = detail.lock_status
            self._available = detail.bridge_is_online

        if self._lock_status != lock_status:
            self._lock_status = lock_status
            return True
        return False

    async def async_update(self):
        """Get the latest state of the sensor and update activity."""
        self._lock_detail = await self._data.async_get_lock_detail(self._lock.device_id)
        lock_activity = await self._data.async_get_latest_device_activity(
            self._lock.device_id, ActivityType.LOCK_OPERATION
        )

        if lock_activity is not None:
            self._changed_by = lock_activity.operated_by
            if self._lock_detail is not None:
                update_lock_detail_from_activity(self._lock_detail, lock_activity)

        if self._lock_detail is not None:
            self._firmware_version = self._lock_detail.firmware_version
            self._model = self._lock_detail.model

        self._update_lock_status_from_detail()

    @property
    def name(self):
        """Return the name of this device."""
        return self._lock.device_name

    @property
    def available(self):
        """Return the availability of this sensor."""
        return self._available

    @property
    def is_locked(self):
        """Return true if device is on."""
        if self._lock_status is None or self._lock_status is LockStatus.UNKNOWN:
            return None
        return self._lock_status is LockStatus.LOCKED

    @property
    def changed_by(self):
        """Last change triggered by."""
        return self._changed_by

    @property
    def device_state_attributes(self):
        """Return the device specific state attributes."""
        if self._lock_detail is None:
            return None

        attributes = {ATTR_BATTERY_LEVEL: self._lock_detail.battery_level}

        if self._lock_detail.keypad is not None:
            attributes["keypad_battery_level"] = self._lock_detail.keypad.battery_level

        return attributes

    @property
    def device_info(self):
        """Return the device_info of the device."""
        return {
            "identifiers": {(DOMAIN, self._lock.device_id)},
            "name": self._lock.device_name,
            "manufacturer": DEFAULT_NAME,
            "model": self._model,
            "sw_version": self._firmware_version,
        }

    @property
    def unique_id(self) -> str:
        """Get the unique id of the lock."""
        return f"{self._lock.device_id:s}_lock"
