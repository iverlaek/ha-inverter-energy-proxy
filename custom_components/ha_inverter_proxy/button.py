"""Buttons to switch GoodWe between proxy/inverter and control the proxy VM."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import async_call_proxy_control, async_set_goodwe_mode, device_info
from .const import DOMAIN


@dataclass(frozen=True, kw_only=True)
class ProxyButtonDescription(ButtonEntityDescription):
    """Button description with the action it performs."""

    press_fn: Callable[[HomeAssistant, ConfigEntry, dict], Awaitable[None]]


async def _use_proxy(
    hass: HomeAssistant, entry: ConfigEntry, runtime: dict
) -> None:
    await async_set_goodwe_mode(hass, entry.entry_id, runtime, "proxy")


async def _use_inverter(
    hass: HomeAssistant, entry: ConfigEntry, runtime: dict
) -> None:
    await async_set_goodwe_mode(hass, entry.entry_id, runtime, "inverter")


def _lifecycle(command: str) -> Callable[[HomeAssistant, ConfigEntry, dict], Awaitable[None]]:
    async def _press(
        hass: HomeAssistant, entry: ConfigEntry, runtime: dict
    ) -> None:
        await async_call_proxy_control(hass, runtime, command)

    return _press


DESCRIPTIONS: tuple[ProxyButtonDescription, ...] = (
    ProxyButtonDescription(
        key="use_proxy",
        translation_key="use_proxy",
        icon="mdi:lan-connect",
        press_fn=_use_proxy,
    ),
    ProxyButtonDescription(
        key="use_inverter",
        translation_key="use_inverter",
        icon="mdi:solar-power",
        press_fn=_use_inverter,
    ),
    ProxyButtonDescription(
        key="start_proxy",
        translation_key="start_proxy",
        icon="mdi:play",
        press_fn=_lifecycle("start"),
    ),
    ProxyButtonDescription(
        key="stop_proxy",
        translation_key="stop_proxy",
        icon="mdi:stop",
        press_fn=_lifecycle("stop"),
    ),
    ProxyButtonDescription(
        key="reboot_proxy",
        translation_key="reboot_proxy",
        icon="mdi:restart",
        press_fn=_lifecycle("reboot"),
    ),
    ProxyButtonDescription(
        key="shutdown_proxy",
        translation_key="shutdown_proxy",
        icon="mdi:power",
        press_fn=_lifecycle("shutdown"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Add all proxy control buttons for this entry."""
    async_add_entities(ProxyButton(entry, description) for description in DESCRIPTIONS)


class ProxyButton(ButtonEntity):
    """A control action for the GoodWe target or proxy service."""

    entity_description: ProxyButtonDescription
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry, description: ProxyButtonDescription) -> None:
        self.entity_description = description
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = device_info(entry)

    async def async_press(self) -> None:
        """Run the configured action."""
        runtime = self.hass.data[DOMAIN][self._entry.entry_id]
        await self.entity_description.press_fn(self.hass, self._entry, runtime)
