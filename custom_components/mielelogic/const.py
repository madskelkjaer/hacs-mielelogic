"""Constants for the MieleLogic integration."""

from __future__ import annotations

import logging
from datetime import timedelta

# Core integration identity
DOMAIN = "mielelogic"

# Logger shared across the integration
LOGGER = logging.getLogger(__package__)

# Platforms this integration forwards its config entry to
PLATFORMS = ["sensor"]

# API endpoints. Token/auth lives on the "sec" host, data on the "api" host.
SEC_BASE = "https://sec.mielelogic.com/v7"
API_BASE = "https://api.mielelogic.com/v7"

TOKEN_URL = f"{SEC_BASE}/token"
ACCOUNTS_URL = f"{API_BASE}/accounts"
TRANSACTIONS_URL = f"{API_BASE}/accounts/transactions"

# Per-laundry live machine states. Formatted with the laundry number.
LAUNDRY_STATES_URL = (
    API_BASE + "/Country/DA/Laundry/{laundry_number}/laundrystates?language=da"
)

# How many days of transaction history to request.
TRANSACTION_HISTORY_DAYS = 7

# OAuth2 grant types
GRANT_TYPE_PASSWORD = "password"
GRANT_TYPE_REFRESH = "refresh_token"

# Public client id used by the web app (captured from the token request).
CLIENT_ID = "YV1ZAQ7BTE9IT2ZBZXLJ"

# OAuth scope == country code. "DA" = Denmark; this also matches the country
# segment in the laundrystates URL.
DEFAULT_SCOPE = "DA"

# How often the coordinator polls the API
UPDATE_INTERVAL = timedelta(minutes=1)

# Access tokens are valid for ~15 minutes. Refresh a little early to avoid
# racing against expiry mid-request.
TOKEN_LIFETIME = timedelta(minutes=15)
TOKEN_REFRESH_MARGIN = timedelta(seconds=60)

# Default timeout for outbound HTTP calls (seconds)
REQUEST_TIMEOUT = 30

# Config entry / token storage keys
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_ACCESS_TOKEN = "access_token"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_EXPIRES_AT = "expires_at"
