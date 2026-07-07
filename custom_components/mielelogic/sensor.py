"""Sensor platform for the MieleLogic integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import MachineData, MieleLogicDataUpdateCoordinator

# Maps the API's MachineSymbol field to a Material Design Icons name.
MACHINE_SYMBOL_ICONS: dict[int, str] = {
    0: "mdi:washing-machine",
    1: "mdi:tumble-dryer",
}
DEFAULT_MACHINE_ICON = "mdi:help-circle-outline"


def _format_program(program: int | None, temperature: int | None) -> str | None:
    """Format program/temperature like the portal, e.g. 'P2, T40'."""
    parts: list[str] = []
    if program is not None:
        parts.append(f"P{program}")
    if temperature is not None:
        parts.append(f"T{temperature}")
    return ", ".join(parts) if parts else None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up MieleLogic sensors from a config entry."""
    coordinator: MieleLogicDataUpdateCoordinator = entry.runtime_data

    # The account balance sensor always exists.
    entities: list[SensorEntity] = [MieleLogicBalanceSensor(coordinator, entry)]

    # Machine sensors are created dynamically from the polled data. Track which
    # ones we have already added so we can add new machines discovered later.
    known_machines: set[str] = set()

    @callback
    def _async_add_new_machines() -> None:
        data = coordinator.data
        if data is None:
            return

        new_entities: list[SensorEntity] = []
        for key in data.machines:
            if key in known_machines:
                continue
            known_machines.add(key)
            new_entities.append(MieleLogicMachineSensor(coordinator, entry, key))
            new_entities.append(
                MieleLogicMachineRemainingSensor(coordinator, entry, key)
            )

        if new_entities:
            async_add_entities(new_entities)

    # Add machines present in the first refresh, then keep listening for more.
    _async_add_new_machines()
    entry.async_on_unload(coordinator.async_add_listener(_async_add_new_machines))

    async_add_entities(entities)


class MieleLogicBalanceSensor(
    CoordinatorEntity[MieleLogicDataUpdateCoordinator], SensorEntity
):
    """Sensor for the account's total balance in DKK."""

    _attr_has_entity_name = True
    _attr_translation_key = "balance"
    _attr_icon = "mdi:cash"
    _attr_device_class = SensorDeviceClass.MONETARY

    def __init__(
        self,
        coordinator: MieleLogicDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialise the balance sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_balance"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"account_{entry.entry_id}")},
            name="MieleLogic",
            manufacturer="Miele",
            model="Account",
        )

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Return the account currency (defaults to DKK)."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.currency

    @property
    def native_value(self) -> float | None:
        """Return the current account balance."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.balance


class MieleLogicMachineSensor(
    CoordinatorEntity[MieleLogicDataUpdateCoordinator], SensorEntity
):
    """Sensor representing a single laundry machine's state."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: MieleLogicDataUpdateCoordinator,
        entry: ConfigEntry,
        machine_key: str,
    ) -> None:
        """Initialise the machine sensor."""
        super().__init__(coordinator)
        self._machine_key = machine_key
        self._attr_unique_id = f"{entry.entry_id}_machine_{machine_key}"

        machine = self._machine
        # The machine is guaranteed to exist at creation time, but guard anyway.
        laundry_number = machine.laundry_number if machine else "unknown"
        laundry_name = machine.laundry_name if machine else "MieleLogic"

        # One device per laundry; all its machines group under it.
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"laundry_{laundry_number}")},
            name=laundry_name,
            manufacturer="Miele",
            model="Laundry",
        )

    @property
    def _machine(self) -> MachineData | None:
        """Return the current MachineData for this sensor, or None if gone."""
        data = self.coordinator.data
        if data is None:
            return None
        return data.machines.get(self._machine_key)

    @property
    def name(self) -> str | None:
        """Return the machine's UnitName."""
        machine = self._machine
        if machine is None:
            return None
        return machine.unit_name

    @property
    def available(self) -> bool:
        """Machine is available only while it is present in the polled data."""
        return super().available and self._machine is not None

    @property
    def icon(self) -> str:
        """Return an icon based on the API's MachineSymbol field."""
        machine = self._machine
        if machine is None:
            return DEFAULT_MACHINE_ICON
        return MACHINE_SYMBOL_ICONS.get(machine.machine_symbol, DEFAULT_MACHINE_ICON)

    @property
    def native_value(self) -> str | None:
        """Return a stable status ('Ledig' / 'I brug'), derived from MachineColor.

        We deliberately avoid using Text1 directly here: while a machine runs,
        Text1 counts down ('Resttid: 30 min.' -> '29 min.' ...) and would spam
        the state history. The numeric countdown is exposed separately.
        """
        machine = self._machine
        if machine is None:
            return None
        return machine.status

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the parsed remaining time and a few useful raw fields."""
        machine = self._machine
        if machine is None:
            return {}

        attrs: dict[str, Any] = {
            "remaining_minutes": machine.remaining_minutes,
            "status_text": machine.text1,
            "status_text2": machine.text2,
            "unit_name": machine.unit_name,
            "laundry_number": machine.laundry_number,
            "laundry_name": machine.laundry_name,
            "machine_number": machine.machine_number,
            "machine_symbol": machine.machine_symbol,
            "machine_type": machine.machine_type,
            "machine_color": machine.machine_color,
            "group_number": machine.group_number,
            # Heuristic: is the current run linked to my account?
            "mine": machine.mine_running,
        }

        # Historical link: the last run I paid for on this exact machine.
        txn = machine.last_transaction
        if txn is not None:
            attrs["last_used_by_me"] = txn.transaction_time
            if txn.program is not None or txn.temperature is not None:
                attrs["last_program"] = _format_program(txn.program, txn.temperature)
            attrs["last_amount"] = txn.amount

        return attrs


class MieleLogicMachineRemainingSensor(
    CoordinatorEntity[MieleLogicDataUpdateCoordinator], SensorEntity
):
    """Numeric remaining-time sensor for a machine, parsed from Text1."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:timer-sand"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: MieleLogicDataUpdateCoordinator,
        entry: ConfigEntry,
        machine_key: str,
    ) -> None:
        """Initialise the remaining-time sensor."""
        super().__init__(coordinator)
        self._machine_key = machine_key
        self._attr_unique_id = f"{entry.entry_id}_machine_{machine_key}_remaining"

        machine = coordinator.data.machines.get(machine_key) if coordinator.data else None
        laundry_number = machine.laundry_number if machine else "unknown"
        laundry_name = machine.laundry_name if machine else "MieleLogic"

        # Share the same per-laundry device as the status sensor.
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"laundry_{laundry_number}")},
            name=laundry_name,
            manufacturer="Miele",
            model="Laundry",
        )

    @property
    def _machine(self) -> MachineData | None:
        """Return the current MachineData for this sensor, or None if gone."""
        data = self.coordinator.data
        if data is None:
            return None
        return data.machines.get(self._machine_key)

    @property
    def name(self) -> str | None:
        """Return '<UnitName> remaining time'."""
        machine = self._machine
        if machine is None:
            return None
        return f"{machine.unit_name} resttid"

    @property
    def available(self) -> bool:
        """Available only while the machine is present in the polled data."""
        return super().available and self._machine is not None

    @property
    def native_value(self) -> int | None:
        """Remaining minutes while running, else 0 (idle)."""
        machine = self._machine
        if machine is None:
            return None
        remaining = machine.remaining_minutes
        # When the machine is free there is no countdown; report 0 rather than
        # None so long-term statistics stay continuous.
        return remaining if remaining is not None else 0
