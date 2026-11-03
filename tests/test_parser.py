"""Tests for the portal HTML parser."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime

import pytest

from custom_components.aigues_vic import parser
from custom_components.aigues_vic.exceptions import ParseError
from custom_components.aigues_vic.models import ContractOption, DailyConsumption

from .fake_portal import (
    PAGE,
    billed_row,
    consumption_tables,
    contract_table,
    daily_row,
    hourly_row,
    hourly_table,
    login_page,
    range_form,
)

OPTIONS = (
    '<option value="100000001" selected="selected">100000001 (Casa)</option>'
    '<option value="200000002">200000002</option><option value=""></option>'
    '<option value="bad-id" selected="selected">bad-id</option>'
)


def page(content: str = "", alert: str = "", options: str = OPTIONS) -> parser.Element:
    """Render and parse a portal page."""
    return parser.parse_html(
        PAGE.format(
            token="tok", options=options, meter="X1", content=content, alert=alert
        )
    )


def test_login_page() -> None:
    """The login form, its hidden fields and the token are found."""
    root = parser.parse_html(login_page("abc", error=False))
    assert parser.is_login_page(root)
    assert parser.form_fields(root, "login") == {"_token": "abc", "entorn": "avsa"}
    assert parser.csrf_token(root) == "abc"
    assert not parser.is_login_page(page())
    with pytest.raises(ParseError):
        parser.form_fields(page(), "login")


def test_csrf_meta_fallback() -> None:
    """The CSRF token can come from a meta tag."""
    root = parser.parse_html('<meta name="csrf-token" content="m"><p>x<br>y</p>')
    assert parser.csrf_token(root) == "m"
    assert parser.csrf_token(parser.parse_html("<p></p>")) is None


def test_contract_list() -> None:
    """The contracts page gives every contract with its ALTA/BAIXA state."""
    rows = (
        "<tr><td>100000001</td><td>C. EXEMPLE 1</td><td>Casa</td>"
        '<td data-order="ALTA"><span>ALTA</span></td><td></td></tr>'
        "<tr><td>200000002</td><td>C. EXEMPLE 2</td><td></td>"
        '<td data-order="BAIXA"><span>BAIXA</span></td><td></td></tr>'
        "<tr><td></td><td></td><td></td><td></td><td></td></tr>"
        "<tr><td>../bad</td><td></td><td></td><td>ALTA</td><td></td></tr>"
    )
    root = parser.parse_html(contract_table(rows))
    assert parser.contract_list(root) == [
        ContractOption("100000001", "Casa", active=True),
        ContractOption("200000002", None, active=False),
    ]
    with pytest.raises(ParseError):
        parser.contract_list(parser.parse_html("<p></p>"))


def test_html_depth_limit() -> None:
    """Excessively nested HTML is rejected before exhausting the stack."""
    deep = "<div>" * parser.MAX_HTML_DEPTH + "</div>" * parser.MAX_HTML_DEPTH
    with pytest.raises(ParseError, match="HTML nesting too deep"):
        parser.parse_html(deep)


def test_contracts_and_card() -> None:
    """Contracts, the selected one and the card fields are parsed."""
    root = page()
    options, selected = parser.contracts(root)
    assert options == [
        ContractOption("100000001", "Casa"),
        ContractOption("200000002", None),
    ]
    assert selected == "100000001"
    card = parser.contract_card(root)
    assert card.meter_serial == "X1"
    assert card.meter_calibre_mm == 15
    assert card.address == "C. EXEMPLE 1"
    assert card.holder == "COGNOM COGNOM, NOM"
    assert parser.contracts(parser.parse_html("<p></p>")) == ([], None)
    empty = parser.contract_card(parser.parse_html("<p></p>"))
    assert empty.meter_serial is None
    assert empty.meter_calibre_mm is None


def test_inactive_contract() -> None:
    """The inactive contract warning is detected in Catalan and Spanish."""
    assert not parser.is_inactive_contract(page())
    for text in ("no està donat d’alta", "no está dado de alta"):
        alert = f'<div class="alert alert-warning">Aquest contracte {text}</div>'
        assert parser.is_inactive_contract(page(alert=alert))
    other = '<div class="alert alert-warning">Manteniment</div>'
    assert not parser.is_inactive_contract(page(alert=other))


def test_consumption_tables() -> None:
    """Daily and billed rows are parsed, sorted and the reading bug ignored."""
    daily = daily_row(date(2026, 1, 2), "X1", 1234, 0.25) + daily_row(
        date(2026, 1, 1), "X1", 1233, 0.1
    )
    billed = billed_row(date(2025, 12, 31), 92, 1200, 21, "") + billed_row(
        date(2025, 9, 30), None, 1179, 0, "Lectura inicial"
    )
    root = page(consumption_tables(daily + "<tr><td>bad</td></tr>", billed))
    rows = parser.daily_consumption(root)
    assert [(r.day, r.reading_m3, r.volume_m3, r.meter_serial) for r in rows] == [
        (date(2026, 1, 1), 1233, 0.1, "X1"),
        (date(2026, 1, 2), 1234, 0.25, "X1"),
    ]
    bills = parser.billed_consumption(root)
    assert [
        (b.reading_date, b.days, b.reading_m3, b.volume_m3, b.incident) for b in bills
    ] == [
        (date(2025, 9, 30), None, 1179, 0.0, "Lectura inicial"),
        (date(2025, 12, 31), 92, 1200, 21.0, None),
    ]


def test_spanish_text_fallback() -> None:
    """Spanish headers and text values without data-order are supported."""
    html = """<table id="table-lecturas"><tr><th>Fecha de lectura</th>
    <th>Contador</th><th>Lectura</th><th>Consumo (m3)</th></tr>
    <tr><td>02/01/2026</td><td></td><td>1.234</td><td>1.234,5</td></tr></table>"""
    (row,) = parser.daily_consumption(parser.parse_html(html))
    assert row == DailyConsumption(date(2026, 1, 2), None, 1234, 1234.5)


def test_minimal_tables() -> None:
    """Optional columns may be missing."""
    html = """<table id="table-lecturas"><tr><th>Data</th><th>Consum (m3)</th></tr>
    <tr><td>02/01/26</td><td>1</td></tr></table>
    <table id="table-consumos"><tr><th>Data</th><th>Consum (m3)</th></tr>
    <tr><td>02/01/26</td><td>3</td></tr></table>"""
    root = parser.parse_html(html)
    assert parser.daily_consumption(root)[0].reading_m3 is None
    bill = parser.billed_consumption(root)[0]
    assert (bill.days, bill.reading_m3, bill.incident) == (None, None, None)


@pytest.mark.parametrize(
    "html",
    [
        "<p></p>",
        '<table id="table-lecturas"><tr><th>Altres</th></tr></table>',
        '<table id="table-lecturas"><tr><th>Data</th><th>m3</th></tr>'
        "<tr><td>32/01/2026</td><td>1</td></tr></table>",
        '<table id="table-lecturas"><tr><th>Data</th><th>m3</th></tr>'
        "<tr><td>01/01/2026</td><td></td></tr></table>",
        '<table id="table-lecturas"><tr><th>Data</th><th>m3</th></tr>'
        '<tr><td>01/01/2026</td><td data-order="x">1</td></tr></table>',
    ],
)
def test_daily_errors(html: str) -> None:
    """Unexpected markup raises a parse error."""
    with pytest.raises(ParseError):
        parser.daily_consumption(parser.parse_html(html))


def test_hourly_table() -> None:
    """Hourly rows keep the portal order."""
    rows = hourly_row("X1", datetime(2026, 1, 1, 1), 2.5) + hourly_row(
        "X1", datetime(2026, 1, 1, 0), 0
    )
    result = parser.hourly_consumption(page(hourly_table(rows)))
    assert [(r.start.hour, r.volume_l, r.meter_serial) for r in result] == [
        (1, 2.5, "X1"),
        (0, 0.0, "X1"),
    ]
    bad = """<table id="table-lecturas"><tr><th>Data</th><th>Litres</th></tr>
    <tr><td>ahir</td><td>1</td></tr></table>"""
    with pytest.raises(ParseError):
        parser.hourly_consumption(parser.parse_html(bad))
    minimal = """<table id="table-lecturas"><tr><th>Data</th><th>Litres</th></tr>
    <tr><td>2026-01-01 05:00:00</td><td>1,5</td></tr></table>"""
    assert parser.hourly_consumption(parser.parse_html(minimal))[0].meter_serial is None


@pytest.mark.parametrize("parse", [parser.daily_consumption, parser.hourly_consumption])
def test_range_without_table_is_empty(
    parse: Callable[[parser.Element], list[object]],
) -> None:
    """A consumption page without a table has no data for the range."""
    assert parse(page(range_form("consums"))) == []
    with pytest.raises(ParseError):
        parse(page("<p>Manteniment</p>"))
    error = '<div class="alert alert-danger">Error</div>'
    with pytest.raises(ParseError):
        parse(page(error + range_form("consums")))
