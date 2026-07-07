"""DataUpdateCoordinator for the MieleLogic integration."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    MieleLogicApiClient,
    MieleLogicAuthError,
    MieleLogicConnectionError,
)
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_EXPIRES_AT,
    CONF_REFRESH_TOKEN,
    DOMAIN,
    LOGGER,
    TOKEN_REFRESH_MARGIN,
    TRANSACTION_HISTORY_DAYS,
    UPDATE_INTERVAL,
)


# The remaining time is embedded in the Danish Text1 string, e.g.
# "Resttid: 30 min.". Parse the first integer followed by "min".
_REMAINING_RE = re.compile(r"(\d+)\s*min", re.IGNORECASE)

# MachineColor is the reliable status indicator (verified empirically):
#   1 = free/available (green), 2 = in use/running (yellow).
# Other values (e.g. reserved) are not yet observed; we fall back to Text1.
MACHINE_COLOR_FREE = 1
MACHINE_COLOR_IN_USE = 2
_MACHINE_COLOR_STATUS: dict[int, str] = {
    MACHINE_COLOR_FREE: "Ledig",
    MACHINE_COLOR_IN_USE: "I brug",
}

# A running machine is considered "mine" if I have a transaction for that exact
# machine whose payment time (= cycle start) falls within this window. Cycles
# are well under a few hours, so this comfortably covers a full wash/dry.
MINE_MATCH_WINDOW = timedelta(hours=4)


@dataclass
class MachineData:
    """A single machine's live state within a laundry."""

    laundry_number: str
    laundry_name: str
    machine_number: int
    unit_name: str
    machine_symbol: int | None
    machine_type: str | None
    group_number: int | None
    machine_color: int | None
    text1: str | None
    text2: str | None

    # Filled in by the transaction-linking pass (see coordinator).
    last_transaction: TransactionData | None = None
    mine_running: bool = False

    @property
    def unique_key(self) -> str:
        """Stable key identifying this machine across updates."""
        return f"{self.laundry_number}_{self.machine_number}"

    @property
    def status(self) -> str | None:
        """Return a stable status string derived from MachineColor.

        Unlike Text1 (which counts down every minute while running), this only
        changes when the machine actually changes state, so it is safe to use
        as the sensor's primary value. Unknown colors fall back to Text1.
        """
        if self.machine_color in _MACHINE_COLOR_STATUS:
            return _MACHINE_COLOR_STATUS[self.machine_color]
        return self.text1

    @property
    def remaining_minutes(self) -> int | None:
        """Parse the remaining minutes out of the status texts.

        Different laundries put the countdown in different fields: some in
        Text1 ("Resttid: 30 min.") and some in Text2 ("Resttid 23 min"). Search
        both and return the first match, or None if the machine is not running.
        """
        for text in (self.text1, self.text2):
            if not text:
                continue
            match = _REMAINING_RE.search(text)
            if match:
                return int(match.group(1))
        return None

    @property
    def is_running(self) -> bool:
        """Whether the machine is currently in use."""
        return self.machine_color == MACHINE_COLOR_IN_USE


@dataclass
class TransactionData:
    """A single normalised transaction (amounts converted to DKK)."""

    laundry_address: str | None
    serial_number: str | None
    machine_number: int | None
    program: int | None
    temperature: int | None
    transaction_time: str | None
    amount: float | None
    balance: float | None


@dataclass
class MieleLogicData:
    """Aggregated data returned by the coordinator to the sensors."""

    balance: float | None = None
    currency: str = "DKK"
    # LaundryNumber -> Name, from AccessibleLaundries.
    laundries: dict[str, str] = field(default_factory=dict)
    # "{laundry}_{machine}" -> MachineData.
    machines: dict[str, MachineData] = field(default_factory=dict)
    transactions: list[TransactionData] = field(default_factory=list)


class MieleLogicDataUpdateCoordinator(DataUpdateCoordinator[MieleLogicData]):
    """Coordinate polling of the MieleLogic API for a single account."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialise the coordinator from a config entry."""
        self.entry = entry

        session = async_get_clientsession(hass)
        self.client = MieleLogicApiClient(
            session,
            access_token=entry.data.get(CONF_ACCESS_TOKEN),
            refresh_token=entry.data.get(CONF_REFRESH_TOKEN),
            expires_at=entry.data.get(CONF_EXPIRES_AT),
        )

        super().__init__(
            hass,
            LOGGER,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )

    async def _async_update_data(self) -> MieleLogicData:
        """Fetch the latest data, refreshing the access token if needed."""
        try:
            # Refresh the token proactively if it is missing or near expiry.
            changed = await self.client.async_ensure_token(
                margin_seconds=TOKEN_REFRESH_MARGIN.total_seconds()
            )
            if changed:
                self._persist_tokens()

            data = MieleLogicData()

            # 1. Account balance + list of accessible laundries.
            await self._async_load_accounts(data)

            # 2. Live machine states for every accessible laundry.
            await self._async_load_machines(data)

            # 3. Recent transaction history.
            await self._async_load_transactions(data)

            # 4. Link transactions to machines (which run is "mine").
            self._link_transactions(data)

            return data

        except MieleLogicAuthError as err:
            # Token is invalid and could not be refreshed -> trigger reauth.
            raise ConfigEntryAuthFailed(str(err)) from err
        except MieleLogicConnectionError as err:
            raise UpdateFailed(str(err)) from err

    async def _async_load_accounts(self, data: MieleLogicData) -> None:
        """Populate balance, currency and the laundry list from /accounts."""
        response = await self.client.async_get_accounts()

        accounts = response.get("Account") or []
        if accounts:
            account = accounts[0] or {}
            balance = account.get("AccountBalance")
            data.balance = float(balance) if balance is not None else None
            data.currency = account.get("Currency") or "DKK"

        for laundry in response.get("AccessibleLaundries") or []:
            number = laundry.get("LaundryNumber")
            if number is None:
                continue
            data.laundries[str(number)] = laundry.get("Name") or str(number)

    async def _async_load_machines(self, data: MieleLogicData) -> None:
        """Populate the machine dictionary from each laundry's states."""
        for laundry_number, laundry_name in data.laundries.items():
            try:
                response = await self.client.async_get_laundry_states(laundry_number)
            except MieleLogicConnectionError as err:
                # A single laundry being unreachable should not fail the whole
                # update; log and continue with whatever we can gather.
                LOGGER.warning(
                    "Could not fetch machine states for laundry %s: %s",
                    laundry_number,
                    err,
                )
                continue

            for machine in response.get("MachineStates") or []:
                machine_number = machine.get("MachineNumber")
                if machine_number is None:
                    continue

                # LaundryNumber in the machine record can differ in type from
                # the account list; normalise to the account's key.
                machine_data = MachineData(
                    laundry_number=laundry_number,
                    laundry_name=laundry_name,
                    machine_number=machine_number,
                    unit_name=machine.get("UnitName") or f"Machine {machine_number}",
                    machine_symbol=machine.get("MachineSymbol"),
                    machine_type=machine.get("MachineType"),
                    group_number=machine.get("GroupNumber"),
                    machine_color=machine.get("MachineColor"),
                    text1=machine.get("Text1"),
                    text2=machine.get("Text2"),
                )
                data.machines[machine_data.unique_key] = machine_data

    async def _async_load_transactions(self, data: MieleLogicData) -> None:
        """Populate the transaction history from /accounts/transactions."""
        now = dt_util.now()
        date_from = now - timedelta(days=TRANSACTION_HISTORY_DAYS)
        # API expects "YYYY-MM-DD-HH".
        fmt = "%Y-%m-%d-%H"

        try:
            response = await self.client.async_get_transactions(
                date_from.strftime(fmt), now.strftime(fmt)
            )
        except MieleLogicConnectionError as err:
            LOGGER.warning("Could not fetch transactions: %s", err)
            return

        for txn in response.get("Transactions") or []:
            data.transactions.append(
                TransactionData(
                    laundry_address=txn.get("LaundryAddress"),
                    serial_number=txn.get("SerialNumber"),
                    machine_number=txn.get("MachineNumber"),
                    program=txn.get("Program"),
                    temperature=txn.get("Temperature"),
                    transaction_time=txn.get("TransactionTime"),
                    amount=_ore_to_dkk(txn.get("Amount")),
                    balance=_ore_to_dkk(txn.get("Balance")),
                )
            )

    def _link_transactions(self, data: MieleLogicData) -> None:
        """Annotate each machine with its matching transaction.

        A transaction's ``SerialNumber`` equals the machine's ``LaundryNumber``,
        and together with ``MachineNumber`` it identifies one physical machine.
        For each machine we attach the most recent matching transaction, and
        flag it as "mine" if that machine is currently running and the payment
        happened within :data:`MINE_MATCH_WINDOW`.
        """
        if not data.transactions:
            return

        now = dt_util.now()

        for machine in data.machines.values():
            matches = [
                txn
                for txn in data.transactions
                if txn.serial_number is not None
                and str(txn.serial_number) == str(machine.laundry_number)
                and txn.machine_number == machine.machine_number
            ]
            if not matches:
                continue

            # Newest transaction wins (ISO timestamps sort lexically).
            newest = max(matches, key=lambda t: t.transaction_time or "")
            machine.last_transaction = newest

            if not machine.is_running or not newest.transaction_time:
                continue

            paid = dt_util.parse_datetime(newest.transaction_time)
            if paid is None:
                continue
            # TransactionTime carries no timezone; treat it as local time.
            if paid.tzinfo is None:
                paid = paid.replace(tzinfo=now.tzinfo)
            if now - paid <= MINE_MATCH_WINDOW:
                machine.mine_running = True

    def _persist_tokens(self) -> None:
        """Write the current token set back into the config entry."""
        self.hass.config_entries.async_update_entry(
            self.entry,
            data={
                **self.entry.data,
                CONF_ACCESS_TOKEN: self.client.access_token,
                CONF_REFRESH_TOKEN: self.client.refresh_token,
                CONF_EXPIRES_AT: self.client.expires_at,
            },
        )


def _ore_to_dkk(value: int | None) -> float | None:
    """Convert an integer amount in øre to a float amount in DKK."""
    if value is None:
        return None
    return value / 100
