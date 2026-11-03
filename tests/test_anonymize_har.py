"""Tests for the HAR anonymization script, using synthetic captures only."""

import json
import re
from pathlib import Path
from typing import Any

import pytest

from scripts.anonymize_har import ANON_EMAIL, REDACTED, Anonymizer, anonymize, main

PORTAL = "https://oficina.aiguesvic.com"
EMAIL = "jane.roe@mail.test"
PASSWORD = 'S3cr"et~!'
TOKEN = "AbCdEf0123456789AbCdEf0123456789AbCdEf01"
CONTRACT = "1234567"
OTHER_CONTRACT = "7654321"
METER = "A12BC34567D"
HOLDER = "ROE DOE, JANE"
ADDRESS = "C. FALS 12-3-4"
ALIAS = "CASA PLATJA"
USER_NAME = "Jane Roe Doe"
UUID = "0f2c4b1e-9a7d-4c3e-8b2a-1d2e3f4a5b6c"
INVOICE = "12345678Z123456789"

CONTRACT_CARD = f"""
<a href="javascript:;" class="user-profile dropdown-toggle" data-toggle="dropdown">
  <img src="" alt=""><span class="hidden-xs">{USER_NAME}</span></a>
<a href="{PORTAL}/usuari/avisos/{UUID}" title="">Avís</a>
<form id="form_contrato" method="get" action="{PORTAL}/abonat">
<input type="hidden" name="_token" value="{TOKEN}">
<label class="control-label control-label-contracte">Núm. Contracte</label>
<div><select class="form-control" id="contrato_activo" name="contrato">
<option value="{CONTRACT}" selected="selected">{CONTRACT} ({ALIAS})</option>
<option value="{OTHER_CONTRACT}">{OTHER_CONTRACT}</option></select></div>
<label class="control-label control-label-contracte">Comptador</label>
<div class="pt7"> 15 mm - {METER} </div>
<label class="control-label control-label-contracte">Adreça subministrament</label>
<div class="pt7"> {ADDRESS} </div>
<label class="control-label control-label-contracte">Titular del contracte</label>
<div class="pt7"> {HOLDER} </div>
</form>
<a href="{PORTAL}/abonat/factures/pdf/{INVOICE}">PDF</a>
<table id="table-lecturas"><tbody><tr>
<td data-order="2026-09-16">16/09/2026</td><td>{METER}</td><td>1</td>
<td data-order="0.279">798</td><td data-order="0.279">0,28</td>
<td data-order="279">279</td></tr></tbody></table>
<p>Contacte: avsa@aiguesvic.com, {EMAIL}, 612 345 678, 12345678Z,
ES91 2100 0418 4502 0005 1332</p>
"""


def _entry(
    url: str,
    *,
    method: str = "GET",
    text: str | None = None,
    mime: str = "text/html; charset=UTF-8",
    params: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    request: dict[str, Any] = {
        "method": method,
        "url": url,
        "headers": [
            {"name": "Cookie", "value": f"laravel_session=abc; password={PASSWORD}"},
            {"name": "Referer", "value": f"{PORTAL}/abonat/canviar/{CONTRACT}"},
        ],
        "cookies": [
            {"name": "username", "value": EMAIL},
            {"name": "password", "value": PASSWORD},
            {"name": "laravel_session", "value": "session-value"},
        ],
        "queryString": [],
    }
    if params is not None:
        request["postData"] = {
            "mimeType": "application/x-www-form-urlencoded",
            "params": params,
            "text": "&".join(f"{p['name']}={p['value']}" for p in params),
        }
    content: dict[str, Any] = {"mimeType": mime, "size": 0}
    if text is not None:
        content["text"] = text
    return {
        "startedDateTime": "2026-10-15T23:37:00.000+02:00",
        "request": request,
        "response": {
            "status": 200,
            "headers": [{"name": "Set-Cookie", "value": "XSRF-TOKEN=secret"}],
            "cookies": [{"name": "XSRF-TOKEN", "value": "secret"}],
            "content": content,
        },
        "_initiator": {"type": "script"},
    }


def _har(*entries: dict[str, Any]) -> dict[str, Any]:
    return {"log": {"version": "1.2", "entries": list(entries)}}


def _login() -> dict[str, Any]:
    return _entry(
        f"{PORTAL}/login",
        method="POST",
        params=[
            {"name": "_token", "value": TOKEN},
            {"name": "entorn", "value": "avsa"},
            {"name": "email", "value": EMAIL},
            {"name": "password", "value": PASSWORD},
        ],
    )


def _captures() -> list[dict[str, Any]]:
    return [
        _har(
            _login(),
            _entry(f"{PORTAL}/abonat/canviar/{CONTRACT}"),
            _entry("https://www.googletagmanager.com/gtm.js", mime="text/javascript"),
        ),
        _har(
            _entry(f"{PORTAL}/abonat", text=CONTRACT_CARD),
            _entry(f"{PORTAL}/js/app.js", text="var x = 1;", mime="text/javascript"),
        ),
    ]


def _run() -> tuple[list[dict[str, Any]], Anonymizer, str]:
    anonymizer = Anonymizer(key=b"k" * 32)
    results = anonymize(_captures(), anonymizer)
    return results, anonymizer, json.dumps(results, ensure_ascii=False)


def test_secrets_and_identifiers_are_removed() -> None:
    """No original personal value or secret may survive in any form."""
    _, anonymizer, document = _run()

    for value in (
        EMAIL,
        PASSWORD,
        TOKEN,
        CONTRACT,
        OTHER_CONTRACT,
        METER,
        HOLDER,
        ADDRESS,
        ALIAS,
        USER_NAME,
        UUID,
        INVOICE,
        "session-value",
        "XSRF-TOKEN=secret",
        "612 345 678",
        "12345678Z",
        "ES91 2100",
    ):
        assert value.casefold() not in document.casefold(), value
    assert "Jane" not in document
    assert anonymizer.leaks(document) == []


def test_requests_are_redacted() -> None:
    """Headers, cookies and form secrets are replaced, other fields kept."""
    results, _, _ = _run()
    login = results[0]["log"]["entries"][0]
    request = login["request"]

    assert {h["value"] for h in request["headers"] if h["name"] == "Cookie"} == {
        REDACTED
    }
    assert {c["value"] for c in request["cookies"]} == {REDACTED}
    assert login["response"]["headers"][0]["value"] == REDACTED
    params = {p["name"]: p["value"] for p in request["postData"]["params"]}
    assert params == {
        "_token": REDACTED,
        "entorn": "avsa",
        "email": ANON_EMAIL,
        "password": REDACTED,
    }
    assert request["postData"]["text"] == (
        "_token=REDACTED&entorn=avsa&email=user%40example.invalid&password=REDACTED"
    )
    assert "_initiator" not in login


def test_relationships_and_shapes_are_preserved() -> None:
    """The same contract maps to the same pseudonym across captures."""
    results, anonymizer, _ = _run()
    switch_url = results[0]["log"]["entries"][1]["request"]["url"]
    page = results[1]["log"]["entries"][0]["response"]["content"]["text"]
    contract = anonymizer.values[CONTRACT][1]
    meter = anonymizer.values[METER.casefold()][1]

    assert switch_url == f"{PORTAL}/abonat/canviar/{contract}"
    assert re.fullmatch(r"\d{7}", contract)
    assert re.fullmatch(r"[A-Z]\d{2}[A-Z]{2}\d{5}[A-Z]", meter)
    assert f'<option value="{contract}" selected="selected">{contract} (ÀLIES 1)' in (
        page
    )
    assert f"15 mm - {meter}" in page
    assert "COGNOM1 COGNOM, NOM" in page
    assert "ADREÇA EXEMPLE 1" in page
    assert "Nom1 Cognom" in page


def test_consumption_data_is_untouched() -> None:
    """Dates, readings and volumes are needed for parser fixtures."""
    results, _, _ = _run()
    page = results[1]["log"]["entries"][0]["response"]["content"]["text"]

    for value in (
        '<td data-order="2026-09-16">16/09/2026</td>',
        '<td data-order="0.279">798</td>',
        '<td data-order="0.279">0,28</td>',
        '<td data-order="279">279</td>',
        "avsa@aiguesvic.com",
    ):
        assert value in page


def test_third_party_entries_and_static_bodies_are_dropped() -> None:
    """Keep only portal requests and remove bodies that are not analysed."""
    results, anonymizer, _ = _run()
    urls = [e["request"]["url"] for r in results for e in r["log"]["entries"]]
    static = results[1]["log"]["entries"][1]["response"]["content"]

    assert all(url.startswith(PORTAL) for url in urls)
    assert anonymizer.stats["third-party-entry"] == 1
    assert "text" not in static


def test_leak_detection_reports_kinds_only() -> None:
    """The leak check finds values in raw and encoded forms."""
    anonymizer = Anonymizer()
    anonymizer.add("email", "someone@mail.test")
    anonymizer.add("holder", HOLDER)

    assert anonymizer.leaks("someone%40mail.test") == ["email"]
    assert anonymizer.leaks("roe doe, jane") == ["holder", "name-part"]
    assert anonymizer.leaks("consum 0,279 m3") == []


def test_cli_writes_only_after_leak_check(tmp_path: Path) -> None:
    """The command writes outputs and reports counts, not values."""
    sources = []
    for index, capture in enumerate(_captures()):
        path = tmp_path / f"capture [{index}].har"
        path.write_text(json.dumps(capture), encoding="utf-8")
        sources.append(path)
    output = tmp_path / "out"

    assert main([*map(str, sources), "--output-dir", str(output)]) == 0
    written = sorted(output.iterdir())
    assert [p.name for p in written] == ["capture_0.anon.har", "capture_1.anon.har"]
    assert EMAIL not in "".join(p.read_text(encoding="utf-8") for p in written)


def test_cli_refuses_to_write_leaks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A failed leak check leaves no output and does not print values."""
    source = tmp_path / "capture.har"
    source.write_text(json.dumps(_captures()[1]), encoding="utf-8")
    output = tmp_path / "out"
    monkeypatch.setattr(Anonymizer, "scrub", lambda self, text: text)

    assert main([str(source), "--output-dir", str(output)]) == 1
    assert not output.exists()
    error = capsys.readouterr().err
    assert "Leak check failed" in error
    assert HOLDER not in error
