"""Download / generate SaaS stencil SVGs into static/saas-icons/.

Prefer Simple Icons (CC0) from jsDelivr when a slug exists; otherwise write a
small monogram tile with the vendor brand color so every saas_* key has a file.

Usage:
  python scripts/fetch_saas_icons.py
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "static" / "saas-icons"
MANIFEST = OUT_DIR / "manifest.json"

# shape_type stem (without saas_) -> (simple_icons_slug | None, brand_hex, monogram letters)
_VENDORS: dict[str, tuple[str | None, str, str]] = {
    "salesforce": ("salesforce", "#00A1E0", "SF"),
    "workday": (None, "#F68D2E", "WD"),
    "successfactors": (None, "#008FD3", "SFS"),
    "servicenow": ("servicenow", "#81B5A1", "SN"),
    "okta": ("okta", "#007DC1", "OK"),
    "guidewire": (None, "#E87722", "GW"),
    "duck_creek": (None, "#6B2D7B", "DC"),
    "temenos": (None, "#0033A0", "TE"),
    "finastra": (None, "#FF5A00", "FN"),
    "fiserv": (None, "#FF6600", "FV"),
    "fis": (None, "#00A3E0", "FIS"),
    "murex": (None, "#1B3A5F", "MX"),
    "bloomberg": (None, "#2800D1", "BB"),
    "refinitiv": (None, "#FF8000", "RF"),
    "experian": (None, "#3B6E8F", "EX"),
    "equifax": (None, "#C8102E", "EQ"),
    "stripe": ("stripe", "#635BFF", "ST"),
    "adyen": ("adyen", "#0ABF53", "AD"),
    "plaid": ("plaid", "#111111", "PL"),
    "slack": ("slack", "#4A154B", "SL"),
    "office365": ("microsoft", "#D83B01", "M365"),
    "microsoft_365": ("microsoft", "#D83B01", "M365"),
}

_CDN = "https://cdn.jsdelivr.net/npm/simple-icons@11.15.0/icons/{slug}.svg"


def _monogram_svg(letters: str, fill: str) -> str:
    safe = letters[:4]
    font_size = 42 if len(safe) <= 2 else 28 if len(safe) == 3 else 22
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" role="img">'
        f'<rect width="64" height="64" rx="12" fill="{fill}"/>'
        f'<text x="32" y="38" text-anchor="middle" font-family="Arial,Helvetica,sans-serif" '
        f'font-size="{font_size}" font-weight="700" fill="#FFFFFF">{safe}</text>'
        f"</svg>\n"
    )


def _recolor_simple_icon(svg: str, brand: str) -> str:
    out = svg
    for old in ('fill="#000"', 'fill="#000000"', "fill='#000'", "fill='#000000'"):
        out = out.replace(old, f'fill="{brand}"')
    if 'fill="' not in out and "<path " in out:
        out = out.replace("<path ", f'<path fill="{brand}" ', 1)
    return out


def _fetch(url: str) -> str | None:
    req = urllib.request.Request(url, headers={"User-Agent": "drawio-agent-saas-icons/1.0"})
    last: Exception | None = None
    for ctx in (ssl.create_default_context(), ssl._create_unverified_context()):
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
                return resp.read().decode("utf-8")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
            continue
    print(f"  miss {url}: {last}")
    return None


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, dict[str, str]] = {}
    for stem, (slug, brand, letters) in _VENDORS.items():
        path = OUT_DIR / f"{stem}.svg"
        source = "monogram"
        svg: str | None = None
        if slug:
            print(f"fetch {stem} <- simple-icons/{slug}")
            raw = _fetch(_CDN.format(slug=slug))
            if raw and "<svg" in raw:
                svg = _recolor_simple_icon(raw, brand)
                source = f"simple-icons:{slug}"
        if not svg:
            print(f"monogram {stem} ({letters})")
            svg = _monogram_svg(letters, brand)
            source = "monogram"
        path.write_text(svg, encoding="utf-8")
        manifest[f"saas_{stem}"] = {
            "file": path.name,
            "source": source,
            "brand": brand,
        }
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(manifest)} icons -> {OUT_DIR}")


if __name__ == "__main__":
    main()
