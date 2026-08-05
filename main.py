import base64
import hashlib
import hmac
import html
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta

import cloudscraper
import requests

try:
    from curl_cffi import requests as curl_requests
    _CURL_CFFI = True
except ImportError:
    _CURL_CFFI = False

def _normalize_workers_url(url: str) -> str:
    """Sửa URL thiếu .dev (vd: dekki.bacbenny95.workers → .workers.dev)."""
    url = (url or "").strip().rstrip("/")
    if url.endswith(".workers") and not url.endswith(".workers.dev"):
        url += ".dev"
    return url


def _resolve_base_url(url: str, timeout: int = 8) -> str:
    """Follow HTTP 3xx redirects và trả về scheme+host cuối cùng.
    Dùng để tự động phát hiện khi domain đổi (vd: khandaia.link → khandaia4.link).
    """
    try:
        r = requests.get(
            url, timeout=timeout, allow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
        )
        final = r.url or url
    except Exception:
        final = url
    m = re.match(r"(https?://[^/?#]+)", final)
    return m.group(1) if m else url.rstrip("/")


def _resolve_all_frontends() -> None:
    """Gọi lúc startup: tự động cập nhật HOIQUAN/KHANDAIA/VONGCAM _FRONTEND_URL
    bằng cách follow redirect. Chạy song song để tiết kiệm thời gian.
    In log nếu domain thực tế khác domain cấu hình.
    """
    global HOIQUAN_FRONTEND_URL, KHANDAIA_FRONTEND_URL, VONGCAM_FRONTEND_URL
    sources = {
        "Hội Quán TV":   ("HOIQUAN",   HOIQUAN_FRONTEND_URL),
        "Khán Đài A":    ("KHANDAIA",  KHANDAIA_FRONTEND_URL),
        "Vòng Cấm TV":   ("VONGCAM",   VONGCAM_FRONTEND_URL),
    }
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(_resolve_base_url, cfg[1]): (name, cfg) for name, cfg in sources.items()}
        for fut in as_completed(futures):
            (name, (key, original)) = futures[fut]
            try:
                resolved = fut.result()
            except Exception:
                resolved = original
            if resolved != original.rstrip("/"):
                print(f"[domain-resolve] {name}: {original} → {resolved}", file=sys.stderr)
            if key == "HOIQUAN":
                HOIQUAN_FRONTEND_URL = resolved
            elif key == "KHANDAIA":
                KHANDAIA_FRONTEND_URL = resolved
            elif key == "VONGCAM":
                VONGCAM_FRONTEND_URL = resolved


# ─── Hội Quán TV config ──────────────────────────────────────────────────────[...]
HOIQUAN_FRONTEND_URL   = (os.environ.get("HOIQUAN_FRONTEND") or "https://sv2.hoiquan4.live")
HOIQUAN_KNOWN_API_BASE = (os.environ.get("HOIQUAN_API") or "https://sv.hoiquantv.xyz/api/v1/external")

# ─── Khán Đài A config ──────────────────────────────────────────────────────[...]
KHANDAIA_FRONTEND_URL   = (os.environ.get("KHANDAIA_FRONTEND") or "https://tructiep.khandaia.link")
KHANDAIA_KNOWN_API_BASE = (os.environ.get("KHANDAIA_API") or "https://sv.khandai-a.xyz/api/v1/external")

# ─── Vòng Cấm TV config ───────────────────────────────────────────────────────[...]
VONGCAM_FRONTEND_URL   = (os.environ.get("VONGCAM_FRONTEND") or "https://sv2.vongcam3.live")
VONGCAM_KNOWN_API_BASE = (os.environ.get("VONGCAM_API") or "https://sv.bugiotv.xyz/internal/api/matches")
VONGCAM_ACCESS_TOKEN   = os.environ.get("VONGCAM_ACCESS_TOKEN", "AB321C")

# ─── Relay URLs (Replit proxy — bypass GitHub Actions 403) ────────────────────
RELAY_SECRET       = os.environ.get("RELAY_SECRET", "")
HOIQUAN_RELAY_URL  = (os.environ.get("HOIQUAN_RELAY_URL") or "https://dekki.bacbenny95.workers.dev/hoiquan").strip().rstrip("/")
KHANDAIA_RELAY_URL = (os.environ.get("KHANDAIA_RELAY_URL") or "https://dekki.bacbenny95.workers.dev/khandaia").strip().rstrip("/")
VONGCAM_RELAY_URL  = (os.environ.get("VONGCAM_RELAY_URL") or "https://dekki.bacbenny95.workers.dev/vongcam").strip().rstrip("/")

# ─── Shared config ──────────────────────────────────────────────────────────[...]
VN_TZ                 = timezone(timedelta(hours=7))
API_DISCOVERY_TTL     = 3600
MATCH_MAX_AGE_SECONDS = int(os.environ.get("MATCH_MAX_DURATION") or 7200)

FINISHED_STATUS_STRINGS = {"finished", "end", "ended", "complete", "completed"}

# ─── Sport logos (Twemoji via jsDelivr) ───────────────────────────────────────
_CDN = "https://cdn.jsdelivr.net/gh/twitter/twemoji@14.0.2/assets/72x72"
SPORT_LOGOS = {
    "football":    f"{_CDN}/26bd.png",
    "tennis":      f"{_CDN}/1f3be.png",
    "basketball":  f"{_CDN}/1f3c0.png",
    "volleyball":  f"{_CDN}/1f3d0.png",
    "billiards":   f"{_CDN}/1f3b1.png",
    "badminton":   f"{_CDN}/1f3f8.png",
    "boxing":      f"{_CDN}/1f94a.png",
    "golf":        f"{_CDN}/26f3.png",
    "esport":      f"{_CDN}/1f3ae.png",
    "motorsport":  f"{_CDN}/1f3ce.png",
    "athletics":   f"{_CDN}/1f3c3.png",
    "swimming":    f"{_CDN}/1f3ca.png",
    "martialarts": f"{_CDN}/1f94b.png",
    "cycling":     f"{_CDN}/1f6b4.png",
    "hockey":      f"{_CDN}/1f3d2.png",
    "default":     f"{_CDN}/1f3c6.png",
}

# ─── API URL caches (dùng để tránh re-discover liên tục trong 1 lần chạy) ────
_hoiquan_api_cache  = {"url": HOIQUAN_KNOWN_API_BASE,  "discovered_at": 0}
_khandaia_api_cache = {"url": KHANDAIA_KNOWN_API_BASE, "discovered_at": 0}

# ─── Shared HTTP headers ──────────────────────────────────────────────────────
_HQ_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}


# ════════════════════════════════════════════════════════════════–[...]
#  Sport logo helpers
# ════════════════════════════════════════════════════════════════–[...]

def _logo_from_text(text: str) -> str:
    t = text.lower()
    if "tennis" in t:
        return SPORT_LOGOS["tennis"]
    if any(k in t for k in ["basketball", "bóng rổ", "bong ro", "nba", "wnba"]):
        return SPORT_LOGOS["basketball"]
    if any(k in t for k in ["volleyball", "bóng chuyền", "bong chuyen"]):
        return SPORT_LOGOS["volleyball"]
    if any(k in t for k in ["billiard", "bi-a", "bia", "snooker", "pool", "uk open"]):
        return SPORT_LOGOS["billiards"]
    if any(k in t for k in ["badminton", "cầu lông", "cau long"]):
        return SPORT_LOGOS["badminton"]
    if any(k in t for k in ["boxing", "kickbox", "muay", "quyền anh", "quyen anh", "ufc", "mma"]):
        return SPORT_LOGOS["boxing"]
    if any(k in t for k in ["golf"]):
        return SPORT_LOGOS["golf"]
    if any(k in t for k in ["esport", "e-sport", "gaming", "lol", "dota", "valorant", "fifa online"]):
        return SPORT_LOGOS["esport"]
    if any(k in t for k in ["formula", "f1 ", " f1", "motogp", "moto gp", "đua xe", "dua xe", "motorsport", "superbike", "wtcc"]):
        return SPORT_LOGOS["motorsport"]
    if any(k in t for k in ["athletics", "điền kinh", "dien kinh", "marathon", "chạy", "cha y"]):
        return SPORT_LOGOS["athletics"]
    if any(k in t for k in ["swim", "bơi lội", "boi loi", "aquatic"]):
        return SPORT_LOGOS["swimming"]
    if any(k in t for k in ["karate", "judo", "taekwondo", "wushu", "võ thuật", "vo thuat",
                              "wrestling", "kung fu", "wwe", "smackdown", "raw", "aew",
                              "impact", "muay thai", "kickboxing", "bjj"]):
        return SPORT_LOGOS["martialarts"]
    if any(k in t for k in ["cycl", "xe đạp", "xe dap", "velo"]):
        return SPORT_LOGOS["cycling"]
    if any(k in t for k in ["hockey", "khúc côn", "khuc con"]):
        return SPORT_LOGOS["hockey"]
    return SPORT_LOGOS["football"]


def _hq_kda_logo(fixture: dict) -> str:
    sport = fixture.get("sport") or {}
    icon = sport.get("iconUrl", "")
    if icon:
        return icon
    parts = " ".join([sport.get("name", ""), sport.get("slug", "")])
    return _logo_from_text(parts)


# ════════════════════════════════════════════════════════════════–[...]
#  Vòng Cấm TV — bugiotv API + static token (re-discover từ JS nếu đổi)
#  Frontend : https://sv2.vongcam3.live
#  API      : https://sv.bugiotv.xyz/internal/api/matches
#  Auth     : Header Access-Token (static, re-discover mỗi 1h nếu đổi)
#  Timezone : startTime từ bugiotv là giờ VN (UTC+7), không phải UTC
# ════════════════════════════════════════════════════════════════–[...]

_vongcam_token_cache = {"token": VONGCAM_ACCESS_TOKEN, "discovered_at": 0.0}


def _discover_vongcam_token(scraper) -> str:
    """Re-discover Access-Token từ JS bundle của Vòng Cấm TV frontend."""
    try:
        r = scraper.get(VONGCAM_FRONTEND_URL, timeout=10)
        js_files = re.findall(r'src="(/[^"]+\.js)"', r.text)
        for js_path in js_files[:6]:
            try:
                js = scraper.get(
                    VONGCAM_FRONTEND_URL.rstrip("/") + js_path, timeout=20
                ).text
            except Exception:
                continue
            for pat in [
                r"""[Aa]ccess[-_]?[Tt]oken["']?\s*:\s*["']([A-Z0-9]{4,32})["']""",
                r"""["']Access-Token["']\s*:\s*["']([A-Z0-9]{4,32})["']""",
                r"""Authorization["']?\s*:\s*["']([A-Z0-9]{4,32})["']""",
            ]:
                hits = re.findall(pat, js)
                for hit in hits:
                    if hit and hit != "null":
                        return hit
    except Exception:
        pass
    return VONGCAM_ACCESS_TOKEN


def _get_vongcam_token(scraper=None) -> str:
    now = time.time()
    if now - _vongcam_token_cache["discovered_at"] > API_DISCOVERY_TTL:
        sc = scraper or cloudscraper.create_scraper()
        _vongcam_token_cache["token"] = _discover_vongcam_token(sc)
        _vongcam_token_cache["discovered_at"] = now
    return _vongcam_token_cache["token"]


def _fetch_vongcam_matches() -> list:
    """Goi bugiotv API, tra ve list matches."""
    # 1. Thu relay Replit truoc - bypass GitHub Actions 403
    if VONGCAM_RELAY_URL:
        try:
            hdrs: dict = {"Content-Type": "application/json", "X-Relay-Token": RELAY_SECRET}
            token = _get_vongcam_token()
            body  = {"access_token": token, "api_url": VONGCAM_KNOWN_API_BASE}
            r = requests.post(VONGCAM_RELAY_URL, headers=hdrs, json=body, timeout=20)
            r.raise_for_status()
            rdata = r.json()
            result = rdata.get("data") or rdata.get("matches") or []
            if result:
                print(f"  OK Vong Cam TV relay: {len(result)} matches", file=sys.stderr)
                return result
        except Exception as e:
            print(f"  FAIL Vong Cam TV relay: {e}", file=sys.stderr)
    # 2. Goi truc tiep (fallback)
    token = _get_vongcam_token()
    headers = {
        "Access-Token": token,
        "Referer":      VONGCAM_FRONTEND_URL + "/",
        "Origin":       VONGCAM_FRONTEND_URL,
        "Accept":       "application/json, text/plain, */*",
        "User-Agent":   "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    sc = cloudscraper.create_scraper()
    try:
        resp = sc.get(VONGCAM_KNOWN_API_BASE, headers=headers, timeout=15)
        if resp.status_code in (401, 403):
            _vongcam_token_cache["discovered_at"] = 0
            token = _get_vongcam_token(sc)
            headers["Access-Token"] = token
            resp = sc.get(VONGCAM_KNOWN_API_BASE, headers=headers, timeout=15)
        resp.raise_for_status()
        return resp.json().get("data", [])
    except Exception as e:
        print(f"Vong Cam TV that bai: {e}", file=sys.stderr)
        return []
def _vongcam_is_active(match: dict) -> bool:
    if bool(match.get("isLive")):
        return True
    start_str = match.get("startTime", "")
    if start_str:
        try:
            if "+" not in start_str and not start_str.endswith("Z"):
                start_str += "+07:00"
            dt      = datetime.fromisoformat(start_str)
            elapsed = time.time() - dt.timestamp()
            if elapsed < MATCH_MAX_AGE_SECONDS:
                return True
        except Exception:
            pass
    return False


def _vongcam_logo(match: dict) -> str:
    """Logo cho Vòng Cấm TV.
    bugiotv API không có sport-type field riêng → ghép tournamentName + title + slug + tags.
    """
    for key in ("sportType", "sport", "sportName", "sportSlug"):
        val = match.get(key)
        if isinstance(val, dict):
            icon = val.get("iconUrl") or val.get("icon", "")
            if icon:
                return icon
            val = val.get("name") or val.get("slug") or val.get("type", "")
        if val and isinstance(val, str) and val.upper() not in ("MANUAL", "AUTO"):
            logo = _logo_from_text(val)
            if logo != SPORT_LOGOS["football"]:
                return logo
    parts = [
        match.get("tournamentName", ""),
        match.get("title", ""),
        match.get("slug", ""),
    ]
    tags = match.get("tags") or []
    if isinstance(tags, list):
        parts.extend(str(t) for t in tags)
    return _logo_from_text(" ".join(p for p in parts if p))


def _build_vongcam_lines(matches: list) -> list:
    try:
        matches = sorted(matches, key=lambda m: m.get("startTime") or "")
    except Exception:
        pass
    lines: list[str] = []
    for match in matches:
        if not _vongcam_is_active(match):
            continue
        home       = match.get("homeClub", {}).get("name", "Home").strip()
        away       = match.get("awayClub", {}).get("name", "Away").strip()
        tournament = match.get("tournamentName", "")
        logo       = _vongcam_logo(match)
        start_str  = match.get("startTime", "")
        try:
            if "+" not in start_str and not start_str.endswith("Z"):
                start_str += "+07:00"
            dt       = datetime.fromisoformat(start_str)
            dt_vn    = dt.astimezone(VN_TZ)
            time_str = dt_vn.strftime("%H:%M")
            date_str = dt_vn.strftime("%d/%m")
        except Exception:
            time_str = "--:--"
            date_str = "--/--"
        commentator = match.get("commentator")
        if not commentator:
            continue
        stream_url = ""
        for key in ("streamSourceFhd", "streamSourceHd", "streamSourceSd"):
            url = (commentator.get(key) or "").strip()
            if url:
                stream_url = url
                break
        if not stream_url:
            continue
        nickname = (commentator.get("nickname") or "").strip()
        display  = f"{time_str} - {date_str} | {home} VS {away} ({tournament}) | {nickname}"
        lines.append(f'#EXTINF:-1 tvg-logo="{logo}" group-title="Vòng Cấm TV",{display}')
        _vc_ref = VONGCAM_FRONTEND_URL.rstrip("/") + "/"
        _vc_url = stream_url + (f"|Referer={_vc_ref}&User-Agent=Mozilla/5.0" if "|" not in stream_url else "")
        lines.append(_vc_url)
    return lines


# ════════════════════════════════════════════════════════════════–[...]
#  VTV tĩnh
# ════════════════════════════════════════════════════════════════–[...]

VTV_M3U_URL            = (os.environ.get("VTV_M3U_URL") or "https://raw.githubusercontent.com/Bacbenny/Verceliptv/refs/heads/main/VTV.m3u")

def _fetch_vtv_lines() -> list:
    resp = requests.get(VTV_M3U_URL, timeout=10)
    resp.raise_for_status()
    result = []
    for line in resp.text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#EXTM3U"):
            continue
        result.append(stripped)
    return result


# ════════════════════════════════════════════════════════════════–[...]
#  Hội Quán TV
# ════════════════════════════════════════════════════════════════–[...]

def _discover_hoiquan_api(scraper) -> str:
    """Tự động tìm HoiQuan API base từ frontend JS bundle."""
    _js_patterns = [
        r'VITE_SERVER_API_BASE_URL:\s*"(https://[^"]+)"',
        r'VITE_API_BASE(?:_URL)?:\s*"(https://[^"]+)"',
        r'baseURL:\s*"(https://sv\.[^"]+)"',
        r'"(https://sv\.[a-z0-9\-]+\.[a-z]+/api/v\d+/external)"',
        r'"(https://[a-z0-9\-]+\.[a-z]+/api/v\d+/external)"',
        r'https://sv\.[a-z0-9\-\.]+/api/v1/external',
    ]
    _probe_hosts = [
        "sv.hoiquantv.xyz", "sv2.hoiquantv.xyz", "sv3.hoiquantv.xyz",
        "api.hoiquantv.xyz", "sv.hoiquan4.live",
    ]
    _probe_paths = [
        "/api/v1/external", "/api/v2/external",
        "/api/v1/fixtures/unfinished", "/api/v2/fixtures/unfinished",
        "/external", "/fixtures/unfinished",
    ]
    try:
        html = scraper.get(HOIQUAN_FRONTEND_URL, timeout=10).text
        js_files = (re.findall(r'src="(/assets/[^"]+\.js)"', html) or
                    re.findall(r'src="(/[^"]+\.js)"', html))
        for js_path in js_files[:5]:
            try:
                js = scraper.get(HOIQUAN_FRONTEND_URL.rstrip("/") + js_path, timeout=15).text
                for pat in _js_patterns:
                    hits = re.findall(pat, js)
                    for hit in hits:
                        if any(x in hit for x in ["cdn","pull","stream","secufun","asynccdn"]):
                            continue
                        # Probe that it actually responds
                        try:
                            probe_url = hit.rstrip("/") + "/fixtures/unfinished"
                            pr = scraper.get(probe_url, headers={"Referer": HOIQUAN_FRONTEND_URL+"/"}, timeout=5)
                            if pr.ok:
                                return hit.rstrip("/")
                        except Exception:
                            pass
                        return hit.rstrip("/")  # Return even if probe fails — JS is authoritative
            except Exception:
                pass
    except Exception:
        pass
    # Probe fallback hosts
    for host in _probe_hosts:
        for path in _probe_paths:
            try:
                url = f"https://{host}{path}"
                pr  = scraper.get(url, headers={"Referer": HOIQUAN_FRONTEND_URL+"/"}, timeout=4)
                if pr.ok and "application/json" in pr.headers.get("content-type",""):
                    base = f"https://{host}" + path.rsplit("/",1)[0]
                    return base
            except Exception:
                pass
    return HOIQUAN_KNOWN_API_BASE
def _get_hoiquan_api_base(scraper) -> str:
    now = time.time()
    if now - _hoiquan_api_cache["discovered_at"] > API_DISCOVERY_TTL:
        _hoiquan_api_cache["url"] = _discover_hoiquan_api(scraper)
        _hoiquan_api_cache["discovered_at"] = now
    return _hoiquan_api_cache["url"]


def _fetch_hoiquan_fixtures() -> list:
    # 1. Thu relay Replit truoc - bypass GitHub Actions 403
    if HOIQUAN_RELAY_URL:
        try:
            hdrs: dict = {"Content-Type": "application/json", "X-Relay-Token": RELAY_SECRET}
            r = requests.post(HOIQUAN_RELAY_URL, headers=hdrs, json={}, timeout=20)
            r.raise_for_status()
            rdata = r.json()
            result = rdata.get("data") or rdata.get("fixtures") or []
            if result:
                print(f"  OK Hoi Quan relay: {len(result)} fixtures", file=sys.stderr)
                return result
        except Exception as e:
            print(f"  FAIL Hoi Quan relay: {e}", file=sys.stderr)
    # 2. Goi truc tiep (fallback)
    scraper  = cloudscraper.create_scraper()
    api_base = _get_hoiquan_api_base(scraper)
    url      = api_base.rstrip("/") + "/fixtures/unfinished"
    headers  = {**_HQ_HEADERS, "Referer": HOIQUAN_FRONTEND_URL + "/"}
    try:
        resp = scraper.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
    except Exception:
        _hoiquan_api_cache["discovered_at"] = 0
        api_base = _get_hoiquan_api_base(scraper)
        url  = api_base.rstrip("/") + "/fixtures/unfinished"
        resp = scraper.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        return []
    return data.get("data", [])


# ════════════════════════════════════════════════════════════════–[...]
#  Khán Đài A
# ════════════════════════════════════════════════════════════════–[...]

def _discover_khandaia_api(scraper) -> str:
    """Tự động tìm KhanDai A API base từ frontend JS bundle."""
    _js_patterns = [
        r'VITE_SERVER_API_BASE_URL:\s*"(https://[^"]+)"',
        r'VITE_API_BASE(?:_URL)?:\s*"(https://[^"]+)"',
        r'baseURL:\s*"(https://sv\.[^"]+)"',
        r'"(https://sv\.[a-z0-9\-]+\.[a-z]+/api/v\d+/external)"',
        r'"(https://[a-z0-9\-]+\.[a-z]+/api/v\d+/external)"',
        r'https://sv\.[a-z0-9\-\.]+/api/v1/external',
    ]
    _probe_hosts = [
        "sv.khandai-a.xyz", "sv2.khandai-a.xyz", "sv3.khandai-a.xyz",
        "api.khandaia.link", "sv.khandaia.link",
    ]
    _probe_paths = [
        "/api/v1/external", "/api/v2/external",
        "/api/v1/fixtures/unfinished", "/api/v2/fixtures/unfinished",
        "/external", "/fixtures/unfinished",
    ]
    try:
        html = scraper.get(KHANDAIA_FRONTEND_URL, timeout=10).text
        js_files = (re.findall(r'src="(/assets/[^"]+\.js)"', html) or
                    re.findall(r'src="(/[^"]+\.js)"', html))
        for js_path in js_files[:5]:
            try:
                js = scraper.get(KHANDAIA_FRONTEND_URL.rstrip("/") + js_path, timeout=20).text
                # Also scan chunk files
                chunk_paths = re.findall(r"assets/\S+\.js", js)
                extra_js = []
                for cp in chunk_paths[:3]:
                    try:
                        cjs = scraper.get(KHANDAIA_FRONTEND_URL.rstrip("/") + "/" + cp, timeout=15).text
                        extra_js.append(cjs)
                    except Exception:
                        pass
                for source in [js] + extra_js:
                    for pat in _js_patterns:
                        hits = re.findall(pat, source)
                        for hit in hits:
                            if any(x in hit for x in ["cdn","pull","stream","secufun","asynccdn"]):
                                continue
                            try:
                                probe_url = hit.rstrip("/") + "/fixtures/unfinished"
                                pr = scraper.get(probe_url, headers={"Referer": KHANDAIA_FRONTEND_URL+"/"}, timeout=5)
                                if pr.ok:
                                    return hit.rstrip("/")
                            except Exception:
                                pass
                            return hit.rstrip("/")
            except Exception:
                pass
    except Exception:
        pass
    # Probe fallback hosts
    for host in _probe_hosts:
        for path in _probe_paths:
            try:
                url = f"https://{host}{path}"
                pr  = scraper.get(url, headers={"Referer": KHANDAIA_FRONTEND_URL+"/"}, timeout=4)
                if pr.ok and "application/json" in pr.headers.get("content-type",""):
                    base = f"https://{host}" + path.rsplit("/",1)[0]
                    return base
            except Exception:
                pass
    return KHANDAIA_KNOWN_API_BASE
def _get_khandaia_api_base(scraper) -> str:
    now = time.time()
    if now - _khandaia_api_cache["discovered_at"] > API_DISCOVERY_TTL:
        _khandaia_api_cache["url"] = _discover_khandaia_api(scraper)
        _khandaia_api_cache["discovered_at"] = now
    return _khandaia_api_cache["url"]


def _fetch_khandaia_fixtures() -> list:
    # 1. Thu relay Replit truoc - bypass GitHub Actions 403
    if KHANDAIA_RELAY_URL:
        try:
            hdrs: dict = {"Content-Type": "application/json", "X-Relay-Token": RELAY_SECRET}
            r = requests.post(KHANDAIA_RELAY_URL, headers=hdrs, json={}, timeout=20)
            r.raise_for_status()
            rdata = r.json()
            result = rdata.get("data") or rdata.get("fixtures") or []
            if result:
                print(f"  OK Khan Dai A relay: {len(result)} fixtures", file=sys.stderr)
                return result
        except Exception as e:
            print(f"  FAIL Khan Dai A relay: {e}", file=sys.stderr)
    # 2. Goi truc tiep (fallback)
    scraper  = cloudscraper.create_scraper()
    api_base = _get_khandaia_api_base(scraper)
    url      = api_base.rstrip("/") + "/fixtures/unfinished"
    headers  = {**_HQ_HEADERS, "Referer": KHANDAIA_FRONTEND_URL + "/"}
    try:
        resp = scraper.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
    except Exception:
        _khandaia_api_cache["discovered_at"] = 0
        api_base = _get_khandaia_api_base(scraper)
        url  = api_base.rstrip("/") + "/fixtures/unfinished"
        resp = scraper.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        return []
    return data.get("data", [])


# ════════════════════════════════════════════════════════════════[...]
#  Shared fixture helpers
# ════════════════════════════════════════════════════════════════[...]

def _fixture_is_active(fixture: dict) -> bool:
    status = str(fixture.get("status") or "").lower().strip()
    if status in FINISHED_STATUS_STRINGS:
        return False
    if fixture.get("isFinished") or fixture.get("isEnd"):
        return False
    is_live        = bool(fixture.get("isLive"))
    start_time_str = fixture.get("startTime", "")
    if start_time_str and not is_live:
        try:
            dt      = datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))
            elapsed = time.time() - dt.timestamp()
            if elapsed > MATCH_MAX_AGE_SECONDS:
                return False
            if status == "active" and elapsed > 5400:
                return False
        except Exception:
            pass
    return True


def _pick_best_stream(streams: list) -> str:
    for quality in ("fhd", "hd", "sd"):
        for s in streams:
            if s.get("name", "").lower() == quality:
                url = s.get("sourceUrl", "")
                if url:
                    return url
    for s in streams:
        url = s.get("sourceUrl", "")
        if url:
            return url
    return ""


def _build_fixture_lines(fixtures: list, group_title: str) -> list:
    try:
        fixtures = sorted(fixtures, key=lambda f: f.get("startTime") or "")
    except Exception:
        pass
    lines = []
    for fixture in fixtures:
        if not _fixture_is_active(fixture):
            continue
        logo      = _hq_kda_logo(fixture)
        start_str = fixture.get("startTime", "")
        home      = fixture.get("homeTeam", {}).get("name", "Home").strip()
        away      = fixture.get("awayTeam", {}).get("name", "Away").strip()
        league    = fixture.get("league", {}).get("name", "")
        try:
            dt       = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
            dt_vn    = dt.astimezone(VN_TZ)
            time_str = dt_vn.strftime("%H:%M")
            date_str = dt_vn.strftime("%d/%m")
        except Exception:
            time_str = "--:--"
            date_str = "--/--"
        for entry in fixture.get("fixtureCommentators", []):
            commentator_obj = entry.get("commentator", {})
            name       = (commentator_obj.get("nickname") or commentator_obj.get("name") or "").strip()
            stream_url = _pick_best_stream(commentator_obj.get("streams", []))
            if not stream_url:
                continue
            display = f"{time_str} - {date_str} | {home} VS {away} ({league}) | {name}"
            lines.append(f'#EXTINF:-1 tvg-logo="{logo}" group-title="{group_title}",{display}')
            _referer_map = {
                "Hội Quán TV": HOIQUAN_FRONTEND_URL.rstrip("/") + "/",
                "Khán Đài A":  KHANDAIA_FRONTEND_URL.rstrip("/") + "/",
            }
            _ref = _referer_map.get(group_title, "")
            _final_url = stream_url + (f"|Referer={_ref}&User-Agent=Mozilla/5.0" if _ref and "|" not in stream_url else "")
            lines.append(_final_url)
    return lines


# ════════════════════════════════════════════════════════════════[...]
#  Main — fetch 4 nguồn, gộp, lưu file
# ════════════════════════════════════════════════════════════════[...]

# ─── Giờ Vàng TV config ──────────────────────────────────────────────────────
GIOVANG_ALL_JSON_URL  = "https://live-api.keonhacaitp.one/storage/livestream/all.json"
GIOVANG_LIVE_JSON_URL = "https://live-api.keonhacaitp.one/storage/livestream/live.json"
GIOVANG_STREAMS_URL   = "https://giovang.city/wp-json/custom-api/v1/streams"
GIOVANG_FRONTEND_URL = "https://giovang.city"

# ─── Pháo Hoa TV config ──────────────────────────────────────────────────────
PHAOHOA_API_BASE     = (os.environ.get("PHAOHOA_API") or "https://phaohoa1.live")
PHAOHOA_FRONTEND_URL = (os.environ.get("PHAOHOA_FRONTEND") or "https://phaohoa.live")

_GIOVANG_HDR = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
    "Referer":    GIOVANG_FRONTEND_URL + "/",
    "Accept":     "application/json, text/plain, */*",
}

_PHAOHOA_HDR = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
    "Referer":    PHAOHOA_FRONTEND_URL + "/",
    "Accept":     "application/json, text/plain, */*",
}


# ─── Giờ Vàng TV ─────────────────────────────────────────────────────────────

def _fetch_giovang_streams() -> dict:
    """Fetch BLV stream URLs từ giovang.city custom API.
    Trả về dict {blv_slug: stream_url} hoặc {} nếu API lỗi."""
    try:
        r = requests.get(GIOVANG_STREAMS_URL, timeout=12, headers=_GIOVANG_HDR)
        if r.status_code != 200:
            return {}
        data = r.json()
        if isinstance(data, list):
            result = {}
            for item in data:
                slug = item.get("slug") or item.get("blv") or item.get("select_blv") or ""
                url  = item.get("stream_url") or item.get("url") or item.get("hls") or ""
                if slug and url:
                    result[slug] = url
            return result
        if isinstance(data, dict) and data.get("code") in ("json_error", "api_error"):
            return {}
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if isinstance(v, str) and "m3u8" in v}
    except Exception:
        pass
    return {}


def _slugify_team(name: str) -> str:
    s = name.lower().strip()
    s = re.sub(r'[^a-z0-9\s-]', '', s)
    s = re.sub(r'[\s]+', '-', s)
    s = re.sub(r'-+', '-', s)
    return s.strip('-')


def _build_match_page_url(match: dict) -> str:
    mid = match.get('id', '')
    if not mid:
        return ''
    teams = match.get('teams', {})
    home = teams.get('home', {})
    away = teams.get('away', {})
    slug1 = home.get('slug', '') or _slugify_team(home.get('name', ''))
    slug2 = away.get('slug', '') or _slugify_team(away.get('name', ''))
    date = match.get('date', '').replace('/', '-')
    if not slug1 or not slug2 or not date:
        return ''
    return f"{GIOVANG_FRONTEND_URL}/truc-tiep-{slug1}-vs-{slug2}-{date}-{mid}/"


def _fetch_giovang_streams_from_pages(matches: list) -> dict:
    now_ts = time.time()
    active = []
    for m in matches:
        blv_list = m.get('blv') or []
        if not blv_list:
            continue
        is_live = bool(m.get('is_live'))
        time_start = int(m.get('time_start') or 0)
        elapsed = now_ts - time_start if time_start else 0
        if elapsed > MATCH_MAX_AGE_SECONDS and not is_live:
            continue
        if time_start and elapsed < -86400:
            continue
        active.append(m)

    if not active:
        return {}

    def _scrape(match):
        url = _build_match_page_url(match)
        if not url:
            return {}
        try:
            r = requests.get(url, headers=_GIOVANG_HDR, timeout=12)
            if not r.ok:
                return {}
            blv_attrs = re.findall(r'data-blv="([^"]+)"', r.text)
            result = {}
            for raw in blv_attrs:
                decoded = html.unescape(raw)
                try:
                    blvs = json.loads(decoded)
                    for blv in blvs:
                        key = blv.get('blv_key', '')
                        stream_url = (blv.get('pc_stream_url') or
                                      blv.get('mobile_stream_url') or
                                      blv.get('link_stream_hd') or
                                      blv.get('link_stream_sd') or '')
                        if key and stream_url:
                            result[key] = stream_url
                except Exception:
                    pass
            return result
        except Exception:
            return {}

    streams = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_scrape, m): m for m in active}
        for fut in as_completed(futures):
            try:
                streams.update(fut.result())
            except Exception:
                pass
    return streams


def _build_giovang_lines(matches: list, streams: dict) -> list:
    """Chuyển giovang.city match list thành M3U lines (chỉ trận có BLV và stream URL)."""
    now_ts = time.time()
    lines: list = []
    try:
        matches = sorted(matches, key=lambda m: m.get("time_start", 0))
    except Exception:
        pass

    for match in matches:
        is_live    = bool(match.get("is_live"))
        time_start = int(match.get("time_start") or 0)
        blv_list   = match.get("blv") or []
        if not blv_list:
            continue

        elapsed = now_ts - time_start if time_start else 0
        # Bỏ qua trận đã kết thúc hơn 2h hoặc quá xa trong tương lai (> 24h)
        if elapsed > MATCH_MAX_AGE_SECONDS and not is_live:
            continue
        if time_start and elapsed < -86400:
            continue

        t1     = (match.get("teams") or {}).get("home", {}).get("name", "Home").strip()
        t2     = (match.get("teams") or {}).get("away", {}).get("name", "Away").strip()
        league = ((match.get("league") or {}).get("title") or "").strip()
        logo   = _logo_from_text(t1 + " " + t2 + " " + league)

        if time_start:
            dt_vn    = datetime.fromtimestamp(time_start, tz=VN_TZ)
            time_str = dt_vn.strftime("%H:%M")
            date_str = dt_vn.strftime("%d/%m")
        else:
            time_str = "--:--"
            date_str = "--/--"

        for blv_id in blv_list:
            stream_url = streams.get(blv_id, "")
            if not stream_url:
                continue
            blv_display = blv_id.replace("blv-", "BLV ").replace("-", " ").title()
            display = f"{time_str} - {date_str} | {t1} VS {t2} ({league}) | {blv_display}"
            lines.append(f'#EXTINF:-1 tvg-logo="{logo}" group-title="Giờ Vàng TV",{display}')
            final_url = stream_url
            if "|" not in stream_url:
                final_url += f"|Referer={GIOVANG_FRONTEND_URL}/&User-Agent=Mozilla/5.0"
            lines.append(final_url)
    return lines


def fetch_giovang() -> list:
    """Nguồn Giờ Vàng TV từ giovang.city."""
    streams = _fetch_giovang_streams()

    # Fetch scheduled matches (all.json)
    r = requests.get(GIOVANG_ALL_JSON_URL, timeout=15, headers=_GIOVANG_HDR)
    r.raise_for_status()
    data    = r.json()
    matches = data.get("response", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])

    # Fetch live matches (live.json) and merge
    try:
        lr = requests.get(GIOVANG_LIVE_JSON_URL, timeout=15, headers=_GIOVANG_HDR)
        if lr.ok:
            ldata = lr.json()
            live_matches = ldata.get("response", []) if isinstance(ldata, dict) else (ldata if isinstance(ldata, list) else [])
            seen_ids = {m.get("id") for m in matches}
            for m in live_matches:
                if m.get("id") not in seen_ids:
                    matches.append(m)
                    seen_ids.add(m.get("id"))
    except Exception:
        pass

    if not matches:
        raise ValueError("giovang: không có trận đấu nào trong all.json/live.json")
    if not streams:
        print("  giovang: streams API lỗi, thử scrape trang trận đấu...", file=sys.stderr)
        streams = _fetch_giovang_streams_from_pages(matches)
        if streams:
            print(f"  giovang: scrape OK, {len(streams)} streams", file=sys.stderr)
    if not streams:
        raise ValueError("giovang: không lấy được stream URLs (API lỗi và scrape cũng thất bại)")
    lines = _build_giovang_lines(matches, streams)
    if not lines:
        raise ValueError("giovang: không có trận nào có BLV với stream URL khớp")
    return lines


# ─── Pháo Hoa TV ─────────────────────────────────────────────────────────────

def _fetch_phaohoa_matches() -> list:
    """Fetch scheduled + live matches từ phaohoa.live API."""
    results: list = []
    for status in ("live", "scheduled"):
        try:
            url = f"{PHAOHOA_API_BASE}/api/matches/?status={status}&ordering=start_time&page_size=100"
            r   = requests.get(url, timeout=15, headers=_PHAOHOA_HDR)
            if r.status_code == 200:
                data = r.json()
                results.extend(data.get("results") or [])
        except Exception:
            pass
    return results


def _build_phaohoa_lines(matches: list) -> list:
    """Chuyển phaohoa.live match list thành M3U lines (chỉ trận có BLV tiếng Việt)."""
    now_ts = time.time()
    lines: list = []
    seen_urls: set = set()
    try:
        matches = sorted(matches, key=lambda m: m.get("start_time") or "")
    except Exception:
        pass

    for match in matches:
        status = str(match.get("status") or "").lower().strip()
        if status in FINISHED_STATUS_STRINGS:
            continue

        start_time_str = match.get("start_time") or ""
        time_str = "--:--"
        date_str = "--/--"
        if start_time_str:
            try:
                dt      = datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))
                elapsed = now_ts - dt.timestamp()
                if elapsed > MATCH_MAX_AGE_SECONDS:
                    continue
                if elapsed < -86400:          # hơn 24h trong tương lai — bỏ qua
                    continue
                dt_vn    = dt.astimezone(VN_TZ)
                time_str = dt_vn.strftime("%H:%M")
                date_str = dt_vn.strftime("%d/%m")
            except Exception:
                pass

        t1     = (match.get("home_team_name") or "Home").strip()
        t2     = (match.get("away_team_name") or "Away").strip()
        league = (match.get("tournament_name") or "").strip()
        logo   = _logo_from_text(t1 + " " + t2 + " " + league)

        for comm in match.get("commentators") or []:
            stream_url = (comm.get("stream_url") or "").strip()
            if not stream_url:
                continue
            # Dedup theo URL trong cùng 1 lần chạy
            if stream_url in seen_urls:
                # Vẫn thêm entry mới với tên trận khác nhau, chỉ bỏ nếu cùng trận
                pass
            seen_urls.add(stream_url)
            name = (comm.get("name") or "BLV").strip()
            display = f"{time_str} - {date_str} | {t1} VS {t2} ({league}) | {name}"
            lines.append(f'#EXTINF:-1 tvg-logo="{logo}" group-title="Pháo Hoa TV",{display}')
            final_url = stream_url
            if "|" not in stream_url:
                final_url += f"|Referer={PHAOHOA_FRONTEND_URL}/&User-Agent=Mozilla/5.0"
            lines.append(final_url)
    return lines


def fetch_phaohoa() -> list:
    """Nguồn Pháo Hoa TV từ phaohoa.live."""
    matches = _fetch_phaohoa_matches()
    if not matches:
        raise ValueError("phaohoa: không fetch được dữ liệu trận đấu từ API")
    return _build_phaohoa_lines(matches)


def fetch_hoiquan() -> list:
    return _build_fixture_lines(_fetch_hoiquan_fixtures(), "Hội Quán TV")


def fetch_khandaia() -> list:
    return _build_fixture_lines(_fetch_khandaia_fixtures(), "Khán Đài A")


def fetch_vongcam() -> list:
    return _build_vongcam_lines(_fetch_vongcam_matches())


def fetch_vtv() -> list:
    try:
        return _fetch_vtv_lines()
    except Exception as e:
        print(f"⚠️  VTV thất bại: {e}", file=sys.stderr)
        return []


def main():
    # Tự động follow redirect để cập nhật domain thực tế của từng nguồn
    _resolve_all_frontends()
    print("🔄 Đang fetch dữ liệu từ 6 nguồn song song…")

    tasks = {
        "giovang":  fetch_giovang,
        "phaohoa":  fetch_phaohoa,
        "hoiquan":  fetch_hoiquan,
        "khandaia": fetch_khandaia,
        "vongcam":  fetch_vongcam,
        "vtv":      fetch_vtv,
    }

    results: dict[str, list] = {}
    errors:  list[str]       = []

    with ThreadPoolExecutor(max_workers=4) as executor:
        future_map = {executor.submit(fn): key for key, fn in tasks.items()}
        for future in as_completed(future_map):
            key = future_map[future]
            try:
                results[key] = future.result()
                count = sum(1 for l in results[key] if l.startswith("#EXTINF"))
                print(f"  ✅ {key}: {count} kênh")
            except Exception as exc:
                results[key] = []
                errors.append(f"{key}: {exc}")
                print(f"  ❌ {key}: {exc}", file=sys.stderr)

    giovang_lines  = results.get("giovang",  [])
    phaohoa_lines  = results.get("phaohoa",  [])
    hoiquan_lines  = results.get("hoiquan",  [])
    khandaia_lines = results.get("khandaia", [])
    vongcam_lines  = results.get("vongcam",  [])
    vtv_lines      = results.get("vtv",      [])

    all_lines = giovang_lines + phaohoa_lines + hoiquan_lines + khandaia_lines + vongcam_lines + vtv_lines

    total   = sum(1 for l in all_lines if l.startswith("#EXTINF"))
    content = "#EXTM3U\n" + "\n".join(all_lines)
    if errors:
        content += "\n# Errors: " + "; ".join(errors)

    with open("dekki.m3u", "w", encoding="utf-8") as f:
        f.write(content)

    gv_count = sum(1 for l in giovang_lines if l.startswith("#EXTINF"))
    ph_count = sum(1 for l in phaohoa_lines if l.startswith("#EXTINF"))
    vc_count = sum(1 for l in vongcam_lines if l.startswith("#EXTINF"))
    print(f"\n✅ Hoàn thành! Đã lưu {total} kênh vào 'dekki.m3u' (Giờ Vàng: {gv_count}, Pháo Hoa: {ph_count}, Vòng Cấm: {vc_count})")
    if errors:
        print(f"⚠️  Lỗi xảy ra: {'; '.join(errors)}", file=sys.stderr)


if __name__ == "__main__":
    main()
