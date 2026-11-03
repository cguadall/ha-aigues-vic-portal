"""Parse server-rendered pages of the Aigües Vic virtual office."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import date, datetime
from html.parser import HTMLParser
from typing import Final

from .exceptions import ParseError
from .models import (
    BilledConsumption,
    ContractCard,
    ContractOption,
    DailyConsumption,
    HourlyConsumption,
)

MAX_HTML_DEPTH: Final = 256
_VOID_TAGS = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta"}
    | {"param", "source", "track", "wbr"}
)
_SKIP_TEXT_TAGS = frozenset({"script", "style", "template"})
_CONTRACT_ID = re.compile(r"\d{1,20}")
_OPTION_LABEL = re.compile(r"^\s*(?P<id>\S+)\s*(?:\((?P<alias>.*)\))?\s*$")
_METER = re.compile(r"^(?:(?P<calibre>\d+)\s*mm\s*-\s*)?(?P<serial>\S+)$")
_INACTIVE_MARKERS = ("donat d'alta", "dado de alta")


def is_valid_contract_id(contract_id: str) -> bool:
    """Return whether a contract identifier has the expected numeric format."""
    return _CONTRACT_ID.fullmatch(contract_id) is not None


@dataclass(eq=False, slots=True)
class Element:
    """A minimal HTML element tree node."""

    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list[Element | str] = field(default_factory=list)

    def iter(self, tag: str | None = None) -> Iterator[Element]:
        """Iterate over descendants in document order."""
        stack: list[Iterator[Element | str]] = [iter(self.children)]
        while stack:
            for child in stack[-1]:
                if isinstance(child, Element):
                    if tag is None or child.tag == tag:
                        yield child
                    stack.append(iter(child.children))
                    break
            else:
                stack.pop()

    def find(
        self, tag: str | None = None, predicate: Callable[[Element], bool] | None = None
    ) -> Element | None:
        """Return the first matching descendant."""
        return next(
            (e for e in self.iter(tag) if predicate is None or predicate(e)), None
        )

    def find_id(self, element_id: str) -> Element | None:
        """Return the descendant with the given id."""
        return self.find(predicate=lambda e: e.attrs.get("id") == element_id)

    def has_class(self, name: str) -> bool:
        """Return whether the element has a CSS class."""
        return name in self.attrs.get("class", "").split()

    def element_children(self) -> list[Element]:
        """Return child elements, ignoring text."""
        return [c for c in self.children if isinstance(c, Element)]

    def text(self) -> str:
        """Return normalized visible text."""
        if self.tag in _SKIP_TEXT_TAGS:
            return ""
        parts: list[str] = []
        stack: list[Iterator[Element | str]] = [iter(self.children)]
        while stack:
            for child in stack[-1]:
                if isinstance(child, str):
                    parts.append(child)
                elif child.tag not in _SKIP_TEXT_TAGS:
                    if child.tag in ("br", "td", "th", "p", "div", "li"):
                        parts.append(" ")
                    stack.append(iter(child.children))
                    break
            else:
                stack.pop()
        return " ".join("".join(parts).split())


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Element("#document")
        self._stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if len(self._stack) >= MAX_HTML_DEPTH:
            raise ParseError("HTML nesting too deep")
        element = Element(tag, {k: v or "" for k, v in attrs})
        self._stack[-1].children.append(element)
        if tag not in _VOID_TAGS:
            self._stack.append(element)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._stack[-1].children.append(Element(tag, {k: v or "" for k, v in attrs}))

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == tag:
                del self._stack[index:]
                return

    def handle_data(self, data: str) -> None:
        self._stack[-1].children.append(data)


def parse_html(html: str) -> Element:
    """Parse an HTML document into an element tree."""
    builder = _TreeBuilder()
    builder.feed(html)
    builder.close()
    return builder.root


def is_login_page(root: Element) -> bool:
    """Return whether the page is the login form."""
    return root.find("form", lambda e: e.attrs.get("id") == "login") is not None


def form_fields(root: Element, form_id: str) -> dict[str, str]:
    """Return the hidden input values of a form."""
    form = root.find("form", lambda e: e.attrs.get("id") == form_id)
    if form is None:
        raise ParseError(f"Form {form_id} not found")
    return {
        e.attrs["name"]: e.attrs.get("value", "")
        for e in form.iter("input")
        if e.attrs.get("type") == "hidden" and "name" in e.attrs
    }


def csrf_token(root: Element) -> str | None:
    """Return the CSRF token embedded in the page, if any."""
    token = root.find("input", lambda e: e.attrs.get("name") == "_token")
    if token is not None and token.attrs.get("value"):
        return token.attrs["value"]
    meta = root.find("meta", lambda e: e.attrs.get("name") == "csrf-token")
    return meta.attrs.get("content") or None if meta is not None else None


def contracts(root: Element) -> tuple[list[ContractOption], str | None]:
    """Return the contracts in the selector and the selected one."""
    select = root.find_id("contrato_activo")
    if select is None:
        return [], None
    options: list[ContractOption] = []
    selected: str | None = None
    for option in select.iter("option"):
        contract_id = option.attrs.get("value", "").strip()
        if not is_valid_contract_id(contract_id):
            continue
        match = _OPTION_LABEL.match(option.text())
        alias = (match.group("alias") or "").strip() if match else ""
        options.append(ContractOption(contract_id, alias or None))
        if "selected" in option.attrs:
            selected = contract_id
    return options, selected


def contract_list(root: Element) -> list[ContractOption]:
    """Return the contracts of the ``/abonat/contractes`` table with their state."""
    headers, rows = _table(root, "table-registros")
    id_column = _column(headers, lambda h: "contract" in h or "contrato" in h)
    state_column = _column(headers, lambda h: h in ("estat", "estado"))
    alias_column = _optional_column(headers, lambda h: h in ("alias", "àlies"))
    result: list[ContractOption] = []
    for row in rows:
        contract_id = row[id_column].text.strip()
        if not is_valid_contract_id(contract_id):
            continue
        alias = row[alias_column].text.strip() if alias_column is not None else ""
        state = row[state_column].order or row[state_column].text
        result.append(
            ContractOption(
                contract_id, alias or None, active=state.strip().casefold() == "alta"
            )
        )
    return result


def contract_card(root: Element) -> ContractCard:
    """Return the labelled contract details."""
    values: dict[str, str] = {}
    for parent in root.iter():
        children = parent.element_children()
        for label, value in zip(children, children[1:], strict=False):
            if label.tag == "label" and label.has_class("control-label-contracte"):
                values[label.text().casefold()] = value.text()
    meter = _METER.match(values.get("comptador", values.get("contador", "")))
    return ContractCard(
        meter_serial=meter.group("serial") if meter else None,
        meter_calibre_mm=int(meter.group("calibre"))
        if meter and meter.group("calibre")
        else None,
        address=values.get("adreça subministrament")
        or values.get("dirección suministro"),
        holder=values.get("titular del contracte")
        or values.get("titular del contrato"),
    )


def is_inactive_contract(root: Element) -> bool:
    """Return whether the portal reports the active contract as not active."""
    for alert in root.iter("div"):
        if alert.has_class("alert-warning"):
            text = alert.text().casefold().replace("’", "'")
            if any(marker in text for marker in _INACTIVE_MARKERS):
                return True
    return False


@dataclass(frozen=True, slots=True)
class _Cell:
    text: str
    order: str | None


def _table(root: Element, table_id: str) -> tuple[list[str], list[list[_Cell]]]:
    table = root.find("table", lambda e: e.attrs.get("id") == table_id)
    if table is None:
        raise ParseError(f"Table {table_id} not found")
    headers = [th.text().casefold() for th in table.iter("th")]
    rows: list[list[_Cell]] = []
    for row in table.iter("tr"):
        cells = [
            _Cell(td.text(), td.attrs.get("data-order"))
            for td in row.element_children()
            if td.tag == "td"
        ]
        if cells and len(cells) == len(headers):
            rows.append(cells)
    return headers, rows


def _range_table(root: Element, table_id: str) -> tuple[list[str], list[list[_Cell]]]:
    """Return a consumption table, empty if the range has no data.

    The portal leaves the table out of the page when the requested range has no
    consumption, but the date range form is still there.
    """
    try:
        return _table(root, table_id)
    except ParseError:
        has_range_form = any(
            field.attrs.get("name") == "fecha_inicio" for field in root.iter("input")
        )
        has_error = any(div.has_class("alert-danger") for div in root.iter("div"))
        if has_range_form and not has_error:
            return [], []
        raise


def _column(headers: list[str], *matchers: Callable[[str], bool]) -> int:
    for index, header in enumerate(headers):
        if any(matcher(header) for matcher in matchers):
            return index
    raise ParseError("Expected column not found")


def _optional_column(headers: list[str], matcher: Callable[[str], bool]) -> int | None:
    try:
        return _column(headers, matcher)
    except ParseError:
        return None


def _is_date(header: str) -> bool:
    return header.startswith(("data", "fecha"))


def _is_meter(header: str) -> bool:
    return header in ("comptador", "contador")


def _is_reading(header: str) -> bool:
    return header == "lectura"


def _is_m3(header: str) -> bool:
    return "m3" in header.replace(" ", "")


def _is_days(header: str) -> bool:
    return "dies" in header or "días" in header


def _is_litres(header: str) -> bool:
    return "litre" in header or "litro" in header


def _is_incident(header: str) -> bool:
    return header.startswith("incid")


def _local_number(text: str) -> float | None:
    cleaned = text.replace("\xa0", "").replace(" ", "").replace(".", "")
    if not cleaned:
        return None
    try:
        return float(cleaned.replace(",", "."))
    except ValueError as err:
        raise ParseError("Invalid number") from err


def _number(cell: _Cell) -> float:
    if cell.order:
        try:
            return float(cell.order)
        except ValueError as err:
            raise ParseError("Invalid number") from err
    value = _local_number(cell.text)
    if value is None:
        raise ParseError("Missing number")
    return value


def _integer_text(cell: _Cell) -> int | None:
    value = _local_number(cell.text)
    return None if value is None else int(value)


def _date(cell: _Cell) -> date:
    for value, fmt in ((cell.order, "%Y-%m-%d"), (cell.text, "%d/%m/%Y")):
        if value:
            try:
                return datetime.strptime(value[:10], fmt).date()
            except ValueError:
                continue
    try:
        return datetime.strptime(cell.text, "%d/%m/%y").date()
    except ValueError as err:
        raise ParseError("Invalid date") from err


def _text_or_none(value: str) -> str | None:
    return value.strip() or None


def daily_consumption(root: Element) -> list[DailyConsumption]:
    """Parse the daily consumption table of the consumption page."""
    headers, rows = _range_table(root, "table-lecturas")
    if not headers:
        return []
    day = _column(headers, _is_date)
    meter = _optional_column(headers, _is_meter)
    reading = _optional_column(headers, _is_reading)
    volume = _column(headers, _is_m3)
    return sorted(
        (
            DailyConsumption(
                day=_date(row[day]),
                meter_serial=_text_or_none(row[meter].text)
                if meter is not None
                else None,
                # The reading cell's data-order holds the consumption, not the reading.
                reading_m3=_integer_text(row[reading]) if reading is not None else None,
                volume_m3=_number(row[volume]),
            )
            for row in rows
        ),
        key=lambda item: item.day,
    )


def billed_consumption(root: Element) -> list[BilledConsumption]:
    """Parse the billed consumption table of the consumption page."""
    headers, rows = _table(root, "table-consumos")
    day = _column(headers, _is_date)
    days = _optional_column(headers, _is_days)
    reading = _optional_column(headers, _is_reading)
    volume = _column(headers, _is_m3)
    incident = _optional_column(headers, _is_incident)
    return sorted(
        (
            BilledConsumption(
                reading_date=_date(row[day]),
                days=_integer_text(row[days]) if days is not None else None,
                reading_m3=_integer_text(row[reading]) if reading is not None else None,
                volume_m3=_number(row[volume]),
                incident=_text_or_none(row[incident].text)
                if incident is not None
                else None,
            )
            for row in rows
        ),
        key=lambda item: item.reading_date,
    )


def hourly_consumption(root: Element) -> list[HourlyConsumption]:
    """Parse the hourly consumption table, keeping the portal order."""
    headers, rows = _range_table(root, "table-lecturas")
    if not headers:
        return []
    start = _column(headers, _is_date)
    meter = _optional_column(headers, _is_meter)
    volume = _column(headers, _is_litres)
    result: list[HourlyConsumption] = []
    for row in rows:
        raw = row[start].order or row[start].text
        try:
            moment = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
        except ValueError as err:
            raise ParseError("Invalid hourly timestamp") from err
        result.append(
            HourlyConsumption(
                start=moment,
                meter_serial=_text_or_none(row[meter].text)
                if meter is not None
                else None,
                volume_l=_number(row[volume]),
            )
        )
    return result
