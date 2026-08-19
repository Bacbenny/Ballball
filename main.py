import html
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta

import requests
from urllib.parse import quote

# ─── Shared config ───────────────────────────────────────────────────────────
VN_TZ                 = timezone(timedelta(hours=7))
MATCH_MAX_AGE_SECONDS = int(os.environ.get("MATCH_MAX_DURATION") or 7200)
FUTURE_WINDOW_SECONDS = int(os.environ.get("FUTURE_WINDOW") or 172800)  # 48h

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


# ─── VTV tĩnh ─────────────────────────────────────────────────────────────────
VTV_M3U_URL = (os.environ.get("VTV_M3U_URL") or
               "https://raw.githubusercontent.com/Bacbenny/Verceliptv/refs/heads/main/VTV.m3u")


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


def fetch_vtv() -> list:
    try:
        return _fetch_vtv_lines()
    except Exception as e:
        print(f"⚠️  VTV thất bại: {e}", file=sys.stderr)
        return []


# ─── Giờ Vàng TV ──────────────────────────────────────────────────────────────
GIOVANG_ALL_JSON_URL  = "https://live-api.keonhacaitp.one/storage/livestream/all.json"
GIOVANG_LIVE_JSON_URL = "https://live-api.keonhacaitp.one/storage/livestream/live.json"
GIOVANG_STREAMS_URL   = "https://giovang.city/wp-json/custom-api/v1/streams"
GIOVANG_FRONTEND_URL   = "https://giovang.city"
# Relay URL — set GIOVANG_RELAY_BASE env var để override khi Replit domain thay đổi
GIOVANG_RELAY_BASE = (
    os.environ.get("GIOVANG_RELAY_BASE") or
    "https://a9ad1c81-aa0e-4108-86a8-5b05fa562d96-00-1rp01pegi8sso.pike.replit.dev/api/giovang-relay"
)

_GIOVANG_HDR = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
    "Referer":    GIOVANG_FRONTEND_URL + "/",
    "Accept":     "application/json, text/plain, */*",
}

# Trận được coi là "sắp bắt đầu" nếu còn trong vòng 60 phút
PRE_MATCH_WINDOW_SECONDS = 3600


def _norm_blv(key: str) -> str:
    """Chuẩn hóa blv key: bỏ prefix blv- để so khớp nhất quán."""
    return key[4:] if key.startswith("blv-") else key


def _lookup_stream(streams: dict, blv_id: str):
    """Lookup stream data theo blv_id — trả về (url, name) hoặc ("", None)."""
    val = (streams.get(blv_id) or
           streams.get(_norm_blv(blv_id)) or
           streams.get(f"blv-{blv_id}"))
    if not val:
        return ("", None)
    # Support cả dạng cũ (str) và mới (tuple)
    if isinstance(val, tuple):
        return val
    return (val, None)


def _fetch_giovang_streams() -> dict:
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
                    # Lưu thêm dạng không có/có prefix blv- để lookup linh hoạt
                    norm = _norm_blv(slug)
                    if norm != slug:
                        result[norm] = url
                    prefixed = f"blv-{slug}"
                    if prefixed != slug and prefixed not in result:
                        result[prefixed] = url
            return result
        if isinstance(data, dict) and data.get("code") in ("json_error", "api_error"):
            return {}
        if isinstance(data, dict):
            result = {}
            for k, v in data.items():
                if isinstance(v, str) and "m3u8" in v:
                    result[k] = v
                    norm = _norm_blv(k)
                    if norm != k:
                        result[norm] = v
            return result
    except Exception:
        pass
    return {}


_VN_CHAR_MAP = {
    "à":"a","á":"a","ả":"a","ã":"a","ạ":"a","ă":"a","ằ":"a","ắ":"a","ẳ":"a","ẵ":"a","ặ":"a",
    "â":"a","ầ":"a","ấ":"a","ẩ":"a","ẫ":"a","ậ":"a","è":"e","é":"e","ẻ":"e","ẽ":"e","ẹ":"e",
    "ê":"e","ề":"e","ế":"e","ể":"e","ễ":"e","ệ":"e","ì":"i","í":"i","ỉ":"i","ĩ":"i","ị":"i",
    "ò":"o","ó":"o","ỏ":"o","õ":"o","ọ":"o","ô":"o","ồ":"o","ố":"o","ổ":"o","ỗ":"o","ộ":"o",
    "ơ":"o","ờ":"o","ớ":"o","ở":"o","ỡ":"o","ợ":"o","ù":"u","ú":"u","ủ":"u","ũ":"u","ụ":"u",
    "ư":"u","ừ":"u","ứ":"u","ử":"u","ữ":"u","ự":"u","ỳ":"y","ý":"y","ỷ":"y","ỹ":"y","ỵ":"y",
    "đ":"d","À":"a","Á":"a","Ả":"a","Ã":"a","Ạ":"a","Ă":"a","Ằ":"a","Ắ":"a","Ẳ":"a","Ẵ":"a",
    "Ặ":"a","Â":"a","Ầ":"a","Ấ":"a","Ẩ":"a","Ẫ":"a","Ậ":"a","È":"e","É":"e","Ẻ":"e","Ẽ":"e",
    "Ẹ":"e","Ê":"e","Ề":"e","Ế":"e","Ể":"e","Ễ":"e","Ệ":"e","Ì":"i","Í":"i","Ỉ":"i","Ĩ":"i",
    "Ị":"i","Ò":"o","Ó":"o","Ỏ":"o","Õ":"o","Ọ":"o","Ô":"o","Ồ":"o","Ố":"o","Ổ":"o","Ỗ":"o",
    "Ộ":"o","Ơ":"o","Ờ":"o","Ớ":"o","Ở":"o","Ỡ":"o","Ợ":"o","Ù":"u","Ú":"u","Ủ":"u","Ũ":"u",
    "Ụ":"u","Ư":"u","Ừ":"u","Ứ":"u","Ử":"u","Ữ":"u","Ự":"u","Ỳ":"y","Ý":"y","Ỷ":"y","Ỹ":"y",
    "Ỵ":"y","Đ":"d",
}


def _slugify_team(name: str) -> str:
    """Slugify có hỗ trợ tiếng Việt có dấu."""
    import unicodedata as _ud
    text = _ud.normalize("NFC", str(name))
    result = "".join(_VN_CHAR_MAP.get(c, c.lower()) for c in text)
    result = re.sub(r"[^a-z0-9]+", "-", result)
    return result.strip("-")


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
        if time_start and elapsed < -FUTURE_WINDOW_SECONDS:
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
                            blv_name = (blv.get("blv_name") or "").strip() or None
                            result[key] = (stream_url, blv_name)
                            norm = _norm_blv(key)
                            if norm != key:
                                result[norm] = (stream_url, blv_name)
                            prefixed = f"blv-{key}"
                            if prefixed != key and prefixed not in result:
                                result[prefixed] = (stream_url, blv_name)
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


def _giovang_logo(match: dict) -> str:
    """Use team logo from API data; fall back to sport-type emoji."""
    teams = match.get("teams") or {}
    home_logo = (teams.get("home") or {}).get("logo", "")
    if home_logo:
        return home_logo
    away_logo = (teams.get("away") or {}).get("logo", "")
    if away_logo:
        return away_logo
    league_icon = ((match.get("league") or {}).get("icon") or "")
    if league_icon:
        return league_icon
    sport_type = (match.get("type") or "").lower()
    return SPORT_LOGOS.get(sport_type, SPORT_LOGOS["default"])


def _build_giovang_lines(matches: list, streams: dict) -> list:
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
        if elapsed > MATCH_MAX_AGE_SECONDS and not is_live:
            continue
        if time_start and elapsed < -FUTURE_WINDOW_SECONDS:
            continue

        t1     = (match.get("teams") or {}).get("home", {}).get("name", "Home").strip()
        t2     = (match.get("teams") or {}).get("away", {}).get("name", "Away").strip()
        league = ((match.get("league") or {}).get("title") or "").strip()
        logo   = _giovang_logo(match)

        if time_start:
            dt_vn    = datetime.fromtimestamp(time_start, tz=VN_TZ)
            time_str = dt_vn.strftime("%H:%M")
            date_str = dt_vn.strftime("%d/%m")
        else:
            time_str = "--:--"
            date_str = "--/--"

        for blv_id in blv_list:
            blv_display = blv_id.replace("blv-", "BLV ").replace("-", " ").title()
            display = f"{time_str} - {date_str} | {t1} VS {t2} ({league}) | {blv_display}"
            if GIOVANG_RELAY_BASE:
                # Relay mode: URL fetch real-time khi player click — không cần stream URL tĩnh
                page_url = _build_match_page_url(match)
                if not page_url:
                    continue
                final_url = (f"{GIOVANG_RELAY_BASE}"
                             f"?page={quote(page_url, safe='')}"
                             f"&blv={quote(blv_id, safe='')}")
            else:
                # Direct mode: lấy URL tại thời điểm tạo M3U
                stream_url, blv_name = _lookup_stream(streams, blv_id)
                if not stream_url:
                    continue
                blv_display = blv_name or blv_display
                display = f"{time_str} - {date_str} | {t1} VS {t2} ({league}) | {blv_display}"
                final_url = stream_url
                if "|" not in stream_url:
                    final_url += f"|Referer={GIOVANG_FRONTEND_URL}/&User-Agent=Mozilla/5.0"
            lines.append(f'#EXTINF:-1 tvg-logo="{logo}" group-title="Giờ Vàng TV",{display}')
            lines.append(final_url)
    return lines


def _fetch_giovang_matches() -> list:
    """Fetch danh sách trận từ all.json + live.json, merge không trùng lặp."""
    r = requests.get(GIOVANG_ALL_JSON_URL, timeout=15, headers=_GIOVANG_HDR)
    r.raise_for_status()
    data    = r.json()
    matches = data.get("response", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
    try:
        lr = requests.get(GIOVANG_LIVE_JSON_URL, timeout=15, headers=_GIOVANG_HDR)
        if lr.ok:
            ldata = lr.json()
            live_matches = ldata.get("response", []) if isinstance(ldata, dict) else (ldata if isinstance(ldata, list) else [])
            seen_ids = {m.get("id") for m in matches}
            for m in live_matches:
                if m.get("id") not in seen_ids:
                    matches.insert(0, m)
                    seen_ids.add(m.get("id"))
    except Exception:
        pass
    return matches


def fetch_giovang() -> list:
    """Nguồn Giờ Vàng TV.
    - Relay mode (GIOVANG_RELAY_BASE set): chỉ cần danh sách trận → build relay URL,
      stream URL thực được fetch real-time khi player click.
    - Direct mode: scrape từng trang trận để lấy URL tĩnh (dễ stale khi CDN rotate)."""
    matches = _fetch_giovang_matches()
    if not matches:
        raise ValueError("giovang: không có trận đấu nào trong all.json/live.json")

    if GIOVANG_RELAY_BASE:
        # ── Relay mode ──────────────────────────────────────────────────────────
        lines = _build_giovang_lines(matches, {})
        if not lines:
            raise ValueError("giovang: không có trận nào trong thời gian hợp lệ")
        n = sum(1 for l in lines if l.startswith('#EXTINF'))
        print(f"  giovang: relay mode — {n} kênh (URL fetch real-time)", file=sys.stderr)
        return lines

    # ── Direct mode ─────────────────────────────────────────────────────────────
    streams = _fetch_giovang_streams_from_pages(matches)
    if not streams:
        raise ValueError("giovang: không lấy được stream URLs từ trang trận đấu")
    lines = _build_giovang_lines(matches, streams)
    if not lines:
        raise ValueError("giovang: không có trận nào có BLV với stream URL khớp")
    return lines


# ─── Pháo Hoa TV ──────────────────────────────────────────────────────────────
PHAOHOA_API_BASE     = (os.environ.get("PHAOHOA_API") or "https://phaohoa1.live")
PHAOHOA_FRONTEND_URL = (os.environ.get("PHAOHOA_FRONTEND") or "https://phaohoa.live")

_PHAOHOA_HDR = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
    "Referer":    PHAOHOA_FRONTEND_URL + "/",
    "Accept":     "application/json, text/plain, */*",
}


def _fetch_phaohoa_matches() -> list:
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


def _phaohoa_logo(match: dict) -> str:
    """Use team/tournament logo from API; fall back to sport-type emoji."""
    home_logo = match.get("home_team_logo") or ""
    if home_logo and home_logo != "None":
        return f"{PHAOHOA_API_BASE}{home_logo}"
    away_logo = match.get("away_team_logo") or ""
    if away_logo and away_logo != "None":
        return f"{PHAOHOA_API_BASE}{away_logo}"
    tour_icon = match.get("tournament_icon_url") or ""
    if tour_icon and tour_icon != "None":
        return f"{PHAOHOA_API_BASE}{tour_icon}"
    sport_slug = (match.get("sport_slug") or "").lower()
    return SPORT_LOGOS.get(sport_slug, SPORT_LOGOS["default"])


def _build_phaohoa_lines(matches: list) -> list:
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
                if elapsed < -FUTURE_WINDOW_SECONDS:
                    continue
                dt_vn    = dt.astimezone(VN_TZ)
                time_str = dt_vn.strftime("%H:%M")
                date_str = dt_vn.strftime("%d/%m")
            except Exception:
                pass

        t1     = (match.get("home_team_name") or "Home").strip()
        t2     = (match.get("away_team_name") or "Away").strip()
        league = (match.get("tournament_name") or "").strip()
        logo   = _phaohoa_logo(match)

        for comm in match.get("commentators") or []:
            stream_url = (comm.get("stream_url") or "").strip()
            if not stream_url:
                continue
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
    matches = _fetch_phaohoa_matches()
    if not matches:
        raise ValueError("phaohoa: không fetch được dữ liệu trận đấu từ API")
    return _build_phaohoa_lines(matches)


# ─── Footy Live ───────────────────────────────────────────────────────────────
# Footy Live exposes the same match data used by its web page.  Each match may
# have several providers/servers; the playlist intentionally keeps only the
# best one so IPTV clients do not show duplicate programs.
FOOTYLIVE_BASE_URL = (
    os.environ.get("FOOTYLIVE_BASE_URL") or "https://footylive.vercel.app"
).rstrip("/")
FOOTYLIVE_MATCHES_URL = f"{FOOTYLIVE_BASE_URL}/api/matches"
FOOTYLIVE_FUTURE_WINDOW_SECONDS = int(
    os.environ.get("FOOTYLIVE_FUTURE_WINDOW") or 86400
)
FOOTYLIVE_RELAY_BASE = (
    os.environ.get("FOOTYLIVE_RELAY_BASE")
    or "https://dekki.bacbenny95.workers.dev/footylive"
).rstrip("/")
FOOTYLIVE_HDR = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": FOOTYLIVE_BASE_URL + "/",
}


def _footylive_absolute_url(url: str) -> str:
    url = str(url or "").strip()
    if url.startswith("/"):
        return FOOTYLIVE_BASE_URL + url
    return url


def _footylive_quality_rank(quality: str) -> int:
    value = str(quality or "").upper().replace(" ", "")
    if value in {"4K", "2160P", "UHD", "FHD", "1080P", "1080"}:
        return 0
    if value in {"HD", "720P", "720"}:
        return 1
    if value in {"SD", "480P", "480", "360P", "360"}:
        return 2
    return 3


def _footylive_provider_rank(source: dict) -> int:
    provider = str(source.get("provider") or source.get("source") or "").lower()
    # WatchFooty is FootyLive's primary provider.  Other providers are useful
    # fallbacks, but should not replace an equal-quality primary stream.
    if "watchfooty" in provider or provider.startswith("wf-"):
        return 0
    if "streamed" in provider:
        return 1
    if "cdn" in provider:
        return 2
    return 3


def _choose_footylive_stream(sources: list) -> dict | None:
    """Return one stable, non-proxied source with the best quality."""
    candidates = []
    seen_urls = set()
    for index, source in enumerate(sources or []):
        if not isinstance(source, dict):
            continue
        url = str(source.get("url") or "").strip()
        # proxiedUrl is signed for a short period and must not be persisted in
        # the repository playlist.  Use the original stream/embed URL instead.
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        candidates.append((
            _footylive_quality_rank(source.get("quality")),
            _footylive_provider_rank(source),
            index,
            source,
        ))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[:3])
    return candidates[0][3]


def _footylive_match_in_window(match: dict, now_ms: int) -> bool:
    status = str(match.get("status") or "").strip().lower().replace("-", "_")
    timestamp = int(match.get("timestamp") or 0)
    # The API has returned both Unix seconds and Unix milliseconds over time.
    if 0 < timestamp < 100_000_000_000:
        timestamp *= 1000
    if status in {"live", "in_progress", "inprogress"}:
        # Trust the provider's active status even when kickoff is stale.
        return True
    if status in {"upcoming", "scheduled", "not_started", "notstarted"} and not timestamp:
        return True
    if not timestamp:
        return False
    return (
        now_ms - MATCH_MAX_AGE_SECONDS * 1000
        <= timestamp
        <= now_ms + FOOTYLIVE_FUTURE_WINDOW_SECONDS * 1000
    )


def _fetch_footylive_stream(match: dict) -> tuple[str, dict | None]:
    """Choose only sources attached to this match by the list endpoint.

    Do not call /api/streams/{id} here: that endpoint can aggregate provider
    fallbacks whose generic embed URL is occasionally assigned to the wrong
    fixture.  A missing source is safer than a misleading program.
    """
    match_id = str(match.get("id") or "").strip()
    source = _choose_footylive_stream(match.get("sources") or [])
    return match_id, source


def _footylive_logo(match: dict) -> str:
    home = match.get("homeTeam") or {}
    away = match.get("awayTeam") or {}
    for value in (
        home.get("badge"),
        away.get("badge"),
        match.get("leagueLogo"),
        match.get("poster"),
    ):
        url = _footylive_absolute_url(value)
        if url:
            return url
    return SPORT_LOGOS["football"]


def _footylive_relay_url(match_id: str) -> str:
    if not FOOTYLIVE_RELAY_BASE or not match_id:
        return ""
    return f"{FOOTYLIVE_RELAY_BASE}/{quote(str(match_id), safe='')}"


def _build_footylive_lines(matches: list, sources_by_id: dict) -> list:
    now_ms = int(time.time() * 1000)
    ordered = sorted(
        matches,
        key=lambda item: (
            0 if str(item.get("status") or "").lower() == "live" else 1,
            int(item.get("timestamp") or 0),
            str(item.get("title") or ""),
        ),
    )
    lines = []
    seen_match_ids = set()

    for match in ordered:
        match_id = str(match.get("id") or "").strip()
        if not match_id or match_id in seen_match_ids:
            continue
        if not _footylive_match_in_window(match, now_ms):
            continue
        source = sources_by_id.get(match_id)
        direct_url = _footylive_absolute_url((source or {}).get("url"))
        relay_url = _footylive_relay_url(match_id)
        if relay_url:
            # Resolve the source only when the IPTV client clicks the item.
            # This keeps upcoming fixtures visible before their stream exists
            # and avoids persisting short-lived provider URLs in the M3U.
            stream_url = relay_url
        elif direct_url.startswith(("http://", "https://")):
            stream_url = direct_url
        else:
            continue

        seen_match_ids.add(match_id)
        timestamp = int(match.get("timestamp") or 0)
        if timestamp:
            kickoff = datetime.fromtimestamp(timestamp / 1000, tz=VN_TZ)
            time_label = kickoff.strftime("%H:%M:%S - %d/%m")
        else:
            time_label = "--:--:-- - --/--"

        home = str((match.get("homeTeam") or {}).get("name") or "").strip()
        away = str((match.get("awayTeam") or {}).get("name") or "").strip()
        title = (
            f"{home} VS {away}"
            if home and away
            else str(match.get("title") or "Football match").strip()
        )
        tournament = str(match.get("tournament") or "").strip()
        details = f"{time_label} | {title}"
        if tournament:
            details += f" | {tournament}"

        safe_details = details.replace('"', "'")
        lines.append(
            f'#EXTINF:-1 tvg-id="footylive-{match_id}" '
            f'tvg-name="{safe_details}" tvg-logo="{_footylive_logo(match)}" '
            f'group-title="Footy Live",{details}'
        )
        lines.append(stream_url)
    return lines


def fetch_footylive() -> list:
    """Fetch Footy Live programs and keep exactly one best source per match."""
    response = requests.get(FOOTYLIVE_MATCHES_URL, headers=FOOTYLIVE_HDR, timeout=20)
    response.raise_for_status()
    payload = response.json()
    matches = payload.get("matches") if isinstance(payload, dict) else payload
    if not isinstance(matches, list):
        raise ValueError("footylive: API không trả về danh sách trận đấu")

    now_ms = int(time.time() * 1000)
    candidates = [m for m in matches if isinstance(m, dict) and _footylive_match_in_window(m, now_ms)]
    sources_by_id = {}
    for match in candidates:
        match_id, source = _fetch_footylive_stream(match)
        if match_id and source:
            sources_by_id[match_id] = source

    lines = _build_footylive_lines(candidates, sources_by_id)
    if not lines:
        raise ValueError("footylive: không có trận nào có stream trong thời gian hợp lệ")
    count = sum(1 for line in lines if line.startswith("#EXTINF"))
    print(
        f"  footylive: {count} trận, mỗi trận chỉ giữ 1 stream tốt nhất",
        file=sys.stderr,
    )
    return lines


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("🔄 Đang fetch dữ liệu từ 4 nguồn song song…")

    tasks = {
        "giovang":  fetch_giovang,
        "phaohoa":  fetch_phaohoa,
        "footylive": fetch_footylive,
        "vtv":      fetch_vtv,
    }

    results: dict[str, list] = {}
    errors:  list[str]       = []

    with ThreadPoolExecutor(max_workers=3) as executor:
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

    giovang_lines = results.get("giovang", [])
    phaohoa_lines = results.get("phaohoa", [])
    footylive_lines = results.get("footylive", [])
    vtv_lines     = results.get("vtv",     [])

    all_lines = giovang_lines + phaohoa_lines + footylive_lines + vtv_lines

    total   = sum(1 for l in all_lines if l.startswith("#EXTINF"))
    content = "#EXTM3U\n" + "\n".join(all_lines)
    if errors:
        content += "\n# Errors: " + "; ".join(errors)

    with open("dekki.m3u", "w", encoding="utf-8") as f:
        f.write(content)

    gv_count = sum(1 for l in giovang_lines if l.startswith("#EXTINF"))
    ph_count = sum(1 for l in phaohoa_lines if l.startswith("#EXTINF"))
    fl_count = sum(1 for l in footylive_lines if l.startswith("#EXTINF"))
    print(
        f"\n✅ Hoàn thành! Đã lưu {total} kênh vào 'dekki.m3u' "
        f"(Giờ Vàng: {gv_count}, Pháo Hoa: {ph_count}, Footy Live: {fl_count})"
    )
    if errors:
        print(f"⚠️  Lỗi xảy ra: {'; '.join(errors)}", file=sys.stderr)


if __name__ == "__main__":
    main()
