"""Config flow for the HA inverter proxy integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_CONTROL_TOKEN,
    CONF_CONTROL_URL,
    CONF_CORE_PORT,
    CONF_FACADE_PORT,
    CONF_GOODWE_ENTRY_ID,
    CONF_INVERTER_HOST,
    CONF_INVERTER_PORT,
    CONF_ORIGINAL_HOST,
    CONF_ORIGINAL_PORT,
    CONF_PROXY_HOST,
    DEFAULT_CONTROL_PORT,
    DEFAULT_CORE_PORT,
    DEFAULT_FACADE_PORT,
    DOMAIN,
)
from .probe import (
    configure_proxy,
    configured_device_host,
    goodwe_udp_alive,
    ping_proxy,
    proxy_status,
)


class InverterProxyConfigFlow(ConfigFlow, domain=DOMAIN):
    """Configure the proxy IP and redirect the existing GoodWe entry to it."""

    VERSION = 1

    def __init__(self) -> None:
        self._proxy_host = ""
        self._core_port = DEFAULT_CORE_PORT
        self._facade_port = DEFAULT_FACADE_PORT
        self._control_url = ""
        self._control_token = ""
        self._status: dict[str, Any] | None = None
        self._goodwe_entries: list[ConfigEntry] = []

    def _user_schema(
        self, values: dict[str, Any] | None = None
    ) -> vol.Schema:
        """Build the form, retaining values when redisplaying after an error."""
        values = values or {}
        return vol.Schema(
            {
                vol.Required(
                    CONF_PROXY_HOST,
                    default=values.get(CONF_PROXY_HOST, ""),
                ): str,
                vol.Optional(
                    CONF_CORE_PORT,
                    default=values.get(CONF_CORE_PORT, DEFAULT_CORE_PORT),
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
                vol.Optional(
                    CONF_FACADE_PORT,
                    default=values.get(CONF_FACADE_PORT, DEFAULT_FACADE_PORT),
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
                vol.Optional(
                    CONF_INVERTER_HOST, default=values.get(CONF_INVERTER_HOST, "")
                ): str,
                vol.Optional(
                    CONF_INVERTER_PORT,
                    default=values.get(CONF_INVERTER_PORT, DEFAULT_FACADE_PORT),
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
                vol.Optional(
                    CONF_CONTROL_URL,
                    default=values.get(CONF_CONTROL_URL, ""),
                ): str,
                vol.Optional(
                    CONF_CONTROL_TOKEN,
                    default=values.get(CONF_CONTROL_TOKEN, ""),
                ): selector.TextSelector(
                    selector.TextSelectorConfig(
                        type=selector.TextSelectorType.PASSWORD
                    )
                ),
            }
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the proxy address, verify it, then redirect GoodWe."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._proxy_host = str(user_input[CONF_PROXY_HOST]).strip()
            self._core_port = int(user_input.get(CONF_CORE_PORT, DEFAULT_CORE_PORT))
            self._facade_port = int(
                user_input.get(CONF_FACADE_PORT, DEFAULT_FACADE_PORT)
            )
            self._control_url = str(user_input.get(CONF_CONTROL_URL, "")).strip()
            self._control_token = str(user_input.get(CONF_CONTROL_TOKEN, "")).strip()

            if not await self.hass.async_add_executor_job(
                ping_proxy, self._proxy_host, self._core_port
            ):
                errors[CONF_PROXY_HOST] = "cannot_connect"
            else:
                goodwe_entries = self.hass.config_entries.async_entries("goodwe")
                if not goodwe_entries:
                    errors["base"] = "no_goodwe"
                elif len(goodwe_entries) > 1:
                    self._goodwe_entries = goodwe_entries
                    self._status = await self.hass.async_add_executor_job(
                        proxy_status, self._proxy_host, self._core_port
                    )
                    return await self.async_step_select_goodwe()
                else:
                    self._status = await self.hass.async_add_executor_job(
                        proxy_status, self._proxy_host, self._core_port
                    )
                    return await self._async_finish(goodwe_entries[0], user_input, errors)

        return self.async_show_form(
            step_id="user", data_schema=self._user_schema(user_input), errors=errors
        )

    async def async_step_select_goodwe(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick which GoodWe entry should be redirected."""
        if user_input is not None:
            entry_id = user_input[CONF_GOODWE_ENTRY_ID]
            goodwe = next(
                (entry for entry in self._goodwe_entries if entry.entry_id == entry_id),
                None,
            )
            if goodwe is not None:
                return await self._async_finish(goodwe, {}, {})
        options = {
            entry.entry_id: f"{entry.title} ({entry.data.get(CONF_HOST, '?')})"
            for entry in self._goodwe_entries
        }
        return self.async_show_form(
            step_id="select_goodwe",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_GOODWE_ENTRY_ID): vol.In(options),
                }
            ),
        )

    async def _async_finish(
        self,
        goodwe: ConfigEntry,
        user_input: dict[str, Any],
        errors: dict[str, str],
    ) -> ConfigFlowResult:
        """Determine the real inverter address, configure the proxy, redirect HA."""
        stated_host = str(goodwe.data.get(CONF_HOST, ""))
        stated_port = int(goodwe.data.get(CONF_PORT, DEFAULT_FACADE_PORT))
        requested_host = str(user_input.get(CONF_INVERTER_HOST, "")).strip()
        requested_port = int(
            user_input.get(CONF_INVERTER_PORT, stated_port or DEFAULT_FACADE_PORT)
        )

        original_host = requested_host or stated_host
        original_port = requested_port if requested_host else stated_port

        candidates = [original_host]
        proxy_known_host = configured_device_host(self._status)
        if proxy_known_host and proxy_known_host not in candidates:
            candidates.append(proxy_known_host)

        chosen_host: str | None = None
        for candidate in candidates:
            if candidate == self._proxy_host:
                continue
            if await self.hass.async_add_executor_job(
                goodwe_udp_alive, candidate, original_port
            ):
                chosen_host = candidate
                break

        if chosen_host is None:
            errors[CONF_INVERTER_HOST] = "inverter_unreachable"
            values = {
                CONF_PROXY_HOST: self._proxy_host,
                CONF_CORE_PORT: self._core_port,
                CONF_FACADE_PORT: self._facade_port,
                CONF_INVERTER_HOST: original_host,
                CONF_INVERTER_PORT: original_port,
            }
            return self.async_show_form(
                step_id="user", data_schema=self._user_schema(values), errors=errors
            )

        response = await self.hass.async_add_executor_job(
            configure_proxy,
            self._proxy_host,
            self._core_port,
            chosen_host,
            original_port,
        )
        if not response or response.get("host") != chosen_host:
            errors["base"] = "configure_failed"
            return self.async_show_form(
                step_id="user", data_schema=self._user_schema(user_input), errors=errors
            )

        await self.async_set_unique_id(
            f"{self._proxy_host}:{self._core_port}:{goodwe.entry_id}"
        )
        self._abort_if_unique_id_configured()

        updated = {**goodwe.data, CONF_HOST: self._proxy_host, CONF_PORT: self._facade_port}
        self.hass.config_entries.async_update_entry(goodwe, data=updated)
        await self.hass.config_entries.async_reload(goodwe.entry_id)

        data: dict[str, Any] = {
            CONF_PROXY_HOST: self._proxy_host,
            CONF_CORE_PORT: self._core_port,
            CONF_FACADE_PORT: self._facade_port,
            CONF_GOODWE_ENTRY_ID: goodwe.entry_id,
            CONF_ORIGINAL_HOST: chosen_host,
            CONF_ORIGINAL_PORT: original_port,
        }
        if self._control_url:
            data[CONF_CONTROL_URL] = self._control_url
        if self._control_token:
            data[CONF_CONTROL_TOKEN] = self._control_token

        return self.async_create_entry(
            title=f"Inverter proxy ({self._proxy_host})",
            data=data,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Offer an options panel with the addresses and control settings."""
        return InverterProxyOptionsFlow(config_entry)


class InverterProxyOptionsFlow(OptionsFlow):
    """Show the configured addresses and let the user set the control token."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Create the flow; HA exposes ``config_entry`` as a property after init."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit the lifecycle control settings without re-adding the integration."""
        current = {**self.config_entry.data, **self.config_entry.options}
        errors: dict[str, str] = {}

        if user_input is not None:
            options = dict(self.config_entry.options)
            url = str(user_input.get(CONF_CONTROL_URL, "")).strip()
            token = str(user_input.get(CONF_CONTROL_TOKEN, "")).strip()
            if url:
                options[CONF_CONTROL_URL] = url
            elif CONF_CONTROL_URL in options:
                options.pop(CONF_CONTROL_URL)
            if token:
                options[CONF_CONTROL_TOKEN] = token
            return self.async_create_entry(title="", data=options)

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_CONTROL_URL,
                    default=current.get(
                        CONF_CONTROL_URL,
                        f"http://{current.get(CONF_PROXY_HOST, '')}:{DEFAULT_CONTROL_PORT}",
                    ),
                ): str,
                vol.Optional(CONF_CONTROL_TOKEN, default=""): selector.TextSelector(
                    selector.TextSelectorConfig(
                        type=selector.TextSelectorType.PASSWORD
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "proxy_address": (
                    f"{current.get(CONF_PROXY_HOST, '?')}:{current.get(CONF_FACADE_PORT, '?')}"
                ),
                "inverter_address": (
                    f"{current.get(CONF_ORIGINAL_HOST, '?')}:{current.get(CONF_ORIGINAL_PORT, '?')}"
                ),
                "core_address": (
                    f"{current.get(CONF_PROXY_HOST, '?')}:{current.get(CONF_CORE_PORT, '?')}"
                ),
            },
        )
