"""Read-only address sensors shown on the proxy device panel."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import async_get_goodwe_state, device_info
from .const import CONF_GOODWE_ENTRY_ID, DOMAIN, update_signal


@dataclass(frozen=True, kw_only=True)
class ProxyAddressSensorDescription(SensorEntityDescription):
    """Sensor description that extracts one address from the runtime state."""

    value_fn: Callable[[dict[str, Any]], str]


DESCRIPTIONS: tuple[ProxyAddressSensorDescription, ...] = (
    ProxyAddressSensorDescription(
        key="goodwe_address",
        name="GoodWe configured address",
        icon="mdi:lan-connect",
        value_fn=lambda state: str(state["goodwe_address"]),
    ),
    ProxyAddressSensorDescription(
        key="proxy_address",
        name="Proxy GoodWe address",
        icon="mdi:lan",
        value_fn=lambda state: str(state["proxy_address"]),
    ),
    ProxyAddressSensorDescription(
        key="inverter_address",
        name="Inverter address",
        icon="mdi:solar-power",
        value_fn=lambda state: str(state["inverter_address"]),
    ),
    ProxyAddressSensorDescription(
        key="core_address",
        name="Proxy control address",
        icon="mdi:cog",
        value_fn=lambda state: str(state["core_address"]),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Add the address sensors for this proxy entry."""
    async_add_entities(
        ProxyAddressSensor(entry, description) for description in DESCRIPTIONS
    )


class ProxyAddressSensor(SensorEntity):
    """Display one address and refresh when the GoodWe target changes."""

    entity_description: ProxyAddressSensorDescription
    _attr_should_poll = False
    _attr_has_entity_name = False

    def __init__(
        self, entry: ConfigEntry, description: ProxyAddressSensorDescription
    ) -> None:
        self.entity_description = description
        self._entry = entry
        self._runtime = None
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = device_info(entry)
        self._attr_native_value = None
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
        goodwe_id = self._runtime[CONF_GOODWE_ENTRY_ID]
        state = async_get_goodwe_state(self.hass, goodwe_id, self._runtime)
        self._attr_native_value = self.entity_description.value_fn(state)
        self._attr_extra_state_attributes = {
            "proxy_address": state["proxy_address"],
            "inverter_address": state["inverter_address"],
            "goodwe_address": state["goodwe_address"],
            "uses_proxy": state["uses_proxy"],
        }
        self.async_write_ha_state()
