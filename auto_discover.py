#!/usr/bin/env python3
"""auto_discover.py — Tự động phát hiện và cập nhật API URLs cho Pháo Hoa TV.
Chay thu cong hoac qua GitHub Actions moi 3 gio.
"""
import os, re, sys, requests
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

CF_TOKEN = os.environ.get("CF_API_TOKEN") or os.environ.get("CLOUDFLARE_API_TOKEN", "")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")

SOURCES = {
    "phaohoa": {
        "frontend":  (os.environ.get("PHAOHOA_FRONTEND") or "https://phaohoa.live"),
        "known_api": (os.environ.get("PHAOHOA_API")      or "https://phaohoa1.live"),
        "env_key":   "PHAOHOA_API",
    },
}


def _get(url, headers=None, timeout=10, **kw):
    h = {"User-Agent": UA, "Accept": "application/json, */*"}
    if headers: h.update(headers)
    return requests.get(url, headers=h, timeout=timeout, **kw)


def _fetch_js_bundles(frontend_url: str, max_js: int = 5) -> list[str]:
    try:
        html = _get(frontend_url, timeout=12).text
    except Exception:
        return []
    js_paths = re.findall(r'src="(/[^"]+\.js)"', html)
    if not js_paths:
        js_paths = re.findall(r'"(/assets/[^"]+\.js)"', html)
    results = []
    for p in js_paths[:max_js]:
        try:
            js = _get(frontend_url.rstrip("/") + p, timeout=20).text
            results.append(js)
        except Exception:
            pass
    return results


def _extract_api_url(js: str, patterns: list[str]) -> str | None:
    for pat in patterns:
        hits = re.findall(pat, js)
        for hit in hits:
            if any(x in hit for x in ["cdn", "pull", "jsdelivr", "twemoji", "flashscore"]):
                continue
            return hit.rstrip("/")
    return None


def discover_phaohoa(known: str) -> tuple[str, str]:
    patterns = [
        r'VITE_SERVER_API_BASE_URL:\s*"(https://[^"]+)"',
        r'VITE_API_BASE(?:_URL)?:\s*"(https://[^"]+)"',
        r'baseURL:\s*"(https://[^"]+)"',
        r'"(https://phaohoa\d*\.live)"',
    ]
    frontend = SOURCES["phaohoa"]["frontend"]
    for js in _fetch_js_bundles(frontend, max_js=5):
        url = _extract_api_url(js, patterns)
        if url:
            try:
                r = _get(f"{url}/api/matches/?status=live&page_size=1",
                         headers={"Referer": frontend + "/"},
                         timeout=6)
                if r.ok:
                    return url, "js"
            except Exception:
                pass

    for dom in ["phaohoa1.live", "phaohoa2.live", "phaohoa3.live"]:
        try:
            url = f"https://{dom}"
            r = _get(f"{url}/api/matches/?status=live&page_size=1",
                     headers={"Referer": frontend + "/"}, timeout=4)
            if r.ok:
                return url, "probe"
        except Exception:
            pass
    return known, "known"


MAIN_PY_PATH = os.path.join(os.path.dirname(__file__), "main.py")


def _update_main_py(key: str, new_url: str) -> bool:
    try:
        with open(MAIN_PY_PATH, "r") as f:
            src = f.read()
        pat = r'(PHAOHOA_API_BASE\s*=\s*\(os\.environ\.get\("PHAOHOA_API"\)\s*or\s*)"https://[^"]+"'
        new_src = re.sub(pat, lambda m: m.group(1) + f'"{new_url}"', src, count=1)
        if new_src == src:
            return False
        with open(MAIN_PY_PATH, "w") as f:
            f.write(new_src)
        return True
    except Exception as e:
        print(f"  main.py patch error: {e}")
        return False


def main():
    print()
    print("=" * 65)
    print("  BallBall Auto-Discover — %s UTC"
          % datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 65)
    print()

    changed = []
    errors  = []

    tasks = [
        ("phaohoa", discover_phaohoa, SOURCES["phaohoa"]["known_api"]),
    ]

    results = {}
    with ThreadPoolExecutor(max_workers=3) as ex:
        futs = {}
        for name, fn, known in tasks:
            def run(n, f, k):
                try:
                    new_url, method = f(k)
                    return n, new_url, method, None
                except Exception as e:
                    return n, k, "error", str(e)
            futs[ex.submit(run, name, fn, known)] = name
        for fut in as_completed(futs):
            name, new_url, method, err = fut.result()
            results[name] = (new_url, method, err)

    for name, fn, known in tasks:
        new_url, method, err = results[name]
        status = "ERROR" if err else ("NEW" if new_url != known else "OK ")
        print(f"  [{status}] {name:12s} -> {new_url}  (via {method})")
        if err:
            print(f"           Error: {err}")
            errors.append(name)
        elif new_url != known:
            changed.append((name, known, new_url))

    print()

    if not changed:
        print("  Ket qua: khong co URL nao thay doi.")
    else:
        print(f"  Phat hien {len(changed)} thay doi — dang cap nhat...")
        for name, old, new in changed:
            print(f"\n  {name}: {old}")
            print(f"       -> {new}")
            updated_main = _update_main_py(name, new)
            print(f"     main.py: {'OK' if updated_main else 'skip (no match)'}")

    print()
    print("=" * 65)
    print("  Hoan thanh: %d thay doi, %d loi" % (len(changed), len(errors)))
    print("=" * 65)
    print()
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
