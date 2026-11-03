"""A stateful fake of the Aigües Vic virtual office with synthetic data.

Serves the modern REST API used by the integration and retains HTML helpers
for legacy parser unit tests.
"""

from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from aiohttp import web

EMAIL = "user@example.invalid"
PASSWORD = "correct horse battery staple"

type Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]


@dataclass
class FakeContract:
    """A synthetic contract."""

    contract_id: str
    alias: str | None = None
    active: bool = True
    meter: str = "A12BC34567D"
    meter_caliber: int = 15
    address: str = "Carrer Major, 1"
    holder: str = "Persona Exemple"
    uuid: str | None = None
    daily: dict[date, tuple[int, float]] = field(default_factory=dict)
    billed: list[tuple[date, int | None, int, int, str]] = field(default_factory=list)
    hourly: list[tuple[datetime, float]] = field(default_factory=list)


class FakePortal:
    """Serve the subset of the portal REST API used by the integration."""

    def __init__(self, contracts: list[FakeContract], today: date) -> None:
        """Initialize the fake portal."""
        self.contracts = {c.contract_id: c for c in contracts}
        self.today = today
        self.password = PASSWORD
        self.access_tokens: set[str] = set()
        self.refresh_tokens: set[str] = set()
        self.requests: list[tuple[str, str, dict[str, str]]] = []
        self.expire_sessions = False
        self.fail_status: int | None = None
        self.drop_login_token = False
        self.lose_session_on_consumption = False
        self.drop_contract_table = False
        self.omit_empty_tables = False
        self.odd_before: date | None = None
        self.odd_status: int | None = None

        self.app = web.Application(middlewares=[self._middleware])
        self.app.add_routes(
            [
                web.post("/accounts/auth/login/", self._login),
                web.post("/accounts/refresh-token/", self._refresh),
                web.post("/accounts/logout/", self._logout),
                web.get("/contracts/", self._contracts),
                web.get("/contracts/billing-data/{uuid}/", self._billing_data),
                web.get("/consumptions/daily-consumptions/", self._daily),
                web.get("/consumptions/hourly-consumptions/", self._hourly),
                web.get("/consumptions/billed-consumptions/", self._billed),
            ]
        )

    def paths(self) -> list[str]:
        """Return the requested method and path pairs."""
        return [f"{method} {path}" for method, path, _ in self.requests]

    @web.middleware
    async def _middleware(
        self, request: web.Request, handler: Handler
    ) -> web.StreamResponse:
        params: dict[str, str] = dict(request.query)
        if (
            request.method == "POST"
            and request.can_read_body
            and request.content_type == "application/json"
        ):
            try:
                body = await request.json()
                if isinstance(body, dict):
                    params.update({k: str(v) for k, v in body.items()})
            except Exception:
                pass
        self.requests.append((request.method, request.path, params))

        if self.fail_status:
            return web.Response(status=self.fail_status)

        if self.expire_sessions:
            self.access_tokens.clear()
            self.refresh_tokens.clear()
            self.expire_sessions = False

        if not request.path.startswith(
            "/accounts/auth/login"
        ) and not request.path.startswith("/accounts/refresh-token"):
            auth_header = request.headers.get("Authorization", "")
            token = auth_header.removeprefix("Bearer ").strip()
            if not token or token not in self.access_tokens:
                return web.json_response(
                    {"detail": "Given token not valid for any token type"},
                    status=401,
                )

        return await handler(request)

    async def _login(self, request: web.Request) -> web.Response:
        if self.drop_login_token:
            # Simulate unexpected/malformed login response
            return web.json_response({"detail": "Missing access token"})
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"detail": "Invalid JSON"}, status=400)
        email = body.get("email")
        password = body.get("password")
        if email != EMAIL or password != self.password:
            return web.json_response(
                {"detail": "No active account found with the given credentials"},
                status=401,
            )
        access = f"access-{secrets.token_hex(16)}"
        refresh = f"refresh-{secrets.token_hex(16)}"
        self.access_tokens.add(access)
        self.refresh_tokens.add(refresh)
        return web.json_response({"access": access, "refresh": refresh})

    async def _refresh(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"detail": "Invalid JSON"}, status=400)
        refresh = body.get("refresh")
        if not refresh or refresh not in self.refresh_tokens:
            return web.json_response(
                {"detail": "Token is invalid or expired"},
                status=401,
            )
        access = f"access-{secrets.token_hex(16)}"
        self.access_tokens.add(access)
        return web.json_response({"access": access})

    async def _logout(self, request: web.Request) -> web.Response:
        auth_header = request.headers.get("Authorization", "")
        token = auth_header.removeprefix("Bearer ").strip()
        self.access_tokens.discard(token)
        return web.json_response({"detail": "Logged out"})

    async def _contracts(self, request: web.Request) -> web.Response:
        if self.drop_contract_table:
            return web.json_response(
                {"contracts": [], "invitations": [], "supply_cuts": []}
            )
        return web.json_response(
            {
                "contracts": [
                    {
                        "id": c.uuid,
                        "contract_number": c.contract_id,
                        "contract_status": {
                            "code": "ACTIVE" if c.active else "INACTIVE",
                            "description": "Alta" if c.active else "Baixa",
                        },
                        "alias": c.alias,
                        "supply_point_address": c.address,
                        "meter_serial_number": c.meter,
                        "meter_caliber": c.meter_caliber,
                        "smart_metering": True,
                    }
                    for c in self.contracts.values()
                ],
                "invitations": [],
                "supply_cuts": [],
            }
        )

    async def _billing_data(self, request: web.Request) -> web.Response:
        uuid_val = request.match_info["uuid"]
        for c in self.contracts.values():
            if uuid_val in (c.uuid, f"uuid-{c.contract_id}", c.contract_id):
                return web.json_response(
                    {
                        "holder_name": c.holder,
                        "tenant_name": c.holder,
                        "address": c.address,
                        "nif": "00000000T",
                        "city": "VIC",
                        "postal_code": "08500",
                    }
                )
        return web.json_response({"detail": "Not found"}, status=404)

    async def _daily(self, request: web.Request) -> web.Response:
        if self.lose_session_on_consumption:
            self.access_tokens.clear()
            return web.json_response({"detail": "Token expired"}, status=401)
        contract_number = request.query.get("contract_number", "")
        contract = self.contracts.get(contract_number)
        if not contract or not contract.active:
            return web.json_response([])
        start = date.fromisoformat(request.query["initial_date"])
        end = date.fromisoformat(request.query["end_date"])
        if self.odd_before is not None and end < self.odd_before:
            if self.odd_status is not None:
                return web.Response(status=self.odd_status)
            return web.Response(text="<html>Error</html>", content_type="text/html")
        items = [
            {
                "meter": contract.meter,
                "reading": reading,
                "consumption": round(volume * 1000, 2),
                "timestamp": f"{day.isoformat()}T23:00:00",
                "origin": "calculated",
                "estimated": False,
            }
            for day, (reading, volume) in sorted(contract.daily.items())
            if start <= day <= end
        ]
        return web.json_response(items)

    async def _hourly(self, request: web.Request) -> web.Response:
        if self.lose_session_on_consumption:
            self.access_tokens.clear()
            return web.json_response({"detail": "Token expired"}, status=401)
        contract_number = request.query.get("contract_number", "")
        contract = self.contracts.get(contract_number)
        if not contract or not contract.active:
            return web.json_response([])
        start = date.fromisoformat(request.query["initial_date"])
        end = date.fromisoformat(request.query["end_date"])
        if self.odd_before is not None and end < self.odd_before:
            if self.odd_status is not None:
                return web.Response(status=self.odd_status)
            return web.Response(text="<html>Error</html>", content_type="text/html")
        items = [
            {
                "meter": contract.meter,
                "reading": None,
                "consumption": litres,
                "timestamp": moment.isoformat(),
                "origin": None,
                "estimated": False,
            }
            for moment, litres in contract.hourly
            if start <= moment.date() <= end
        ]
        return web.json_response(items)

    async def _billed(self, request: web.Request) -> web.Response:
        contract_number = request.query.get("contract_number", "")
        contract = self.contracts.get(contract_number)
        if not contract or not contract.active:
            return web.json_response([])
        items = [
            {
                "meter": contract.meter,
                "reading": reading,
                "consumption": volume,
                "timestamp": day.isoformat(),
                "origin": incident or "LF",
                "estimated": False,
            }
            for day, _days, reading, volume, incident in contract.billed
        ]
        return web.json_response(items)


def sample_contract(
    contract_id: str,
    today: date,
    *,
    alias: str | None = None,
    days: int = 60,
    hourly: bool = True,
    start_reading: int = 1000,
) -> FakeContract:
    """Build a contract with deterministic synthetic consumption up to today."""
    contract = FakeContract(contract_id, alias)
    total = float(start_reading)
    for offset in range(days, -1, -1):
        day = today - timedelta(days=offset)
        litres = [
            round(5 + (hour * 7 + offset * 3) % 20 * 0.5, 2) for hour in range(24)
        ]
        if day == today:
            litres = litres[:3]
        volume = round(sum(litres) / 1000, 3)
        total += volume
        contract.daily[day] = (int(total), volume)
        if hourly:
            contract.hourly.extend(
                (datetime.combine(day, datetime.min.time()) + timedelta(hours=h), v)
                for h, v in enumerate(litres)
            )
    contract.billed = [
        (today - timedelta(days=200), None, start_reading - 30, 0, "Lectura inicial"),
        (today - timedelta(days=108), 92, start_reading - 12, 18, ""),
        (today - timedelta(days=16), 92, start_reading + 9, 21, ""),
    ]
    return contract


# ---------------------------------------------------------------------------
# Legacy HTML template helpers preserved for test_parser.py
# ---------------------------------------------------------------------------

LIST_PAGE = """<!DOCTYPE html><html lang="ca"><head><meta charset="utf-8">
<title>Oficina Virtual</title></head>
<body class="nav-md"><div class="right_col" role="main">{content}</div></body></html>"""

INACTIVE_ALERT = (
    '<div class="alert alert-warning"><p>No té cap contracte donat d\'alta.</p></div>'
)


def login_page(token: str, *, error: bool) -> str:
    """Render the login page."""
    message = (
        '<span class="help-block">Dades d&#039;accés incorrectes.</span>'
        if error
        else ""
    )
    return f"""<!DOCTYPE html><html lang="ca"><head><meta charset="utf-8">
<title>Oficina Virtual</title><script src="/js/jquery.cookie.js"></script>
<script id="Cookiebot" src="https://consent.example.invalid/uc.js"
 data-blockingmode="auto" type="text/javascript"></script>
<script>function recordem() {{ $.cookie('remember', 1); }}</script></head><body>
<form id="login" onsubmit="recordem()" class="login100-form validate-form"
 method="post" action="https://oficina.aiguesvic.com/login">
<input type="hidden" name="_token" value="{token}">
<input type="hidden" name="entorn" value="avsa" required/>
<span class="login100-form-title">Benvinguts a <span> l'Oficina Virtual</span></span>
{message}
<div class="form-group"><input type="text" class="form-control-login" name="email"
 value="" placeholder="Adreça electrònica" id="username"></div>
<div class="form-group"><input type="password" class="form-control-login"
 placeholder="Mot de pas" name="password" id="password"></div>
<input type="checkbox" id="remember" class="remember" name="remember"> Recorda'm <br/>
<button class="login100-form-btn submit" type="submit">ACCEDIR </button>
</form></body></html>"""


def contract_table(rows: str) -> str:
    """Render the ``/abonat/contractes`` table."""
    return f"""<table id="table-registros" class="table table-striped dataTable"><thead>
<tr><th data-priority="1" style="max-width:160px;">Núm. Contracte</th>
<th data-priority="2">Adreça subministrament</th><th data-priority="4">Alias</th>
<th data-priority="3" class="text-center" style="max-width:120px;">Estat</th>
<th data-priority="1" class="text-center" style="max-width:120px;"></th></tr></thead>
<tbody>{rows}</tbody></table>"""


PAGE = """<!DOCTYPE html><html lang="ca"><head><meta charset="utf-8">
<title>Oficina Virtual</title></head><body class="nav-md">
<div class="top_nav"><a href="javascript:;" class="user-profile dropdown-toggle">
<img src="" alt=""><span class="hidden-xs">Nom Cognom</span></a></div>
<div class="right_col" role="main">
<form id="form_contrato" method="get" class="form-horizontal"
 action="https://oficina.aiguesvic.com/abonat">
<input type="hidden" name="_token" value="{token}">
<div class="col-lg-6 col-xs-12"><label class="control-label control-label-contracte
 col-md-4 col-xs-12">Núm. Contracte</label><div class="col-md-8 col-xs-12">
<select class="form-control pull-left" id="contrato_activo"
 name="contrato">{options}</select>
</div></div>
<div class="col-lg-6 col-xs-12"><label class="control-label control-label-contracte
 col-md-4 col-xs-12">Comptador</label>
<div class="col-md-8 col-xs-12 pt7"> 15 mm - {meter} </div></div>
<div class="form-group form-group-seleccio m0"><div class="col-lg-6 col-xs-12">
<label class="control-label control-label-contracte col-md-4 col-xs-12">Adreça
 subministrament</label><div class="col-md-8 col-xs-12 pt7"> C. EXEMPLE 1 </div></div>
<div class="col-lg-6 col-xs-12"><label class="control-label control-label-contracte
 col-md-4 col-xs-12">Titular del contracte</label>
<div class="col-md-8 col-xs-12 pt7"> COGNOM COGNOM, NOM </div></div></div>
</form>
{alert}{content}
</div>
<script>var html = '<table id="table-lecturas"><tr><td>x</td></tr></table>';</script>
</body></html>"""


def range_form(path: str) -> str:
    """Render the date range form of a consumption page."""
    return f"""<form method="post" class="form-horizontal"
 action="https://oficina.aiguesvic.com/abonat/{path}">
<input type="hidden" id="fecha_inicio" name="fecha_inicio" value="" required/>
<input type="hidden" id="fecha_fin" name="fecha_fin" value="" required/></form>"""


def consumption_tables(daily: str, billed: str, *, omit_daily: bool = False) -> str:
    """Render the daily and billed tables."""
    daily_table = (
        ""
        if omit_daily
        else f"""<table id="table-lecturas" class="table table-striped dataTable">
<thead><tr>
<th data-priority="1" class="text-center">Data de lectura</th>
<th data-priority="5" class="text-center">Comptador</th>
<th data-priority="4" class="text-center">Període (dies)</th>
<th data-priority="2" class="text-center">Lectura</th>
<th data-priority="3" class="text-center">Consum (m<sup>3</sup>)</th>
<th data-priority="2" class="text-center">Consum (l/dia)</th>
</tr></thead><tbody>{daily}</tbody></table>"""
    )
    return f"""<p><small>Els consums en alguns casos no són els reals.</small></p>
{range_form("consums")}
{daily_table}
<table id="table-consumos" class="table table-striped dataTable"><thead><tr>
<th data-priority="1" class="text-center">Data de lectura</th>
<th data-priority="5" class="text-center">Període (dies)</th>
<th data-priority="2" class="text-center">Lectura</th>
<th data-priority="3" class="text-center">Consum (m<sup>3</sup>)</th>
<th data-priority="4" class="text-center">Consum (l/dia)</th>
<th data-priority="6" class="text-center">Incidència</th>
</tr></thead><tbody>{billed}</tbody></table>"""


def daily_row(day: date, meter: str, reading: int, volume: float) -> str:
    """Render a daily row, including the portal's reading data-order bug."""
    litres = round(volume * 1000)
    shown = f"{volume:.2f}".replace(".", ",")
    reading_text = f"{reading:,}".replace(",", ".")
    return f"""<tr>
<td data-order="{day.isoformat()}" class="text-center">{day:%d/%m/%Y}</td>
<td class="text-center">{meter}</td><td class="text-center">1</td>
<td data-order="{volume:.3f}" class="text-center">{reading_text}</td>
<td data-order="{volume:.3f}" class="text-center">{shown}</td>
<td data-order="{litres}" class="text-center">{litres}</td></tr>"""


def billed_row(
    day: date, days: int | None, reading: int, volume: int, incident: str
) -> str:
    """Render a billed row."""
    per_day = round(volume * 1000 / days) if days else 0
    return f"""<tr>
<td data-order="{day.isoformat()}" class="text-center">{day:%d/%m/%y}</td>
<td class="text-center">{days if days is not None else ""}</td>
<td class="text-center">{reading}</td>
<td data-order="{volume}" class="text-center">{volume}</td>
<td data-order="{per_day}" class="text-center">{per_day}</td>
<td class="text-center">{incident}</td></tr>"""


def hourly_table(rows: str) -> str:
    """Render the hourly table."""
    return f"""<table id="table-lecturas" class="table dataTable"><thead><tr>
<th data-priority="5" class="text-center">Comptador</th>
<th data-priority="1" class="text-center">Data de lectura</th>
<th data-priority="3" class="text-center">Consum (Litres)</th>
</tr></thead><tbody>{rows}</tbody></table>"""


def hourly_row(meter: str, moment: datetime, litres: float) -> str:
    """Render an hourly row."""
    stamp = f"{moment:%Y-%m-%d %H:%M:%S}"
    shown = f"{litres:.2f}".replace(".", ",")
    return f"""<tr><td class="text-center">{meter}</td>
<td data-order="{stamp}" class="text-center">{stamp}</td>
<td data-order="{litres:.2f}" class="text-center">{shown}</td></tr>"""
