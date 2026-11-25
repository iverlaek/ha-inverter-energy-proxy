"""Checkmark sensor showing whether GoodWe points at the proxy."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import async_get_goodwe_state, device_info
from .const import DOMAIN, update_signal


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Add the 'GoodWe uses proxy' checkmark."""
    async_add_entities([GoodWeUsesProxySensor(entry)])


class GoodWeUsesProxySensor(BinarySensorEntity):
    """On when the GoodWe integration is configured with the proxy address."""

    _attr_should_poll = False
    _attr_translation_key = "goodwe_uses_proxy"
    _attr_icon = "mdi:check-network"

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        self._runtime: dict[str, Any] | None = None
        self._attr_unique_id = f"{entry.entry_id}_uses_proxy"
        self._attr_device_info = device_info(entry)
        self._attr_is_on = False
        self._attr_extra_state_attributes: dict[str, Any] = {}
        self._unsub: Callable[[], None] | None = None

    async def async_added_to_hass(self) -> None:
        """Subscribe to runtime updates and sync the initial value."""
        self._runtime = self.hass.data[DOMAIN][self._entry.entry_id]
        self._unsub = async_dispatcher_connect(
            self.hass, update_signal(self._entry.entry_id), self._handle_update
        )
        self._handle_update()

    async def async_will_remove_from_hass(self) -> None:
        """Detach the dispatcher listener."""
        if self._unsub:
            self._unsub()
            self._unsub = None

    @callback
    def _handle_update(self) -> None:
        state = async_get_goodwe_state(self.hass, self._entry.entry_id, self._runtime)
        self._attr_is_on = bool(state["uses_proxy"])
        self._attr_icon = "mdi:check-network" if self._attr_is_on else "mdi:close-network"
        self._attr_extra_state_attributes = {
            "goodwe_address": state["goodwe_address"],
            "proxy_address": state["proxy_address"],
            "inverter_address": state["inverter_address"],
            "fallback_active": state["fallback_active"],
            "proxy_configured": state["proxy_configured"],
        }
        self.async_write_ha_state()
