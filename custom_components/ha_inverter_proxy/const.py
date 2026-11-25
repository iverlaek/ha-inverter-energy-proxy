"""Constants for the HA inverter proxy integration."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "ha_inverter_proxy"

CONF_PROXY_HOST = "proxy_host"
CONF_CORE_PORT = "core_port"
CONF_FACADE_PORT = "facade_port"
CONF_GOODWE_ENTRY_ID = "goodwe_entry_id"
CONF_ORIGINAL_HOST = "original_host"
CONF_ORIGINAL_PORT = "original_port"
CONF_INVERTER_HOST = "inverter_host"
CONF_INVERTER_PORT = "inverter_port"
CONF_CONTROL_URL = "control_url"
CONF_CONTROL_TOKEN = "control_token"
CONF_CHECK_INTERVAL_SECONDS = "check_interval_seconds"
CONF_FALLBACK_AFTER_SECONDS = "fallback_after_seconds"

DEFAULT_CORE_PORT = 8787
DEFAULT_FACADE_PORT = 8899
DEFAULT_CONTROL_PORT = 8790

DEFAULT_CHECK_INTERVAL_SECONDS = 30
DEFAULT_FALLBACK_AFTER_SECONDS = 300

PLATFORMS = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.SENSOR]

SIGNAL_UPDATE = f"{DOMAIN}_update"


def update_signal(entry_id: str) -> str:
    """Return the dispatcher signal used to refresh this entry's entities."""
    return f"{SIGNAL_UPDATE}_{entry_id}"
