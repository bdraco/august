"""Support for August devices."""
from datetime import timedelta
import logging

from august.api import Api
from august.authenticator import AuthenticationState, Authenticator, ValidationResult
from requests import RequestException, Session
import voluptuous as vol

from homeassistant.const import (
    CONF_PASSWORD,
    CONF_TIMEOUT,
    CONF_USERNAME,
    EVENT_HOMEASSISTANT_STOP,
)
from homeassistant.helpers import discovery
import homeassistant.helpers.config_validation as cv
from homeassistant.util import Throttle

_LOGGER = logging.getLogger(__name__)

_CONFIGURING = {}

DEFAULT_TIMEOUT = 10
ACTIVITY_FETCH_LIMIT = 10
ACTIVITY_INITIAL_FETCH_LIMIT = 20

CONF_LOGIN_METHOD = "login_method"
CONF_INSTALL_ID = "install_id"

NOTIFICATION_ID = "august_notification"
NOTIFICATION_TITLE = "August Setup"

AUGUST_CONFIG_FILE = ".august.conf"

DATA_AUGUST = "august"
DOMAIN = "august"
DEFAULT_ENTITY_NAMESPACE = "august"

# Limit battery and hardware updates to 1800 seconds
# in order to reduce the number of api requests and
# avoid hitting rate limits
MIN_TIME_BETWEEN_LOCK_DETAIL_UPDATES = timedelta(seconds=1800)

DEFAULT_SCAN_INTERVAL = timedelta(seconds=10)
MIN_TIME_BETWEEN_UPDATES = timedelta(seconds=10)

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

AUGUST_COMPONENTS = ["camera", "binary_sensor", "lock"]


def request_configuration(hass, config, api, authenticator):
    """Request configuration steps from the user."""
    configurator = hass.components.configurator

    def august_configuration_callback(data):
        """Run when the configuration callback is called."""

        result = authenticator.validate_verification_code(data.get("verification_code"))

        if result == ValidationResult.INVALID_VERIFICATION_CODE:
            configurator.notify_errors(
                _CONFIGURING[DOMAIN], "Invalid verification code"
            )
        elif result == ValidationResult.VALIDATED:
            setup_august(hass, config, api, authenticator)

    if DOMAIN not in _CONFIGURING:
        authenticator.send_verification_code()

    conf = config[DOMAIN]
    username = conf.get(CONF_USERNAME)
    login_method = conf.get(CONF_LOGIN_METHOD)

    _CONFIGURING[DOMAIN] = configurator.request_config(
        NOTIFICATION_TITLE,
        august_configuration_callback,
        description="Please check your {} ({}) and enter the verification "
        "code below".format(login_method, username),
        submit_caption="Verify",
        fields=[
            {"id": "verification_code", "name": "Verification code", "type": "string"}
        ],
    )


def setup_august(hass, config, api, authenticator):
    """Set up the August component."""

    authentication = None
    try:
        authentication = authenticator.authenticate()
    except RequestException as ex:
        _LOGGER.error("Unable to connect to August service: %s", str(ex))

        hass.components.persistent_notification.create(
            "Error: {}<br />"
            "You will need to restart hass after fixing."
            "".format(ex),
            title=NOTIFICATION_TITLE,
            notification_id=NOTIFICATION_ID,
        )

    state = authentication.state

    if state == AuthenticationState.AUTHENTICATED:
        if DOMAIN in _CONFIGURING:
            hass.components.configurator.request_done(_CONFIGURING.pop(DOMAIN))

        hass.data[DATA_AUGUST] = AugustData(hass, api, authentication.access_token)

        for component in AUGUST_COMPONENTS:
            discovery.load_platform(hass, component, DOMAIN, {}, config)

        return True
    if state == AuthenticationState.BAD_PASSWORD:
        _LOGGER.error("Invalid password provided")
        return False
    if state == AuthenticationState.REQUIRES_VALIDATION:
        request_configuration(hass, config, api, authenticator)
        return True

    return False


def setup(hass, config):
    """Set up the August component."""

    conf = config[DOMAIN]
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
        access_token_cache_file=hass.config.path(AUGUST_CONFIG_FILE),
    )

    def close_http_session(event):
        """Close API sessions used to connect to August."""
        _LOGGER.debug("Closing August HTTP sessions")
        if api_http_session:
            try:
                api_http_session.close()
            except RequestException:
                pass

        _LOGGER.debug("August HTTP session closed.")

    hass.bus.listen_once(EVENT_HOMEASSISTANT_STOP, close_http_session)
    _LOGGER.debug("Registered for Home Assistant stop event")

    return setup_august(hass, config, api, authenticator)


class AugustData:
    """August data object."""

    def __init__(self, hass, api, access_token):
        """Init August data object."""
        self._hass = hass
        self._api = api
        self._access_token = access_token
        self._doorbells = self._api.get_doorbells(self._access_token) or []
        self._locks = self._api.get_operable_locks(self._access_token) or []
        self._house_ids = set()
        for device in self._doorbells + self._locks:
            self._house_ids.add(device.house_id)

        self._doorbell_detail_by_id = {}
        self._lock_status_by_id = {}
        self._lock_detail_by_id = {}
        self._door_state_by_id = {}
        self._activities_by_id = {}

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

    def get_device_activities(self, device_id, *activity_types):
        """Return a list of activities."""
        _LOGGER.debug("Getting device activities")
        self._update_device_activities()

        activities = self._activities_by_id.get(device_id, [])
        if activity_types:
            return [a for a in activities if a.activity_type in activity_types]
        return activities

    def get_latest_device_activity(self, device_id, *activity_types):
        """Return latest activity."""
        activities = self.get_device_activities(device_id, *activity_types)
        return next(iter(activities or []), None)

    @Throttle(MIN_TIME_BETWEEN_UPDATES)
    def _update_device_activities(self, limit=ACTIVITY_FETCH_LIMIT):
        """Update data object with latest from August API."""
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

    def get_doorbell_detail(self, doorbell_id):
        """Return doorbell detail."""
        self._update_doorbells()
        return self._doorbell_detail_by_id.get(doorbell_id)

    @Throttle(MIN_TIME_BETWEEN_UPDATES)
    def _update_doorbells(self):
        detail_by_id = {}

        _LOGGER.debug("Start retrieving doorbell details")
        for doorbell in self._doorbells:
            _LOGGER.debug("Updating doorbell status for %s", doorbell.device_name)
            try:
                detail_by_id[doorbell.device_id] = self._api.get_doorbell_detail(
                    self._access_token, doorbell.device_id
                )
            except RequestException as ex:
                _LOGGER.error(
                    "Request error trying to retrieve doorbell status for %s. %s",
                    doorbell.device_name,
                    ex,
                )
                detail_by_id[doorbell.device_id] = None
            except Exception:
                detail_by_id[doorbell.device_id] = None
                raise

        _LOGGER.debug("Completed retrieving doorbell details")
        self._doorbell_detail_by_id = detail_by_id

    def get_lock_status(self, lock_id):
        """Return status if the door is locked or unlocked.

        This is status for the lock itself.
        """
        self._update_locks()
        return self._lock_status_by_id.get(lock_id)

    def get_lock_detail(self, lock_id):
        """Return lock detail."""
        self._update_locks()
        return self._lock_detail_by_id.get(lock_id)

    def get_door_state(self, lock_id):
        """Return status if the door is open or closed.

        This is the status from the door sensor.
        """
        self._update_locks_status()
        return self._door_state_by_id.get(lock_id)

    def _update_locks(self):
        self._update_locks_status()
        self._update_locks_detail()

    @Throttle(MIN_TIME_BETWEEN_UPDATES)
    def _update_locks_status(self):
        status_by_id = {}
        state_by_id = {}

        _LOGGER.debug("Start retrieving lock and door status")
        for lock in self._locks:
            _LOGGER.debug("Updating lock and door status for %s", lock.device_name)
            try:
                (
                    status_by_id[lock.device_id],
                    state_by_id[lock.device_id],
                ) = self._api.get_lock_status(
                    self._access_token, lock.device_id, door_status=True
                )
            except RequestException as ex:
                _LOGGER.error(
                    "Request error trying to retrieve lock and door status for %s. %s",
                    lock.device_name,
                    ex,
                )
                status_by_id[lock.device_id] = None
                state_by_id[lock.device_id] = None
            except Exception:
                status_by_id[lock.device_id] = None
                state_by_id[lock.device_id] = None
                raise

        _LOGGER.debug("Completed retrieving lock and door status")
        self._lock_status_by_id = status_by_id
        self._door_state_by_id = state_by_id

    @Throttle(MIN_TIME_BETWEEN_LOCK_DETAIL_UPDATES)
    def _update_locks_detail(self):
        detail_by_id = {}

        _LOGGER.debug("Start retrieving locks detail")
        for lock in self._locks:
            try:
                detail_by_id[lock.device_id] = self._api.get_lock_detail(
                    self._access_token, lock.device_id
                )
            except RequestException as ex:
                _LOGGER.error(
                    "Request error trying to retrieve door details for %s. %s",
                    lock.device_name,
                    ex,
                )
                detail_by_id[lock.device_id] = None
            except Exception:
                detail_by_id[lock.device_id] = None
                raise

        _LOGGER.debug("Completed retrieving locks detail")
        self._lock_detail_by_id = detail_by_id

    def lock(self, device_id):
        """Lock the device."""
        return self._api.lock(self._access_token, device_id)

    def unlock(self, device_id):
        """Unlock the device."""
        return self._api.unlock(self._access_token, device_id)
