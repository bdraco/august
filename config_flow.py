"""Config flow for August integration."""
import logging

# , ValidationResult
from august.api import Api
from august.authenticator import AuthenticationState, Authenticator
from requests import RequestException, Session
import voluptuous as vol

from homeassistant import config_entries, core, exceptions
import homeassistant.helpers.config_validation as cv

from . import (
    AUGUST_CONFIG_FILE,
    CONF_ACCESS_TOKEN_CACHE_FILE,
    CONF_INSTALL_ID,
    CONF_LOGIN_METHOD,
    CONF_PASSWORD,
    CONF_TIMEOUT,
    CONF_USERNAME,
    DEFAULT_TIMEOUT,
)
from . import DOMAIN  # pylint:disable=unused-import

_LOGGER = logging.getLogger(__name__)

# TODO adjust the data schema to the data that you need
DATA_SCHEMA = vol.Schema({"host": str, "username": str, "password": str})


LOGIN_METHODS = ["phone", "email"]
DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_LOGIN_METHOD): vol.In(LOGIN_METHODS),
        vol.Required(CONF_USERNAME): cv.string,
        vol.Required(CONF_PASSWORD): cv.string,
        vol.Optional(CONF_INSTALL_ID): cv.string,
        vol.Optional(CONF_TIMEOUT, default=DEFAULT_TIMEOUT): cv.positive_int,
    }
)


async def validate_input(hass: core.HomeAssistant, data):
    """Validate the user input allows us to connect.

    Data has the keys from DATA_SCHEMA with values provided by the user.
    """
    """Request configuration steps from the user."""

    # def august_configuration_callback(data):
    # """Run when the configuration callback is called."""

    # result = authenticator.validate_verification_code(data.get("verification_code"))

    # if result == ValidationResult.INVALID_VERIFICATION_CODE:
    # configurator.notify_errors(
    # _CONFIGURING[DOMAIN], "Invalid verification code"
    # )
    # elif result == ValidationResult.VALIDATED:
    # setup_august(hass, config, api, authenticator, token_refresh_lock)

    api_http_session = Session()
    api = Api(timeout=data.get(CONF_TIMEOUT), http_session=api_http_session)

    username = data.get(CONF_USERNAME)
    access_token_cache_file = data.get(CONF_ACCESS_TOKEN_CACHE_FILE)
    if access_token_cache_file is None:
        access_token_cache_file = username + "." + AUGUST_CONFIG_FILE

    authenticator = Authenticator(
        api,
        data.get(CONF_LOGIN_METHOD),
        username,
        data.get(CONF_PASSWORD),
        install_id=data.get(CONF_INSTALL_ID),
        access_token_cache_file=hass.config.path(access_token_cache_file),
    )

    authentication = None
    try:
        authentication = await hass.async_add_executor_job(authenticator.authenticate)
    except RequestException as ex:
        _LOGGER.error("Unable to connect to August service: %s", str(ex))
        raise CannotConnect

    state = authentication.state

    if state == AuthenticationState.BAD_PASSWORD:
        authenticator.send_verification_code()
        raise InvalidAuth

    # if state == AuthenticationState.AUTHENTICATED:
    #    return True
    #
    if state == AuthenticationState.REQUIRES_VALIDATION:
        raise RequireValidation
    #    # ok ?

    # if DOMAIN not in _CONFIGURING:

    # _CONFIGURING[DOMAIN] = configurator.request_config(
    #    NOTIFICATION_TITLE,
    #    august_configuration_callback,
    #    description="Please check your {} ({}) and enter the verification "
    #    "code below".format(login_method, username),
    #    submit_caption="Verify",
    #    fields=[
    #        {"id": "verification_code", "name": "Verification code", "type": "string"}
    #    ],
    # )

    # Return info that you want to store in the config entry.
    return {
        "title": username,
        "data": {
            CONF_LOGIN_METHOD: data.get(CONF_LOGIN_METHOD),
            CONF_USERNAME: data.get(CONF_USERNAME),
            CONF_PASSWORD: data.get(CONF_PASSWORD),
            CONF_INSTALL_ID: data.get(CONF_INSTALL_ID),
            CONF_TIMEOUT: data.get(CONF_TIMEOUT),
            CONF_ACCESS_TOKEN_CACHE_FILE: access_token_cache_file,
        },
    }


class AugustConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for August."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_CLOUD_POLL

    def _username_in_configuration_exists(self, user_input) -> bool:
        """Return True if username exists in configuration."""
        username = user_input[CONF_USERNAME]
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            if entry.data[CONF_USERNAME] == username:
                return True
        return False

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}
        if user_input is not None:
            if self._username_in_configuration_exists(user_input):
                return self.async_abort(reason="already_configured")
            try:
                info = await validate_input(self.hass, user_input)
                await self.async_set_unique_id(info["data"][CONF_USERNAME])
                return self.async_create_entry(title=info["title"], data=info["data"])
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except RequireValidation:
                self.user_config = user_input

                return await self.async_step_validation()
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user", data_schema=DATA_SCHEMA, errors=errors
        )

    async def async_step_validation(self, user_input=None):
        """Handle validation (2fa) step."""
        if user_input:
            return await self.async_step_user({**self.user_pass, **user_input})

        return self.async_show_form(
            step_id="validation",
            data_schema=vol.Schema({vol.Required("code"): vol.All(str, vol.Strip)}),
            description_placeholders={
                CONF_USERNAME: self.user_config.get(CONF_USERNAME),
                CONF_LOGIN_METHOD: self.user_config.get(CONF_LOGIN_METHOD),
            },
        )

    async def async_step_import(self, user_input):
        """Handle import."""

        if self._username_in_configuration_exists(user_input):
            return self.async_abort(reason="already_configured")
        return await self.async_step_user(user_input)


class RequireValidation(exceptions.HomeAssistantError):
    """Error to indicate we require validation (2fa)."""


class CannotConnect(exceptions.HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(exceptions.HomeAssistantError):
    """Error to indicate there is invalid auth."""
