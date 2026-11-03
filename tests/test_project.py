"""Validate repository metadata without a live Home Assistant."""

import json
import struct
import tomllib
import zlib
from pathlib import Path

from custom_components.aigues_vic.const import DOMAIN

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / DOMAIN


def test_single_integration() -> None:
    """Keep the repository compatible with the HACS single-integration layout."""
    domains = {
        path.name
        for path in (ROOT / "custom_components").iterdir()
        if path.is_dir() and not path.name.startswith((".", "__"))
    }
    assert domains == {DOMAIN}


def test_manifest() -> None:
    """Require consistent manifest metadata."""
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    repository = "https://github.com/cguadall/ha-aigues-vic-portal"

    assert manifest["domain"] == DOMAIN == "aigues_vic"
    assert manifest["name"] == "Aigües Vic"
    assert manifest["codeowners"] == ["@cguadall"]
    assert manifest["documentation"] == repository
    assert manifest["issue_tracker"] == f"{repository}/issues"
    assert manifest["integration_type"] == "service"
    assert manifest["iot_class"] == "cloud_polling"
    assert manifest["version"] == "0.3.0"
    assert manifest["config_flow"] is True
    assert manifest["dependencies"] == ["recorder"]
    assert manifest["requirements"] == []
    assert list(manifest)[:2] == ["domain", "name"]
    assert list(manifest)[2:] == sorted(list(manifest)[2:])


def test_hacs_metadata() -> None:
    """Match display names and declare the minimum Home Assistant version."""
    hacs = json.loads((ROOT / "hacs.json").read_text(encoding="utf-8"))
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))

    assert hacs["name"] == manifest["name"]
    assert hacs["homeassistant"] == "2026.10.0"
    assert not hacs.get("content_in_root", False)


def test_documentation_and_license() -> None:
    """Ship both documentation languages, clear status and the MIT license."""
    catalan = (ROOT / "README.md").read_text(encoding="utf-8")
    english = (ROOT / "README.en.md").read_text(encoding="utf-8")

    assert "(README.en.md)" in catalan
    assert "(README.md)" in english
    assert "pendent de validar amb el portal real" in catalan
    assert "not yet validated against the real portal" in english
    for text in (catalan, english):
        assert "aigues_vic:water_" in text
    assert (ROOT / "LICENSE").read_text(encoding="utf-8").startswith("MIT License")


def _keys(value: object, prefix: str = "") -> set[str]:
    if not isinstance(value, dict):
        return {prefix}
    return set().union(*(_keys(v, f"{prefix}.{k}") for k, v in value.items()))


def test_translations() -> None:
    """Keep the source strings and both translations in sync."""
    strings = json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8"))
    expected = _keys(strings)
    for language in ("en", "ca"):
        path = COMPONENT / "translations" / f"{language}.json"
        assert _keys(json.loads(path.read_text(encoding="utf-8"))) == expected
    english = COMPONENT / "translations" / "en.json"
    assert json.loads(english.read_text(encoding="utf-8")) == strings


def test_python_configuration() -> None:
    """Align the local, CI and analysis Python versions."""
    configuration = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert (ROOT / ".python-version").read_text(encoding="utf-8").strip() == "3.14"
    assert configuration["tool"]["ruff"]["target-version"] == "py314"
    assert configuration["tool"]["mypy"]["python_version"] == "3.14"


def test_brand_icon() -> None:
    """Validate PNG chunks, checksums, dimensions and the complete pixel stream."""
    data = (COMPONENT / "brand" / "icon.png").read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    offset = 8
    compressed = bytearray()
    chunks: list[bytes] = []
    while offset < len(data):
        size = struct.unpack(">I", data[offset : offset + 4])[0]
        kind = data[offset + 4 : offset + 8]
        payload = data[offset + 8 : offset + 8 + size]
        checksum = struct.unpack(">I", data[offset + 8 + size : offset + 12 + size])[0]
        assert checksum == zlib.crc32(kind + payload)
        chunks.append(kind)
        if kind == b"IHDR":
            assert struct.unpack(">IIBBBBB", payload) == (256, 256, 8, 6, 0, 0, 0)
        elif kind == b"IDAT":
            compressed.extend(payload)
        offset += size + 12
    assert chunks[0] == b"IHDR"
    assert chunks[-1] == b"IEND"
    assert offset == len(data)
    pixels = zlib.decompress(compressed)
    assert len(pixels) == 256 * (1 + 256 * 4)
