"""The MieleLogic integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from datetime import timedelta

from .const import CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, DOMAIN, PLATFORMS
from .coordinator import MieleLogicDataUpdateCoordinator

# Modern config-entry-scoped storage: coordinator lives on entry.runtime_data.
type MieleLogicConfigEntry = ConfigEntry[MieleLogicDataUpdateCoordinator]


async def async_setup_entry(
    hass: HomeAssistant, entry: MieleLogicConfigEntry
) -> bool:
    """Set up MieleLogic from a config entry."""
    coordinator = MieleLogicDataUpdateCoordinator(hass, entry)

    # Perform the first refresh so entities have data on startup. This raises
    # ConfigEntryNotReady / ConfigEntryAuthFailed automatically on failure.
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator

    # Also stash it in hass.data for backwards compatibility with helpers that
    # still look it up by entry_id.
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Reload the entry when the user changes options (e.g. scan interval).
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def _async_update_listener(
    hass: HomeAssistant, entry: MieleLogicConfigEntry
) -> None:
    """Reload the entry only when the scan interval option actually changed.

    The entry is also updated whenever we persist refreshed tokens; those
    changes must NOT trigger a reload (that would restart the integration on
    every token refresh), so we compare the effective interval first.
    """
    coordinator = entry.runtime_data
    new_interval = timedelta(
        seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    )
    if coordinator.update_interval != new_interval:
        await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(
    hass: HomeAssistant, entry: MieleLogicConfigEntry
) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)

    return unload_ok
