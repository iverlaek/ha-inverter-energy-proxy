"""Home Assistant integration that points GoodWe at the inverter proxy.

On setup HA hands the original inverter address to the proxy core, then keeps
the GoodWe config entry pointed at the proxy's dongle facade. If the facade is
unreachable for five minutes and the real inverter answers, the GoodWe entry is
temporarily restored to the inverter. As soon as the proxy answers again, the
entry is switched back automatically.

The integration also exposes the addresses, a proxy/inverter toggle, and
lifecycle buttons for the proxy VM's control service.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from functools import partial
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    CHECK_INTERVAL_SECONDS,
    CONF_CONTROL_TOKEN,
    CONF_CONTROL_URL,
    CONF_CORE_PORT,
    CONF_FACADE_PORT,
    CONF_GOODWE_ENTRY_ID,
    CONF_ORIGINAL_HOST,
    CONF_ORIGINAL_PORT,
    CONF_PROXY_HOST,
    DEFAULT_CONTROL_PORT,
    DOMAIN,
    FALLBACK_AFTER_SECONDS,
    PLATFORMS,
    update_signal,
)
from .probe import configure_proxy, goodwe_udp_alive, proxy_control

_LOGGER = logging.getLogger(__name__)


def _control_url(runtime: dict[str, Any]) -> str:
    configured = runtime.get(CONF_CONTROL_URL)
    if configured:
        return str(configured)
    return f"http://{runtime[CONF_PROXY_HOST]}:{DEFAULT_CONTROL_PORT}"


def device_info(entry: ConfigEntry) -> DeviceInfo:
    """Describe the single device that owns all integration entities."""
    host = entry.data.get(CONF_PROXY_HOST, "")
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=f"Inverter Proxy ({host})",
        manufacturer="iverlaek",
        model="Inverter Proxy",
        configuration_url=f"http://{host}:{DEFAULT_CONTROL_PORT}",
    )


async def _async_configure_proxy(hass: HomeAssistant, runtime: dict[str, Any]) -> bool:
    """Tell the proxy core where the physical inverter is."""
    response = await hass.async_add_executor_job(
        configure_proxy,
        runtime[CONF_PROXY_HOST],
        runtime[CONF_CORE_PORT],
        runtime[CONF_ORIGINAL_HOST],
        int(runtime[CONF_ORIGINAL_PORT]),
    )
    if not response:
        _LOGGER.warning(
            "Cannot hand inverter address %s:%s to proxy %s:%s",
            runtime[CONF_ORIGINAL_HOST],
            runtime[CONF_ORIGINAL_PORT],
            runtime[CONF_PROXY_HOST],
            runtime[CONF_CORE_PORT],
        )
        runtime["configured"] = False
        return False
    runtime["configured"] = True
    return True


async def async_set_goodwe_address(
    hass: HomeAssistant, entry_id: str, host: str, port: int
) -> bool:
    """Update the GoodWe config entry in place, preserving its entity IDs."""
    goodwe = hass.config_entries.async_get_entry(entry_id)
    if goodwe is None:
        _LOGGER.warning("GoodWe config entry %s no longer exists", entry_id)
        return False
    if goodwe.data.get(CONF_HOST) == host and goodwe.data.get(CONF_PORT) == int(port):
        return False
    updated = {**goodwe.data, CONF_HOST: host, CONF_PORT: int(port)}
    hass.config_entries.async_update_entry(goodwe, data=updated)
    await hass.config_entries.async_reload(entry_id)
    async_dispatcher_send(hass, update_signal(entry_id))
    return True


def async_get_goodwe_state(
    hass: HomeAssistant, entry_id: str, runtime: dict[str, Any]
) -> dict[str, Any]:
    """Return the current addresses and whether GoodWe points at the proxy."""
    goodwe = hass.config_entries.async_get_entry(entry_id)
    host = goodwe.data.get(CONF_HOST) if goodwe else None
    port = goodwe.data.get(CONF_PORT) if goodwe else None
    return {
        "goodwe_host": host,
        "goodwe_port": port,
        "goodwe_address": f"{host}:{port}" if host else "unknown",
        "proxy_host": runtime[CONF_PROXY_HOST],
        "proxy_address": f"{runtime[CONF_PROXY_HOST]}:{runtime[CONF_FACADE_PORT]}",
        "inverter_address": (
            f"{runtime[CONF_ORIGINAL_HOST]}:{runtime[CONF_ORIGINAL_PORT]}"
        ),
        "core_address": f"{runtime[CONF_PROXY_HOST]}:{runtime[CONF_CORE_PORT]}",
        "uses_proxy": host == runtime[CONF_PROXY_HOST],
        "fallback_active": runtime["fallback_active"],
        "proxy_configured": runtime.get("configured", False),
    }


async def async_set_goodwe_mode(
    hass: HomeAssistant, entry_id: str, runtime: dict[str, Any], mode: str
) -> bool:
    """Point GoodWe at either the proxy facade or the physical inverter."""
    if mode == "proxy":
        changed = await async_set_goodwe_address(
            hass,
            entry_id,
            runtime[CONF_PROXY_HOST],
            int(runtime[CONF_FACADE_PORT]),
        )
        runtime["manual_mode"] = None
        return changed
    if mode == "inverter":
        changed = await async_set_goodwe_address(
            hass,
            entry_id,
            runtime[CONF_ORIGINAL_HOST],
            int(runtime[CONF_ORIGINAL_PORT]),
        )
        runtime["manual_mode"] = "inverter"
        return changed
    raise HomeAssistantError(f"Unknown GoodWe mode {mode!r}")


async def async_call_proxy_control(
    hass: HomeAssistant, runtime: dict[str, Any], command: str
) -> dict[str, Any]:
    """Send a lifecycle command to the token-protected VM control service."""
    token = runtime.get(CONF_CONTROL_TOKEN)
    if not token:
        raise HomeAssistantError(
            "No proxy control token configured. Add one in the integration options."
        )
    response = await hass.async_add_executor_job(
        proxy_control, _control_url(runtime), token, command
    )
    if not response or not response.get("ok"):
        raise HomeAssistantError(
            f"Proxy control command {command!r} failed or was rejected"
        )
    return response


async def _async_monitor(
    hass: HomeAssistant, entry_id: str, _now: datetime | None = None
) -> None:
    """Check proxy health and apply the fallback/restore policy."""
    runtime = hass.data.get(DOMAIN, {}).get(entry_id)
    if not runtime:
        return

    proxy_up = await hass.async_add_executor_job(
        goodwe_udp_alive,
        runtime[CONF_PROXY_HOST],
        int(runtime[CONF_FACADE_PORT]),
    )

    if proxy_up:
        runtime["missed_since"] = None
        runtime["inverter_check_logged"] = False
        if not runtime.get("configured"):
            await _async_configure_proxy(hass, runtime)

        goodwe = hass.config_entries.async_get_entry(runtime[CONF_GOODWE_ENTRY_ID])
        current_host = goodwe.data.get(CONF_HOST) if goodwe else None
        if runtime.get("manual_mode") != "inverter" and (
            runtime["fallback_active"] or current_host == runtime[CONF_ORIGINAL_HOST]
        ):
            changed = await async_set_goodwe_address(
                hass,
                runtime[CONF_GOODWE_ENTRY_ID],
                runtime[CONF_PROXY_HOST],
                int(runtime[CONF_FACADE_PORT]),
            )
            if changed:
                _LOGGER.info(
                    "Proxy %s is back; GoodWe switched from inverter %s to proxy",
                    runtime[CONF_PROXY_HOST],
                    runtime[CONF_ORIGINAL_HOST],
                )
            runtime["fallback_active"] = False
        async_dispatcher_send(hass, update_signal(entry_id))
        return

    # The proxy is not answering the GoodWe facade right now.
    now = time.monotonic()
    if runtime["missed_since"] is None:
        runtime["missed_since"] = now
        _LOGGER.warning(
            "Proxy %s:%s is unreachable; will fall back to inverter %s after %s s "
            "if the inverter is still running",
            runtime[CONF_PROXY_HOST],
            runtime[CONF_FACADE_PORT],
            runtime[CONF_ORIGINAL_HOST],
            FALLBACK_AFTER_SECONDS,
        )

    if runtime["fallback_active"]:
        return
    if now - runtime["missed_since"] < FALLBACK_AFTER_SECONDS:
        return

    inverter_up = await hass.async_add_executor_job(
        goodwe_udp_alive,
        runtime[CONF_ORIGINAL_HOST],
        int(runtime[CONF_ORIGINAL_PORT]),
    )
    if not inverter_up:
        if not runtime["inverter_check_logged"]:
            _LOGGER.warning(
                "Proxy is down but inverter %s:%s is not answering either; "
                "keeping the GoodWe address unchanged",
                runtime[CONF_ORIGINAL_HOST],
                runtime[CONF_ORIGINAL_PORT],
            )
            runtime["inverter_check_logged"] = True
        return

    changed = await async_set_goodwe_address(
        hass,
        runtime[CONF_GOODWE_ENTRY_ID],
        runtime[CONF_ORIGINAL_HOST],
        int(runtime[CONF_ORIGINAL_PORT]),
    )
    if changed:
        runtime["fallback_active"] = True
        runtime["manual_mode"] = None
        _LOGGER.warning(
            "Proxy unavailable for more than %s minutes; GoodWe reverted directly "
            "to inverter %s:%s until the proxy returns",
            FALLBACK_AFTER_SECONDS // 60,
            runtime[CONF_ORIGINAL_HOST],
            runtime[CONF_ORIGINAL_PORT],
        )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up proxy monitoring and expose status/control entities."""
    data = {**entry.data, **entry.options}
    goodwe = hass.config_entries.async_get_entry(data[CONF_GOODWE_ENTRY_ID])
    fallback_active = bool(
        goodwe and goodwe.data.get(CONF_HOST) == data[CONF_ORIGINAL_HOST]
    )

    runtime: dict[str, Any] = {
        CONF_PROXY_HOST: data[CONF_PROXY_HOST],
        CONF_CORE_PORT: int(data[CONF_CORE_PORT]),
        CONF_FACADE_PORT: int(data[CONF_FACADE_PORT]),
        CONF_GOODWE_ENTRY_ID: data[CONF_GOODWE_ENTRY_ID],
        CONF_ORIGINAL_HOST: data[CONF_ORIGINAL_HOST],
        CONF_ORIGINAL_PORT: int(data[CONF_ORIGINAL_PORT]),
        CONF_CONTROL_URL: data.get(CONF_CONTROL_URL),
        CONF_CONTROL_TOKEN: data.get(CONF_CONTROL_TOKEN),
        "fallback_active": fallback_active,
        "manual_mode": None,
        "missed_since": None,
        "inverter_check_logged": False,
        "configured": False,
    }
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = runtime

    await _async_configure_proxy(hass, runtime)
    unsub = async_track_time_interval(
        hass,
        partial(_async_monitor, hass, entry.entry_id),
        timedelta(seconds=CHECK_INTERVAL_SECONDS),
    )
    runtime["unsub"] = unsub
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Cancel the monitor when the integration entry is unloaded."""
    runtime = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if runtime and runtime.get("unsub"):
        runtime["unsub"]()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Restore the original GoodWe address when the integration is removed."""
    await async_set_goodwe_address(
        hass,
        entry.data[CONF_GOODWE_ENTRY_ID],
        entry.data[CONF_ORIGINAL_HOST],
        int(entry.data[CONF_ORIGINAL_PORT]),
    )
