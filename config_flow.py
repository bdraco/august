"""Config flow for August integration."""
import logging

from august.api import Api
from august.authenticator import AuthenticationState, Authenticator, ValidationResult
from requests import RequestException, Session
import voluptuous as vol

from homeassistant import config_entries, core, exceptions

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
        vol.Required(CONF_LOGIN_METHOD, default="phone"): vol.In(LOGIN_METHODS),
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Optional(CONF_TIMEOUT, default=DEFAULT_TIMEOUT): vol.Coerce(int),
    }
)


async def _async_close_http_session(hass, http_session):
    try:
        await hass.async_add_executor_job(http_session.close)
    except RequestException:
        pass


async def validate_input(hass: core.HomeAssistant, data):
    """Validate the user input allows us to connect.

    Data has the keys from DATA_SCHEMA with values provided by the user.
    """
    """Request configuration steps from the user."""
    api_http_session = Session()
    api = Api(timeout=data.get(CONF_TIMEOUT), http_session=api_http_session)

    username = data.get(CONF_USERNAME)
    access_token_cache_file = data.get(CONF_ACCESS_TOKEN_CACHE_FILE)
    if access_token_cache_file is None:
        access_token_cache_file = "." + username + AUGUST_CONFIG_FILE

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
        await _async_close_http_session(hass, api_http_session)
        raise CannotConnect

    state = authentication.state

    if state == AuthenticationState.BAD_PASSWORD:
        await _async_close_http_session(hass, api_http_session)
        raise InvalidAuth

    if state == AuthenticationState.REQUIRES_VALIDATION:
        code = data.get("code")
        result = None

        if code:
            result = await hass.async_add_executor_job(
                authenticator.validate_verification_code, code
            )
            _LOGGER.debug("Verification code validation: %s", result)
            if result != ValidationResult.VALIDATED:
                raise RequireValidation

            # we have to call authenticate again to write the token
            authentication = await hass.async_add_executor_job(
                authenticator.authenticate
            )
        else:
            _LOGGER.debug(
                "Requesting new verification code for %s via %s",
                data.get(CONF_USERNAME),
                data.get(CONF_LOGIN_METHOD),
            )
            await hass.async_add_executor_job(authenticator.send_verification_code)
            await _async_close_http_session(hass, api_http_session)
            raise RequireValidation

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
            try:
                info = await validate_input(self.hass, user_input)
                await self.async_set_unique_id(user_input[CONF_USERNAME])
                return self.async_create_entry(title=info["title"], data=info["data"])
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except RequireValidation:
                self.user_auth_details = user_input

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
            return await self.async_step_user({**self.user_auth_details, **user_input})

        return self.async_show_form(
            step_id="validation",
            data_schema=vol.Schema({vol.Required("code"): vol.All(str, vol.Strip)}),
            description_placeholders={
                CONF_USERNAME: self.user_auth_details.get(CONF_USERNAME),
                CONF_LOGIN_METHOD: self.user_auth_details.get(CONF_LOGIN_METHOD),
            },
        )

    async def async_step_import(self, user_input):
        """Handle import."""
        await self.async_set_unique_id(user_input[CONF_USERNAME])
        self._abort_if_unique_id_configured()

        return await self.async_step_user(user_input)


class RequireValidation(exceptions.HomeAssistantError):
    """Error to indicate we require validation (2fa)."""


class CannotConnect(exceptions.HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(exceptions.HomeAssistantError):
    """Error to indicate there is invalid auth."""
