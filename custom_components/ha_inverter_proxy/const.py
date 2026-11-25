"""Constants for the HA inverter proxy integration."""

from __future__ import annotations

DOMAIN = "ha_inverter_proxy"

CONF_PROXY_HOST = "proxy_host"
CONF_CORE_PORT = "core_port"
CONF_FACADE_PORT = "facade_port"
CONF_GOODWE_ENTRY_ID = "goodwe_entry_id"
CONF_ORIGINAL_HOST = "original_host"
CONF_ORIGINAL_PORT = "original_port"
CONF_INVERTER_HOST = "inverter_host"
CONF_INVERTER_PORT = "inverter_port"

DEFAULT_CORE_PORT = 8787
DEFAULT_FACADE_PORT = 8899

CHECK_INTERVAL_SECONDS = 30
FALLBACK_AFTER_SECONDS = 300
