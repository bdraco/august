"""Support for August camera."""
from datetime import timedelta

from august.activity import ActivityType
from august.util import update_doorbell_image_from_activity

from homeassistant.components.camera import Camera

from . import DEFAULT_TIMEOUT, DOMAIN

SCAN_INTERVAL = timedelta(seconds=5)


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up August cameras."""
    data = hass.data[DOMAIN][config_entry.entry_id]
    devices = []

    for doorbell in data.doorbells:
        devices.append(AugustCamera(data, doorbell, DEFAULT_TIMEOUT))

    async_add_entities(devices, True)


class AugustCamera(Camera):
    """An implementation of a August security camera."""

    def __init__(self, data, doorbell, timeout):
        """Initialize a August security camera."""
        super().__init__()
        self._data = data
        self._doorbell = doorbell
        self._doorbell_detail = None
        self._timeout = timeout
        self._image_url = None
        self._image_content = None

    @property
    def name(self):
        """Return the name of this device."""
        return self._doorbell.device_name

    @property
    def is_recording(self):
        """Return true if the device is recording."""
        return self._doorbell.has_subscription

    @property
    def motion_detection_enabled(self):
        """Return the camera motion detection status."""
        return True

    @property
    def brand(self):
        """Return the camera brand."""
        return "August"

    @property
    def model(self):
        """Return the camera model."""
        return "Doorbell"

    async def async_camera_image(self):
        """Return bytes of camera image."""
        self._doorbell_detail = await self._data.async_get_doorbell_detail(
            self._doorbell.device_id
        )
        doorbell_activity = await self._data.async_get_latest_device_activity(
            self._doorbell.device_id, ActivityType.DOORBELL_MOTION
        )

        if doorbell_activity is not None:
            update_doorbell_image_from_activity(
                self._doorbell_detail, doorbell_activity
            )

        if self._image_url is not self._doorbell_detail.image_url:
            self._image_url = self._doorbell_detail.image_url
            self._image_content = await self.hass.async_add_executor_job(
                self._camera_image
            )

        return self._image_content

    def _camera_image(self):
        """Return bytes of camera image."""
        return self._doorbell_detail.get_doorbell_image(timeout=self._timeout)

    @property
    def unique_id(self) -> str:
        """Get the unique id of the camera."""
        return f"{self._doorbell.device_id:s}_camera"
