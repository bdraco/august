"""Support for August devices."""
import asyncio
from datetime import timedelta
from functools import partial
import logging

# , ValidationResult
from august.api import Api, AugustApiHTTPError
from august.authenticator import AuthenticationState, Authenticator
from august.doorbell import Doorbell
from august.lock import Lock
from requests import RequestException, Session
import voluptuous as vol

from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_TIMEOUT, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
import homeassistant.helpers.config_validation as cv
from homeassistant.util import Throttle

_LOGGER = logging.getLogger(__name__)

DEFAULT_NAME = "August"

DEFAULT_TIMEOUT = 10
ACTIVITY_FETCH_LIMIT = 10

CONF_ACCESS_TOKEN_CACHE_FILE = "access_token_cache_file"
CONF_LOGIN_METHOD = "login_method"
CONF_INSTALL_ID = "install_id"

NOTIFICATION_ID = "august_notification"
NOTIFICATION_TITLE = "August Setup"

AUGUST_CONFIG_FILE = ".august.conf"

DOMAIN = "august"

_CONFIGURING = {}


# Limit battery, online, and hardware updates to 1800 seconds
# in order to reduce the number of api requests and
# avoid hitting rate limits
MIN_TIME_BETWEEN_DETAIL_UPDATES = timedelta(seconds=1800)

# Activity needs to be checked more frequently as the
# doorbell motion and rings are included here
MIN_TIME_BETWEEN_ACTIVITY_UPDATES = timedelta(seconds=10)

DEFAULT_SCAN_INTERVAL = timedelta(seconds=10)


LOGIN_METHODS = ["phone", "email"]

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required(CONF_LOGIN_METHOD): vol.In(LOGIN_METHODS),
                vol.Required(CONF_USERNAME): cv.string,
                vol.Required(CONF_PASSWORD): cv.string,
                vol.Optional(CONF_INSTALL_ID): cv.string,
                vol.Optional(CONF_TIMEOUT, default=DEFAULT_TIMEOUT): cv.positive_int,
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)

PLATFORMS = ["camera", "binary_sensor", "sensor", "lock"]

def setup_august(
    hass, config_entry, api, authenticator, token_refresh_lock, api_http_session
):
    """Set up the August component."""
    entry_id = config_entry.entry_id
    entry_data = config_entry.data
    authentication = None
    try:
        authentication = authenticator.authenticate()
    except RequestException as ex:
        _LOGGER.error("Unable to connect to August service: %s", str(ex))

    state = authentication.state
    login_method = entry_data.get(CONF_LOGIN_METHOD)
    username = entry_data.get(CONF_USERNAME)

    if state == AuthenticationState.AUTHENTICATED:
        hass.data[DOMAIN][entry_id] = AugustData(
            hass,
            api,
            authentication,
            authenticator,
            token_refresh_lock,
            api_http_session,
        )
        return True

    if state == AuthenticationState.BAD_PASSWORD:
        _LOGGER.error("Password is no longer valid. Please set up August again")
        return False
    if state == AuthenticationState.REQUIRES_VALIDATION:
        def august_configuration_validation_callback(data):
	    result = authenticator.validate_verification_code(data.get("verification_code"))

	    if result == ValidationResult.INVALID_VERIFICATION_CODE:
		configurator.notify_errors(
		    _CONFIGURING[entry_id], "Invalid verification code"
		)
	    elif result == ValidationResult.VALIDATED:
		setup_august(hass, config_entry, api, authenticator, token_refresh_lock, api_http_session)

	if entry_id not in _CONFIGURING:
	    authenticator.send_verification_code()

        _LOGGER.error("Access token is no longer valid.")
        _CONFIGURING[entry_id] = configurator.request_config(
	    NOTIFICATION_TITLE,
	    august_configuration_validation_callback,
	    description="Please check your {} ({}) and enter the verification "
	    "code below".format(login_method, username),
	    submit_caption="Verify",
	    fields=[
		{"id": "verification_code", "name": "Verification code", "type": "string"}
	    ],
	)
        return False

    return False


async def async_setup(hass: HomeAssistant, config: dict):
    """Set up the August component."""

    conf = config.get(DOMAIN)

    if not conf:
        return True

    hass.data.setdefault(DOMAIN, {})

    hass.async_create_task(
        hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_IMPORT},
            data={
                CONF_LOGIN_METHOD: conf.get(CONF_LOGIN_METHOD),
                CONF_USERNAME: conf.get(CONF_USERNAME),
                CONF_PASSWORD: conf.get(CONF_PASSWORD),
                CONF_INSTALL_ID: conf.get(CONF_INSTALL_ID),
                CONF_ACCESS_TOKEN_CACHE_FILE: AUGUST_CONFIG_FILE,
            },
        )
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Set up August from a config entry."""

    hass.data.setdefault(DOMAIN, {})
    conf = entry.data

    api_http_session = None
    try:
        api_http_session = Session()
    except RequestException as ex:
        _LOGGER.warning("Creating HTTP session failed with: %s", str(ex))

    api = Api(timeout=conf.get(CONF_TIMEOUT), http_session=api_http_session)

    authenticator = Authenticator(
        api,
        conf.get(CONF_LOGIN_METHOD),
        conf.get(CONF_USERNAME),
        conf.get(CONF_PASSWORD),
        install_id=conf.get(CONF_INSTALL_ID),
        access_token_cache_file=hass.config.path(
            conf.get(CONF_ACCESS_TOKEN_CACHE_FILE)
        ),
    )

    token_refresh_lock = asyncio.Lock()

    setup_ok = await hass.async_add_executor_job(
        setup_august,
        hass,
        entry,
        api,
        authenticator,
        token_refresh_lock,
        api_http_session,
    )

    if not setup_ok:
        return False

    for component in PLATFORMS:
        hass.async_create_task(
            hass.config_entries.async_forward_entry_setup(entry, component)
        )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Unload a config entry."""
    unload_ok = all(
        await asyncio.gather(
            *[
                hass.config_entries.async_forward_entry_unload(entry, component)
                for component in PLATFORMS
            ]
        )
    )

    #FIXME: close_http_session doesn't seem to happen now

    if unload_ok:
        hass.data[DOMAIN][entry.entry_id].close_http_session()
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


class AugustData:
    """August data object."""

    def __init__(
        self,
        hass,
        api,
        authentication,
        authenticator,
        token_refresh_lock,
        api_http_session,
    ):
        """Init August data object."""
        self._hass = hass
        self._api = api
        self._api_http_session = api_http_session
        self._authenticator = authenticator
        self._access_token = authentication.access_token
        self._access_token_expires = authentication.access_token_expires

        self._token_refresh_lock = token_refresh_lock
        self._doorbells = self._api.get_doorbells(self._access_token) or []
        self._locks = self._api.get_operable_locks(self._access_token) or []
        self._house_ids = set()
        for device in self._doorbells + self._locks:
            self._house_ids.add(device.house_id)

        self._doorbell_detail_by_id = {}
        self._lock_detail_by_id = {}
        self._activities_by_id = {}

        # We check the locks right away so we can
        # remove inoperative ones
        self._update_locks_detail()
        self._filter_inoperative_locks()

        self._update_doorbells_detail()

    def close_http_session(self):
        """Close API sessions used to connect to August."""
        _LOGGER.debug("Closing August HTTP sessions")
        if self._api_http_session:
            try:
                self._api_http_session.close()
            except RequestException:
                pass

        _LOGGER.debug("August HTTP session closed.")

    @property
    def house_ids(self):
        """Return a list of house_ids."""
        return self._house_ids

    @property
    def doorbells(self):
        """Return a list of doorbells."""
        return self._doorbells

    @property
    def locks(self):
        """Return a list of locks."""
        return self._locks

    async def _async_refresh_access_token_if_needed(self):
        """Refresh the august access token if needed."""
        if self._authenticator.should_refresh():
            async with self._token_refresh_lock:
                await self._hass.async_add_executor_job(self._refresh_access_token)

    def _refresh_access_token(self):
        refreshed_authentication = self._authenticator.refresh_access_token(force=False)
        _LOGGER.info(
            "Refreshed august access token. The old token expired at %s, and the new token expires at %s",
            self._access_token_expires,
            refreshed_authentication.access_token_expires,
        )
        self._access_token = refreshed_authentication.access_token
        self._access_token_expires = refreshed_authentication.access_token_expires

    async def async_get_device_activities(self, device_id, *activity_types):
        """Return a list of activities."""
        _LOGGER.debug("Getting device activities for %s", device_id)
        await self._async_update_device_activities()

        activities = self._activities_by_id.get(device_id, [])
        if activity_types:
            return [a for a in activities if a.activity_type in activity_types]
        return activities

    async def async_get_latest_device_activity(self, device_id, *activity_types):
        """Return latest activity."""
        activities = await self.async_get_device_activities(device_id, *activity_types)
        return next(iter(activities or []), None)

    @Throttle(MIN_TIME_BETWEEN_ACTIVITY_UPDATES)
    async def _async_update_device_activities(self, limit=ACTIVITY_FETCH_LIMIT):
        """Update data object with latest from August API."""

        # This is the only place we refresh the api token
        await self._async_refresh_access_token_if_needed()
        return await self._hass.async_add_executor_job(
            partial(self._update_device_activities, limit=ACTIVITY_FETCH_LIMIT)
        )

    def _update_device_activities(self, limit=ACTIVITY_FETCH_LIMIT):
        _LOGGER.debug("Start retrieving device activities")
        for house_id in self.house_ids:
            _LOGGER.debug("Updating device activity for house id %s", house_id)

            activities = self._api.get_house_activities(
                self._access_token, house_id, limit=limit
            )

            device_ids = {a.device_id for a in activities}
            for device_id in device_ids:
                self._activities_by_id[device_id] = [
                    a for a in activities if a.device_id == device_id
                ]

        _LOGGER.debug("Completed retrieving device activities")

    async def async_get_doorbell_detail(self, device_id):
        """Return doorbell detail."""
        await self._async_update_doorbells_detail()
        return self._doorbell_detail_by_id.get(device_id)

    @Throttle(MIN_TIME_BETWEEN_DETAIL_UPDATES)
    async def _async_update_doorbells_detail(self):
        await self._hass.async_add_executor_job(self._update_doorbells_detail)

    def _update_doorbells_detail(self):
        self._doorbell_detail_by_id = self._update_device_detail(
            "doorbell", self._doorbells, self._api.get_doorbell_detail
        )

    def lock_has_doorsense(self, device_id):
        """Determine if a lock has doorsense installed and can tell when the door is open or closed."""
        # We do not update here since this is not expected
        # to change until restart
        if self._lock_detail_by_id[device_id] is None:
            return False
        return self._lock_detail_by_id[device_id].doorsense

    async def async_get_lock_detail(self, device_id):
        """Return lock detail."""
        await self._async_update_locks_detail()
        return self._lock_detail_by_id[device_id]

    def get_lock_name(self, device_id):
        """Return lock name as August has it stored."""
        for lock in self._locks:
            if lock.device_id == device_id:
                return lock.device_name

    @Throttle(MIN_TIME_BETWEEN_DETAIL_UPDATES)
    async def _async_update_locks_detail(self):
        await self._hass.async_add_executor_job(self._update_locks_detail)

    def _update_locks_detail(self):
        self._lock_detail_by_id = self._update_device_detail(
            "lock", self._locks, self._api.get_lock_detail
        )

    def _update_device_detail(self, device_type, devices, api_call):
        detail_by_id = {}

        _LOGGER.debug("Start retrieving %s detail", device_type)
        for device in devices:
            device_id = device.device_id
            try:
                detail_by_id[device_id] = api_call(self._access_token, device_id)
            except RequestException as ex:
                _LOGGER.error(
                    "Request error trying to retrieve %s details for %s. %s",
                    device_type,
                    device.device_name,
                    ex,
                )
                detail_by_id[device_id] = None
            except Exception:
                detail_by_id[device_id] = None
                raise

        _LOGGER.debug("Completed retrieving %s detail", device_type)
        return detail_by_id

    def lock(self, device_id):
        """Lock the device."""
        return _call_api_operation_that_requires_bridge(
            self.get_lock_name(device_id),
            "lock",
            self._api.lock_return_activities,
            self._access_token,
            device_id,
        )

    def unlock(self, device_id):
        """Unlock the device."""
        return _call_api_operation_that_requires_bridge(
            self.get_lock_name(device_id),
            "unlock",
            self._api.unlock_return_activities,
            self._access_token,
            device_id,
        )

    def _filter_inoperative_locks(self):
        # Remove non-operative locks as there must
        # be a bridge (August Connect) for them to
        # be usable
        operative_locks = []
        for lock in self._locks:
            lock_detail = self._lock_detail_by_id.get(lock.device_id)
            if lock_detail is None:
                _LOGGER.info(
                    "The lock %s could not be setup because the system could not fetch details about the lock.",
                    lock.device_name,
                )
            elif lock_detail.bridge is None:
                _LOGGER.info(
                    "The lock %s could not be setup because it does not have a bridge (Connect).",
                    lock.device_name,
                )
            elif not lock_detail.bridge.operative:
                _LOGGER.info(
                    "The lock %s could not be setup because the bridge (Connect) is not operative.",
                    lock.device_name,
                )
            else:
                operative_locks.append(lock)

        self._locks = operative_locks


def _call_api_operation_that_requires_bridge(
    device_name, operation_name, func, *args, **kwargs
):
    """Call an API that requires the bridge to be online."""
    ret = None
    try:
        ret = func(*args, **kwargs)
    except AugustApiHTTPError as err:
        raise HomeAssistantError(device_name + ": " + str(err))

    return ret


def find_linked_doorsense_unique_id(device_id):
    """Find the unique_id assigned to doorsense sensor from the august device_id."""
    return f"{device_id}_open"


async def async_detail_provider(data, device):
    """Return the py-august detail for a device."""
    if isinstance(device, Lock):
        return await data.async_get_lock_detail(device.device_id)
    if isinstance(device, Doorbell):
        return await data.async_get_doorbell_detail(device.device_id)
    raise ValueError
