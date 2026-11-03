# Aigües Vic Virtual Office Analysis

[Català](portal-analysis.md)

Analysis of the Aigües de Vic (AVSA) virtual office and REST API used by the Home Assistant integration to retrieve water consumption data and meter readings.

## Architecture

- **Web Portal (SPA):** `https://oficina.aiguesvic.com`
- **REST API Host:** `https://api-oficina.aiguesvic.com`
- **Format:** JSON UTF-8
- **Authentication:** JWT (JSON Web Tokens) via the standard header `Authorization: Bearer <access_token>`

## Authentication and Session Management

Authentication is handled directly against the REST API using JWT access and refresh token pairs:

```text
POST /accounts/auth/login/       Obtain access and refresh tokens
POST /accounts/refresh-token/    Refresh an expired access token
POST /accounts/logout/           Close session and revoke refresh token
```

### 1. Sign In (`POST /accounts/auth/login/`)
Sends credentials as JSON:
```json
{
  "email": "user@example.com",
  "password": "secret_password"
}
```
Returns HTTP 200 with the token pair:
```json
{
  "access": "eyJhbGciOi...",
  "refresh": "eyJhbGciOi..."
}
```
If credentials are invalid, the API responds with HTTP 401:
```json
{
  "detail": "No active account found with the given credentials."
}
```

### 2. Token Refresh (`POST /accounts/refresh-token/`)
When the access token expires, API requests return HTTP 401. The integration client transparently refreshes the token without prompting for the user's password:
```json
{
  "refresh": "eyJhbGciOi..."
}
```
Returns a fresh access token:
```json
{
  "access": "eyJhbGciOi..."
}
```

### 3. Sign Out (`POST /accounts/logout/`)
Revokes the refresh token on the server:
```json
{
  "refresh": "eyJhbGciOi..."
}
```

## Data Endpoints

All authenticated requests require the header:
```http
Authorization: Bearer <access_token>
```

### Contract List (`GET /contracts/`)
Returns all contracts associated with the user account:
```json
[
  {
    "uuid": "4bf0e4b8-2a12-42c2-80dc-3ec4db5f9a65",
    "contract_number": "100000001",
    "alias": "Home",
    "address": "CARRER MAJOR 1, 08500 VIC",
    "cups": "ES0000000000000000AA",
    "contract_status": {
      "code": "ACTIVE",
      "description": "Alta"
    }
  }
]
```
- The integration only processes contracts with code `ACTIVE`.
- `contract_number` is preserved as the public identifier for Home Assistant entities and long-term statistics (`aigues_vic:water_<contract_id>`).
- `uuid` is used internally for downstream API requests.

### Technical & Billing Data (`GET /contracts/billing-data/{uuid}/`)
Returns supply technical details:
```json
{
  "holder_name": "JOHN DOE",
  "meter_serial_number": "A12BC34567D",
  "caliber": 15,
  "payment_method": "DIRECT_DEBIT"
}
```

### Daily Consumption (`GET /consumptions/daily-consumptions/`)
Query string parameters:
- `contract_uuid`: Contract UUID string.
- `initial_date`: Start date in `YYYY-MM-DD` format.
- `end_date`: End date in `YYYY-MM-DD` format.

Example response:
```json
[
  {
    "timestamp": "2026-10-30T23:00:00",
    "consumption": 235,
    "reading": 1014.52
  }
]
```
- `consumption`: Daily volume in litres.
- `reading`: Cumulative meter index reading in m³ (may include decimals).
- `timestamp`: End-of-day local timestamp.

### Hourly Consumption (`GET /consumptions/hourly-consumptions/`)
Query string parameters:
- `contract_uuid`: Contract UUID string.
- `initial_date`: Start date in `YYYY-MM-DD` format.
- `end_date`: End date in `YYYY-MM-DD` format.

Example response:
```json
[
  {
    "timestamp": "2026-10-30T00:00:00",
    "consumption": 12.5
  },
  {
    "timestamp": "2026-10-30T01:00:00",
    "consumption": 0.0
  }
]
```
- `consumption`: Litres consumed during the one-hour interval.
- `timestamp`: Naive local ISO timestamp (`YYYY-MM-DDTHH:MM:SS`).
- Supports 25-hour days on the October DST fallback day.

### Billed Consumption (`GET /consumptions/billed-consumptions/`)
Query string parameters:
- `contract_uuid`: Contract UUID string.

Example response:
```json
[
  {
    "initial_date": "2026-07-01",
    "end_date": "2026-09-30",
    "consumption": 21,
    "reading": 1014
  }
]
```
- `consumption`: Total billed volume in m³.
- `reading`: Meter index reading in m³ on the end date.

## Home Assistant Integration Architecture

1. **Identifier Stability & Backwards Compatibility:**
   - The integration never uses internal API UUIDs as public entity keys, preventing historical data fragmentation in Home Assistant's recorder database. The commercial `contract_number` is always used.
2. **Chunked Queries:**
   - History imports are requested in 31-day chunks for hourly data and 92-day chunks for daily data to prevent server timeouts and payload limits.
3. **Caching & Efficient Restarts:**
   - When Home Assistant restarts, cached data fetched after the last scheduled poll is reused, avoiding redundant network requests.
4. **Error Handling & Resilience:**
   - On HTTP 401 responses, the client transparently refreshes the JWT token via `/accounts/refresh-token/` and retries the request.
   - Network errors trigger automatic retries with exponential backoff (30 min, 1 h, 2 h, 4 h) while preserving the last valid state.
