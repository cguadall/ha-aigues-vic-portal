"""Anonymize HAR captures of the Aigües Vic virtual office.

Sensitive values are learned from every input file first and then replaced
consistently, so relationships between requests (for example the contract
selected in one capture and shown in another) survive anonymization.
Identifiers keep their shape (letters stay letters, digits stay digits) to keep
parsers testable. Pseudonyms come from a random per-run key and are not
reversible. Output is only written when a final leak check passes.

Usage:
    python scripts/anonymize_har.py [HAR ...] [--output-dir DIR]

Without arguments it reads ``local/*.har`` and writes ``local/anonymized/``.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import html
import json
import re
import secrets
import sys
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote, quote_plus, unquote_plus, urlencode, urlsplit

PORTAL_HOSTS = frozenset({"oficina.aiguesvic.com", "api-oficina.aiguesvic.com"})
REDACTED = "REDACTED"
ANON_EMAIL = "user@example.invalid"

SENSITIVE_HEADERS = frozenset(
    {
        "authorization",
        "cookie",
        "proxy-authorization",
        "set-cookie",
        "x-csrf-token",
        "x-xsrf-token",
    }
)
SECRET_FIELDS = frozenset({"_token", "password", "token", "remember_token"})
TEXT_MIME_HINTS = ("html", "json", "javascript", "xml", "text/plain")
STATIC_SUFFIXES = (
    ".css",
    ".js",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".ico",
    ".woff",
    ".woff2",
    ".ttf",
    ".map",
    ".pdf",
)

_TOKEN_PATTERNS = (
    re.compile(r'name="_token"\s+value="([^"]+)"'),
    re.compile(r'<meta\s+name="csrf-token"\s+content="([^"]+)"'),
    re.compile(r"""['"]X-CSRF-TOKEN['"]\s*:\s*['"]([^'"]+)['"]"""),
)
_CONTRACT_SELECT = re.compile(
    r'<select[^>]*id="contrato_activo"[^>]*>(.*?)</select>', re.S
)
_OPTION = re.compile(
    r'<option[^>]*value="(\d+)"[^>]*>\s*(\d+)\s*(?:\((.*?)\))?\s*</option>', re.S
)
_SWITCH_CONTRACT = re.compile(r"/abonat/canviar/(\d+)")
_INVOICE = re.compile(r"/abonat/factures/[^/\"'\s]+/([^/\"'\s?#<]+)")
_UUID = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I
)
_METER_SERIAL = re.compile(r"\b[A-Z]\d{2}[A-Z]{2}\d{5,}[A-Z]?\b")
_LABELLED_VALUE = re.compile(
    r"<label[^>]*control-label-contracte[^>]*>\s*([^<]+?)\s*</label>\s*"
    r"<div[^>]*>(.*?)</div>",
    re.S,
)
_USER_PROFILE = re.compile(
    r'class="user-profile[^"]*"[^>]*>.*?<span class="hidden-xs">\s*(.*?)\s*</span>',
    re.S,
)
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_EMAIL_ALLOWLIST = frozenset({"avsa@aiguesvic.com", ANON_EMAIL})
_DNI_NIE = re.compile(r"(?<![A-Za-z0-9])(?:\d{8}|[XYZ]\d{7})[A-Z](?![A-Za-z0-9])")
_IBAN = re.compile(r"\bES\d{2}(?:[ ]?\d{4}){5}\b")
_MOBILE = re.compile(
    r"(?<![A-Za-z0-9])(?<!\d[.,])(?:\+34[ ]?)?[67]\d{2}[ ]?\d{3}[ ]?\d{3}"
    r"(?![A-Za-z0-9]|[.,]\d)"
)
_TAG = re.compile(r"<[^>]+>")

# Holder, supply address and contract aliases are free text; they are replaced
# with fixed placeholders because their shape is irrelevant for parsing.
_LABEL_KINDS = {
    "adreça subministrament": "address",
    "titular del contracte": "holder",
}
_PLACEHOLDERS = {
    "address": "ADREÇA EXEMPLE {n}",
    "alias": "ÀLIES {n}",
    "holder": "COGNOM{n} COGNOM, NOM",
    "name-part": "NOM{n}",
    "user-name": "Nom{n} Cognom",
}
_SHAPED_KINDS = frozenset({"contract", "invoice", "meter", "uuid"})
_NAME_KINDS = frozenset({"holder", "user-name"})
_MIN_LEAK_LENGTH = 4


def _text(fragment: str) -> str:
    return " ".join(html.unescape(_TAG.sub(" ", fragment)).split())


@dataclass
class Anonymizer:
    """Learn sensitive values from HAR data and replace them consistently."""

    key: bytes = field(default_factory=lambda: secrets.token_bytes(32))
    values: dict[str, tuple[str, str]] = field(default_factory=dict)
    stats: Counter[str] = field(default_factory=Counter)
    _counters: Counter[str] = field(default_factory=Counter)
    _pattern: re.Pattern[str] | None = None
    _lookup: dict[str, str] = field(default_factory=dict)

    def add(self, kind: str, value: str) -> None:
        """Register a sensitive value of a given kind."""
        value = value.strip()
        if not value or value.casefold() in self.values:
            return
        if kind == "email":
            replacement = ANON_EMAIL
        elif kind in ("secret", "password"):
            replacement = REDACTED
        elif kind == "dni":
            replacement = "00000000T"
        elif kind == "phone":
            replacement = "600000000"
        elif kind in _SHAPED_KINDS:
            replacement = self._shaped(kind, value)
        else:
            self._counters[kind] += 1
            replacement = _PLACEHOLDERS[kind].format(n=self._counters[kind])
        self.values[value.casefold()] = (kind, replacement)
        self._pattern = None
        if kind in _NAME_KINDS:
            for part in re.split(r"[\s,]+", value):
                if len(part) >= 3 and part.casefold() not in self.values:
                    self.add("name-part", part)

    def _shaped(self, kind: str, value: str) -> str:
        digest = hmac.new(self.key, f"{kind}:{value}".encode(), hashlib.sha256)
        stream = digest.digest()
        result = []
        for index, char in enumerate(value):
            byte = stream[index % len(stream)]
            if char.isdigit():
                result.append(str(byte % 10))
            elif "a" <= char.lower() <= "z":
                base = "A" if char.isupper() else "a"
                result.append(chr(ord(base) + byte % 26))
            else:
                result.append(char)
        pseudonym = "".join(result)
        return pseudonym if pseudonym != value else self._shaped(kind, value + "*")

    def learn_entry(self, entry: dict[str, Any]) -> None:
        """Collect sensitive values from one HAR entry."""
        request = entry.get("request", {})
        for cookie in request.get("cookies", []):
            name = cookie.get("name", "").lower()
            value = unquote_plus(cookie.get("value", ""))
            if value.lower() == "null":
                continue
            if name == "username":
                self.add("email", value)
            elif name == "password":
                self.add("password", value)
        for param in request.get("postData", {}).get("params", []):
            name = param.get("name", "").lower()
            value = unquote_plus(param.get("value", ""))
            if value.lower() == "null":
                continue
            if name in ("email", "username"):
                self.add("email", value)
            elif name == "password":
                self.add("password", value)
            elif name in SECRET_FIELDS:
                self.add("secret", value)
        post_data = request.get("postData", {})
        if "json" in post_data.get("mimeType", "").lower() and post_data.get("text"):
            try:
                body = json.loads(post_data["text"])
                if isinstance(body, dict):
                    if "email" in body:
                        self.add("email", str(body["email"]))
                    if "password" in body:
                        self.add("password", str(body["password"]))
                    if "refresh" in body:
                        self.add("secret", str(body["refresh"]))
            except Exception:
                pass
        for url in (request.get("url", ""), _header(request, "referer")):
            self._learn_text(url)
        content = entry.get("response", {}).get("content", {})
        if _is_text(content.get("mimeType")):
            self._learn_text(_decoded_text(content))

    def _learn_text(self, text: str) -> None:
        if not text:
            return
        for pattern in _TOKEN_PATTERNS:
            for token in pattern.findall(text):
                self.add("secret", token)
        for contract in _SWITCH_CONTRACT.findall(text):
            self.add("contract", contract)
        for invoice in _INVOICE.findall(text):
            self.add("invoice", invoice)
        for uuid in _UUID.findall(text):
            self.add("uuid", uuid)
        for select in _CONTRACT_SELECT.findall(text):
            for value, label, alias in _OPTION.findall(select):
                self.add("contract", value)
                self.add("contract", label)
                if alias.strip():
                    self.add("alias", _text(alias))
        for label, value in _LABELLED_VALUE.findall(text):
            kind = _LABEL_KINDS.get(_text(label).casefold())
            if kind:
                self.add(kind, _text(value))
        for name in _USER_PROFILE.findall(text):
            self.add("user-name", _text(name))
        for serial in _METER_SERIAL.findall(text):
            self.add("meter", serial)
        if text.strip().startswith(("{", "[")):
            try:
                data = json.loads(text)
                self._learn_json(data)
            except Exception:
                pass

    def _learn_json(self, data: Any) -> None:
        if isinstance(data, dict):
            for k, v in data.items():
                if k in ("access", "refresh", "token"):
                    if isinstance(v, str):
                        self.add("secret", v)
                elif k in ("holder_name", "tenant_name"):
                    if isinstance(v, str):
                        self.add("holder", v)
                elif k in ("address", "supply_point_address"):
                    if isinstance(v, str):
                        self.add("address", v)
                elif k == "alias":
                    if isinstance(v, str):
                        self.add("alias", v)
                elif k in ("meter", "meter_serial_number"):
                    if isinstance(v, str):
                        self.add("meter", v)
                elif k in ("contract_number", "policy"):
                    if isinstance(v, str) and v.isdigit():
                        self.add("contract", v)
                elif k == "nif" and isinstance(v, str):
                    self.add("dni", v)
                elif k == "phone2" and isinstance(v, str) and v:
                    self.add("phone", v)
                else:
                    self._learn_json(v)
        elif isinstance(data, list):
            for item in data:
                self._learn_json(item)

    def _compiled(self) -> re.Pattern[str]:
        if self._pattern is None:
            variants: dict[str, str] = {}
            for value in self.values:
                for variant in _variants(value):
                    variants.setdefault(variant.casefold(), value)
            self._lookup = variants
            alternatives = sorted(variants, key=len, reverse=True)
            body = "|".join(re.escape(item) for item in alternatives) or r"(?!)"
            self._pattern = re.compile(
                rf"(?<![0-9A-Za-z])(?:{body})(?![0-9A-Za-z])", re.I
            )
        return self._pattern

    def scrub(self, text: str) -> str:
        """Replace learned values and generic personal data patterns."""
        if not text:
            return text

        def learned(match: re.Match[str]) -> str:
            original = self._lookup[match.group(0).casefold()]
            kind, replacement = self.values[original]
            self.stats[kind] += 1
            return replacement

        text = self._compiled().sub(learned, text)
        text = self._generic(text, _EMAIL, "email", ANON_EMAIL)
        text = self._generic(text, _DNI_NIE, "dni", "00000000T")
        text = self._generic(text, _IBAN, "iban", "ES00 0000 0000 0000 0000 0000")
        return self._generic(text, _MOBILE, "phone", "600000000")

    def _generic(
        self, text: str, pattern: re.Pattern[str], kind: str, replacement: str
    ) -> str:
        def substitute(match: re.Match[str]) -> str:
            if match.group(0).lower() in _EMAIL_ALLOWLIST:
                return match.group(0)
            self.stats[kind] += 1
            return replacement

        return pattern.sub(substitute, text)

    def leaks(self, document: str) -> list[str]:
        """Return the kinds of learned values still present in the output."""
        folded = document.casefold()
        found = set()
        for value, (kind, _) in self.values.items():
            if len(value) < _MIN_LEAK_LENGTH and kind not in ("password", "secret"):
                continue
            if any(variant.casefold() in folded for variant in _variants(value)):
                found.add(kind)
        return sorted(found)


def _variants(value: str) -> set[str]:
    escaped = html.escape(value, quote=True)
    return {
        value,
        escaped,
        escaped.replace("&#x27;", "&#039;"),
        quote(value, safe=""),
        quote_plus(value),
    }


def _header(message: dict[str, Any], name: str) -> str:
    return next(
        (
            str(header.get("value", ""))
            for header in message.get("headers", [])
            if header.get("name", "").lower() == name
        ),
        "",
    )


def _is_text(mime_type: str | None) -> bool:
    return any(hint in (mime_type or "").lower() for hint in TEXT_MIME_HINTS)


def _decoded_text(content: dict[str, Any]) -> str:
    text = content.get("text") or ""
    if content.get("encoding") == "base64":
        return base64.b64decode(text).decode("utf-8", "replace")
    return str(text)


def _is_portal(url: str) -> bool:
    return urlsplit(url).hostname in PORTAL_HOSTS


def _is_static(url: str) -> bool:
    return urlsplit(url).path.lower().endswith(STATIC_SUFFIXES)


def _walk(value: Any, transform: Callable[[str], str]) -> Any:
    if isinstance(value, str):
        return transform(value)
    if isinstance(value, list):
        return [_walk(item, transform) for item in value]
    if isinstance(value, dict):
        return {key: _walk(item, transform) for key, item in value.items()}
    return value


def _redact_message(message: dict[str, Any]) -> None:
    for header in message.get("headers", []):
        if header.get("name", "").lower() in SENSITIVE_HEADERS:
            header["value"] = REDACTED
    for cookie in message.get("cookies", []):
        cookie["value"] = REDACTED


def _redact_post_data(request: dict[str, Any]) -> None:
    post_data = request.get("postData")
    if not post_data:
        return
    params = post_data.get("params", [])
    for param in params:
        name = param.get("name", "").lower()
        if name in SECRET_FIELDS:
            param["value"] = REDACTED
        elif name in ("email", "username"):
            param["value"] = ANON_EMAIL
    if params and "x-www-form-urlencoded" in post_data.get("mimeType", ""):
        post_data["text"] = urlencode([(p["name"], p.get("value", "")) for p in params])
    elif "json" in post_data.get("mimeType", "").lower() and post_data.get("text"):
        try:
            body = json.loads(post_data["text"])
            if isinstance(body, dict):
                if "email" in body:
                    body["email"] = ANON_EMAIL
                if "password" in body:
                    body["password"] = REDACTED
                if "refresh" in body:
                    body["refresh"] = REDACTED
                post_data["text"] = json.dumps(body)
        except Exception:
            post_data["text"] = REDACTED
    elif "text" in post_data:
        post_data["text"] = REDACTED


def prepare_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Remove secrets and irrelevant bodies; text is scrubbed afterwards."""
    request = entry["request"]
    response = entry["response"]
    _redact_message(request)
    _redact_message(response)
    _redact_post_data(request)
    entry.pop("_initiator", None)
    content = response.get("content", {})
    if _is_static(request.get("url", "")) or not _is_text(content.get("mimeType")):
        content.pop("text", None)
        content.pop("encoding", None)
        content["comment"] = "body removed by anonymize_har.py"
    elif content.get("encoding") == "base64":
        content["text"] = _decoded_text(content)
        content.pop("encoding")
    return entry


def anonymize(
    hars: Iterable[dict[str, Any]], anonymizer: Anonymizer
) -> list[dict[str, Any]]:
    """Return anonymized copies of the given HAR documents."""
    documents: list[dict[str, Any]] = [json.loads(json.dumps(har)) for har in hars]
    for document in documents:
        for entry in document["log"]["entries"]:
            if _is_portal(entry["request"]["url"]):
                anonymizer.learn_entry(entry)
    for document in documents:
        log = document["log"]
        portal = [e for e in log["entries"] if _is_portal(e["request"]["url"])]
        anonymizer.stats["third-party-entry"] += len(log["entries"]) - len(portal)
        log["entries"] = [prepare_entry(entry) for entry in portal]
        log.pop("comment", None)
        document["log"] = _walk(log, anonymizer.scrub)
    return documents


def main(argv: list[str] | None = None) -> int:
    """Run the command line interface."""
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Anonymize Aigües Vic HAR files.")
    parser.add_argument("hars", nargs="*", type=Path)
    parser.add_argument(
        "--output-dir", type=Path, default=root / "local" / "anonymized"
    )
    args = parser.parse_args(argv)
    inputs: list[Path] = args.hars or sorted((root / "local").glob("*.har"))
    if not inputs:
        parser.error("no HAR files found")

    anonymizer = Anonymizer()
    sources = [json.loads(path.read_text(encoding="utf-8")) for path in inputs]
    results = anonymize(sources, anonymizer)
    serialized = [json.dumps(item, ensure_ascii=False, indent=2) for item in results]
    leaks = anonymizer.leaks("\n".join(serialized))
    if leaks:
        print(
            f"Leak check failed, nothing written: {', '.join(leaks)}", file=sys.stderr
        )
        return 1

    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    for path, text in zip(inputs, serialized, strict=True):
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", path.stem).strip("_")
        (output_dir / f"{name}.anon.har").write_text(text + "\n", encoding="utf-8")
    learned = Counter(kind for kind, _ in anonymizer.values.values())
    print(f"Wrote {len(inputs)} file(s) to {output_dir}")
    print("Learned values:", dict(sorted(learned.items())))
    print("Replacements:", dict(sorted(anonymizer.stats.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
