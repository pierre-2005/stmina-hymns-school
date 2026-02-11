import json
import os
from typing import Any, Dict, Optional

from openpyxl import load_workbook


_cache: Dict[str, Any] = {}
_cache_mtime: float | None = None


def _parse_time_to_ms(t: str) -> int:
    """
    Accepts:
      "m:ss"     -> 0:06
      "m:ss.s"   -> 0:06.5
      "mm:ss"    -> 02:15
      "h:mm:ss"  -> 1:02:03
    Returns milliseconds.
    """
    t = (t or "").strip()
    if not t:
        return 0

    parts = t.split(":")
    if len(parts) == 1:
        return int(round(float(parts[0]) * 1000))

    last = float(parts[-1])  # may include decimals
    nums = [int(p) for p in parts[:-1]]

    if len(nums) == 1:
        m = nums[0]
        total = (m * 60) + last
        return int(round(total * 1000))

    if len(nums) == 2:
        h, m = nums
        total = (h * 3600) + (m * 60) + last
        return int(round(total * 1000))

    return 0


def _truthy(v: Any, default: bool = True) -> bool:
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ("1", "true", "yes", "y", "on"):
        return True
    if s in ("0", "false", "no", "n", "off"):
        return False
    return default


def _read_table(wb, sheet_name: str) -> list[dict]:
    if sheet_name not in wb.sheetnames:
        return []
    ws = wb[sheet_name]
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    headers = [str(h).strip() if h is not None else "" for h in rows[0]]
    out: list[dict] = []
    for r in rows[1:]:
        if r is None:
            continue
        if all(v is None or str(v).strip() == "" for v in r):
            continue
        d = {}
        for i, h in enumerate(headers):
            if not h:
                continue
            d[h] = r[i] if i < len(r) else None
        out.append(d)
    return out


def _load_xlsx(content_path: str) -> Dict[str, Any]:
    wb = load_workbook(content_path, data_only=True)

    meta_rows = _read_table(wb, "meta")
    site: Dict[str, Any] = {
        "site_title": "St. Mina Hymns School",
        "site_subtitle": "",
        "footer_text": "",
        "languages": [],
        "levels": [],
    }
    for row in meta_rows:
        k = str(row.get("key") or "").strip()
        v = row.get("value")
        if k:
            site[k] = v if v is not None else ""

    languages = []
    for row in _read_table(wb, "languages"):
        code = str(row.get("code") or "").strip()
        if not code:
            continue
        languages.append({
            "code": code,
            "name": str(row.get("name") or code),
            "is_rtl": _truthy(row.get("is_rtl"), default=False),
            "default_on": _truthy(row.get("default_on"), default=True),
        })
    site["languages"] = sorted(languages, key=lambda x: x["code"])

    # levels
    levels_by_slug: dict[str, dict] = {}
    for row in _read_table(wb, "levels"):
        if not _truthy(row.get("published"), default=True):
            continue
        slug = str(row.get("level_slug") or "").strip()
        if not slug:
            continue
        levels_by_slug[slug] = {
            "slug": slug,
            "name": str(row.get("level_name") or slug),
            "description": str(row.get("level_description") or ""),
            "sort": int(row.get("sort") or 0),
            "years": [],
        }

    # years
    years_by_slug: dict[str, dict] = {}
    for row in _read_table(wb, "years"):
        if not _truthy(row.get("published"), default=True):
            continue
        yslug = str(row.get("year_slug") or "").strip()
        lslug = str(row.get("level_slug") or "").strip()
        if not yslug or not lslug or lslug not in levels_by_slug:
            continue
        yr = {
            "slug": yslug,
            "name": str(row.get("year_name") or yslug),
            "description": str(row.get("year_description") or ""),
            "sort": int(row.get("sort") or 0),
            "hymns": [],
        }
        years_by_slug[yslug] = yr
        levels_by_slug[lslug]["years"].append(yr)

    # hymns
    hymns_by_slug: dict[str, dict] = {}
    for row in _read_table(wb, "hymns"):
        if not _truthy(row.get("published"), default=True):
            continue
        hslug = str(row.get("hymn_slug") or "").strip()
        yslug = str(row.get("year_slug") or "").strip()
        if not hslug or not yslug or yslug not in years_by_slug:
            continue
        hymn = {
            "slug": hslug,
            "title": str(row.get("hymn_title") or hslug),
            "note": str(row.get("hymn_note") or ""),
            "sort": int(row.get("sort") or 0),
            "recordings": [],
            "segments": [],
        }
        hymns_by_slug[hslug] = hymn
        years_by_slug[yslug]["hymns"].append(hymn)

    # recordings
    for row in _read_table(wb, "recordings"):
        if not _truthy(row.get("published"), default=True):
            continue
        hslug = str(row.get("hymn_slug") or "").strip()
        if hslug not in hymns_by_slug:
            continue
        url = str(row.get("url") or "").strip()
        if not url:
            continue
        hymns_by_slug[hslug]["recordings"].append({
            "label": str(row.get("label") or "Recording"),
            "url": url,
            "default_rate": float(row.get("default_rate") or 1.0),
        })

    # segments (lyrics rows)
    lang_codes = [l["code"] for l in site["languages"]]
    for row in _read_table(wb, "segments"):
        if not _truthy(row.get("published"), default=True):
            continue
        hslug = str(row.get("hymn_slug") or "").strip()
        if hslug not in hymns_by_slug:
            continue
        t = str(row.get("t") or "0:00").strip()
        texts = {}
        for code in lang_codes:
            val = row.get(code)
            if val is None:
                continue
            s = str(val)
            if s.strip() == "":
                continue
            texts[code] = s
        hymns_by_slug[hslug]["segments"].append({
            "t": t,
            "start_ms": _parse_time_to_ms(t),
            "texts": texts,
        })

    # sort everything
    for lvl in levels_by_slug.values():
        lvl["years"] = sorted(lvl["years"], key=lambda y: (y.get("sort", 0), y["name"]))
        for yr in lvl["years"]:
            yr["hymns"] = sorted(yr["hymns"], key=lambda h: (h.get("sort", 0), h["title"]))
            for h in yr["hymns"]:
                h["segments"] = sorted(h["segments"], key=lambda s: int(s.get("start_ms", 0)))

    site["levels"] = sorted(levels_by_slug.values(), key=lambda l: (l.get("sort", 0), l["name"]))
    return site


def load_site(content_path: str) -> Dict[str, Any]:
    global _cache, _cache_mtime

    mtime = os.path.getmtime(content_path)
    if _cache and _cache_mtime == mtime:
        return _cache

    if content_path.lower().endswith(".xlsx"):
        site = _load_xlsx(content_path)
    else:
        with open(content_path, "r", encoding="utf-8") as f:
            site = json.load(f)

    _cache = site
    _cache_mtime = mtime
    return site


def find_level(site: Dict[str, Any], level_slug: str) -> Optional[Dict[str, Any]]:
    for lvl in site.get("levels", []):
        if lvl.get("slug") == level_slug:
            return lvl
    return None


def find_year(level: Dict[str, Any], year_slug: str) -> Optional[Dict[str, Any]]:
    for yr in level.get("years", []):
        if yr.get("slug") == year_slug:
            return yr
    return None


def find_hymn(year: Dict[str, Any], hymn_slug: str) -> Optional[Dict[str, Any]]:
    for h in year.get("hymns", []):
        if h.get("slug") == hymn_slug:
            return h
    return None
