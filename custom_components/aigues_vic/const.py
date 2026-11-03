"""Constants for the Aigües Vic integration."""

from datetime import date
from typing import Final
from zoneinfo import ZoneInfo

DOMAIN: Final = "aigues_vic"
PORTAL_URL: Final = "https://oficina.aiguesvic.com"
API_URL: Final = "https://api-oficina.aiguesvic.com"
PORTAL_TIMEZONE: Final = ZoneInfo("Europe/Madrid")

CONF_IMPORT_HISTORY: Final = "import_history"
CONF_PRICE: Final = "price_per_m3"

DAILY_UPDATE_HOUR: Final = 6
HISTORY_START: Final = date(2016, 1, 1)
INITIAL_IMPORT_DAYS: Final = 30
REIMPORT_DAYS: Final = 14
