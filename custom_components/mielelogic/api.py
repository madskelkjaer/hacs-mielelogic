"""Low-level API client and token handling for MieleLogic."""

from __future__ import annotations

from datetime import datetime, timezone

import aiohttp
import async_timeout

from .const import (
    ACCOUNTS_URL,
    GRANT_TYPE_PASSWORD,
    GRANT_TYPE_REFRESH,
    LAUNDRY_STATES_URL,
    LOGGER,
    REQUEST_TIMEOUT,
    TOKEN_LIFETIME,
    TOKEN_URL,
    TRANSACTIONS_URL,
)


class MieleLogicError(Exception):
    """Base error for the MieleLogic API."""


class MieleLogicAuthError(MieleLogicError):
    """Raised when authentication fails (bad credentials / invalid token)."""


class MieleLogicConnectionError(MieleLogicError):
    """Raised when the API cannot be reached."""


def _utcnow() -> datetime:
    """Return an aware UTC timestamp."""
    return datetime.now(timezone.utc)


class MieleLogicApiClient:
    """Thin async wrapper around the MieleLogic REST API.

    Holds the current token set and knows how to obtain and refresh it.
    Token material is returned to callers so it can be persisted in the
    config entry; this client keeps an in-memory copy for live requests.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        access_token: str | None = None,
        refresh_token: str | None = None,
        expires_at: float | None = None,
    ) -> None:
        """Initialise the client, optionally with previously stored tokens."""
        self._session = session
        self._access_token = access_token
        self._refresh_token = refresh_token
        # Stored as a POSIX timestamp (seconds since epoch, UTC).
        self._expires_at = expires_at

    # ---------------------------------------------------------------------
    # Token accessors
    # ---------------------------------------------------------------------
    @property
    def access_token(self) -> str | None:
        """Return the current access token."""
        return self._access_token

    @property
    def refresh_token(self) -> str | None:
        """Return the current refresh token."""
        return self._refresh_token

    @property
    def expires_at(self) -> float | None:
        """Return the access token expiry as a POSIX timestamp."""
        return self._expires_at

    def token_data(self) -> dict[str, str | float | None]:
        """Return the token set as a serialisable dict for the config entry."""
        return {
            "access_token": self._access_token,
            "refresh_token": self._refresh_token,
            "expires_at": self._expires_at,
        }

    def is_token_valid(self, margin_seconds: float = 0) -> bool:
        """Return True if the access token exists and is not (nearly) expired."""
        if not self._access_token or self._expires_at is None:
            return False
        return _utcnow().timestamp() + margin_seconds < self._expires_at

    # ---------------------------------------------------------------------
    # Auth
    # ---------------------------------------------------------------------
    async def async_login(self, username: str, password: str) -> dict:
        """Authenticate with username/password and store the resulting tokens."""
        return await self._async_token_request(
            {
                "grant_type": GRANT_TYPE_PASSWORD,
                "username": username,
                "password": password,
            }
        )

    async def async_refresh_token(self) -> dict:
        """Exchange the refresh token for a fresh access token."""
        if not self._refresh_token:
            raise MieleLogicAuthError("No refresh token available")

        return await self._async_token_request(
            {
                "grant_type": GRANT_TYPE_REFRESH,
                "refresh_token": self._refresh_token,
            }
        )

    async def async_ensure_token(self, margin_seconds: float = 0) -> bool:
        """Refresh the access token if it is missing or about to expire.

        Returns True if the token set changed (and therefore needs persisting).
        """
        if self.is_token_valid(margin_seconds):
            return False
        await self.async_refresh_token()
        return True

    async def _async_token_request(self, payload: dict[str, str]) -> dict:
        """Perform a POST to the token endpoint and store the result."""
        try:
            async with async_timeout.timeout(REQUEST_TIMEOUT):
                async with self._session.post(TOKEN_URL, data=payload) as resp:
                    if resp.status in (400, 401, 403):
                        text = await resp.text()
                        LOGGER.debug("Auth rejected (%s): %s", resp.status, text)
                        raise MieleLogicAuthError(
                            f"Authentication failed with status {resp.status}"
                        )
                    resp.raise_for_status()
                    data = await resp.json()
        except MieleLogicAuthError:
            raise
        except aiohttp.ClientResponseError as err:
            raise MieleLogicConnectionError(
                f"Unexpected response from token endpoint: {err.status}"
            ) from err
        except (aiohttp.ClientError, TimeoutError) as err:
            raise MieleLogicConnectionError(
                f"Could not reach token endpoint: {err}"
            ) from err

        self._store_token_response(data)
        return self.token_data()

    def _store_token_response(self, data: dict) -> None:
        """Extract and cache token material from a token endpoint response."""
        access_token = data.get("access_token")
        if not access_token:
            raise MieleLogicAuthError("Token response did not contain an access_token")

        self._access_token = access_token
        # Keep the previous refresh token if the server did not rotate it.
        self._refresh_token = data.get("refresh_token", self._refresh_token)

        expires_in = data.get("expires_in")
        if expires_in is not None:
            lifetime = float(expires_in)
        else:
            lifetime = TOKEN_LIFETIME.total_seconds()
        self._expires_at = _utcnow().timestamp() + lifetime

    # ---------------------------------------------------------------------
    # Data
    # ---------------------------------------------------------------------
    async def async_get_accounts(self) -> dict:
        """Fetch account balance and the list of accessible laundries."""
        return await self._async_get(ACCOUNTS_URL)

    async def async_get_laundry_states(self, laundry_number: str | int) -> dict:
        """Fetch the live machine states for a single laundry."""
        url = LAUNDRY_STATES_URL.format(laundry_number=laundry_number)
        return await self._async_get(url)

    async def async_get_transactions(
        self, date_from: str, date_to: str
    ) -> dict:
        """Fetch transaction history between two ``YYYY-MM-DD-HH`` timestamps."""
        params = {"dateFrom": date_from, "dateTo": date_to}
        return await self._async_get(TRANSACTIONS_URL, params=params)

    async def _async_get(
        self, url: str, *, params: dict[str, str] | None = None
    ) -> dict:
        """Perform an authenticated GET and return the decoded JSON body.

        Assumes a valid access token; callers should invoke
        :meth:`async_ensure_token` beforehand.
        """
        if not self._access_token:
            raise MieleLogicAuthError("No access token available")

        headers = {
            "Accept": "application/json, text/plain, */*",
            "Authorization": f"Bearer {self._access_token}",
        }
        try:
            async with async_timeout.timeout(REQUEST_TIMEOUT):
                async with self._session.get(
                    url, headers=headers, params=params
                ) as resp:
                    if resp.status in (401, 403):
                        raise MieleLogicAuthError("Access token was rejected by API")
                    resp.raise_for_status()
                    return await resp.json()
        except MieleLogicAuthError:
            raise
        except aiohttp.ClientResponseError as err:
            raise MieleLogicConnectionError(
                f"Unexpected response from {url}: {err.status}"
            ) from err
        except (aiohttp.ClientError, TimeoutError) as err:
            raise MieleLogicConnectionError(
                f"Could not reach {url}: {err}"
            ) from err
