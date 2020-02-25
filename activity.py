"""Consume the august activity stream."""
import logging

from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval

from .const import AUGUST_DEVICE_UPDATE, MIN_TIME_BETWEEN_ACTIVITY_UPDATES

_LOGGER = logging.getLogger(__name__)


class ActivityStream:
    """August activity stream handler."""

    DEFAULT_ACTIVITY_FETCH_LIMIT = 10

    def __init__(self, hass, api, august_gateway, house_ids):
        """Init August activity stream object."""
        self._hass = hass
        self._august_gateway = august_gateway
        self._api = api
        self._house_ids = house_ids
        self._latest_activities_by_id_type = {}
        self._abort_async_track_time_interval = async_track_time_interval(
            hass, self._async_update, MIN_TIME_BETWEEN_ACTIVITY_UPDATES,
        )

    def stop(self):
        """Stop fetching updates from the activity stream."""
        self._abort_async_track_time_interval()

    @callback
    def get_latest_device_activity(self, device_id, activity_types):
        """Return latest activity of each requested type."""
        activities = []
        for activity_type in activity_types:
            if activity_type in self._latest_activities_by_id_type[device_id]:
                activities.append(
                    self._latest_activities_by_id_type[device_id][activity_type]
                )
        return activities

    async def _async_update(self, limit=DEFAULT_ACTIVITY_FETCH_LIMIT):
        """Update the activity stream from August."""

        # This is the only place we refresh the api token
        await self._august_gateway.async_refresh_access_token_if_needed()

        return await self._hass.async_add_executor_job(
            self._update_device_activities, limit
        )

    def _update_device_activities(self, limit):
        _LOGGER.debug("Start retrieving device activities")
        for house_id in self._house_ids:
            _LOGGER.debug("Updating device activity for house id %s", house_id)
            activities = self._api.get_house_activities(
                self._august_gateway.access_token, house_id, limit=limit
            )
            _LOGGER.debug(
                "Completed retrieving device activities for house id %s", house_id
            )

            updated_device_ids = self._process_newer_device_activities(activities)

            if len(updated_device_ids):
                self._signal_device_updates(updated_device_ids)

    def _signal_device_updates(self, updated_device_ids):
        for device_id in updated_device_ids:
            async_dispatcher_send(self._hass, f"{AUGUST_DEVICE_UPDATE}-{device_id}")

    def _process_newer_device_activities(self, activities):
        updated_device_ids = set()
        for activity in activities:
            self._latest_activities_by_id_type.setdefault(activity.device_id, {})

            lastest_activity = self._latest_activities_by_id_type[
                activity.device_id
            ].get(activity.activity_type)

            # Ignore activities that are older than the latest one
            if (
                lastest_activity
                and lastest_activity.activity_start_time > activity.activity_start_time
            ):
                continue

            self._latest_activities_by_id_type[activity.device_id][
                activity.activity_type
            ] = activity

            updated_device_ids.add(activity.device_id)
