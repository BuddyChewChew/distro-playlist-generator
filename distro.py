import requests
import json
import re
import time
import uuid
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

# ==================== CONFIG ====================
M3U_OUTPUT = "distrotv.m3u"
JSON_OUTPUT = "distrotv_raw.json"

# Add or remove geos here
GEOS = ["US", "JP", "CA", "MX"]   # <-- Change this list as needed

ANDROID_UA = "Dalvik/2.1.0 (Linux; U; Android 9; AFTT Build/STT9.221129.002) GTV/AFTT DistroTV/2.0.9"
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"

HLS_HEADERS = {
    "User-Agent": BROWSER_UA,
    "Origin": "https://distro.tv",
    "Referer": "https://distro.tv/",
}

FEED_BASE = "https://tv.jsrdn.com/tv_v5/getfeed.php?type=live"

MACRO_RE = re.compile(r"__[^_].*?__")

MACRO_REPLACEMENTS = {
    "__CACHE_BUSTER__":           lambda: str(int(time.time() * 1000)),
    "__DEVICE_ID__":              lambda: str(uuid.uuid4()),
    "__LIMIT_AD_TRACKING__":      lambda: "0",
    "__IS_GDPR__":                lambda: "0",
    "__IS_CCPA__":                lambda: "0",
    "__GEO_COUNTRY__":            lambda: "US",   # you can make this dynamic per geo if needed
    "__LATITUDE__":               lambda: "",
    "__LONGITUDE__":              lambda: "",
    "__GEO_DMA__":                lambda: "",
    "__GEO_TYPE__":               lambda: "",
    "__PAGEURL_ESC__":            lambda: "https%3A%2F%2Fdistro.tv%2F",
    "__STORE_URL__":              lambda: "https%3A%2F%2Fdistro.tv%2F",
    "__APP_BUNDLE__":             lambda: "distro.tv",
    "__APP_VERSION__":            lambda: "0",
    "__APP_CATEGORY__":           lambda: "",
    "__WIDTH__":                  lambda: "1920",
    "__HEIGHT__":                 lambda: "1080",
    "__DEVICE__":                 lambda: "Linux",
    "__DEVICE_ID_TYPE__":         lambda: "uuid",
    "__DEVICE_CONNECTION_TYPE__": lambda: "",
    "__DEVICE_CATEGORY__":        lambda: "desktop",
    "__env.i__":                  lambda: "web",
    "__env.u__":                  lambda: "web",
    "__PALN__":                   lambda: "",
    "__GDPR_CONSENT__":           lambda: "",
    "__ADVERTISING_ID__":         lambda: "",
    "__CLIENT_IP__":              lambda: "",
}

_LANG_TAGS = {'English', 'Spanish', 'Asian', 'African', 'Arabic', 'Middle Eastern', 'French', 'Portuguese', 'Hindi', 'Urdu', 'Korean', 'Japanese', 'Chinese', 'Tagalog', 'Vietnamese', 'Russian'}

_LANG_CODE = {
    'English': 'en', 'Spanish': 'es', 'French': 'fr', 'Portuguese': 'pt',
    'Hindi': 'hi', 'Urdu': 'ur', 'Korean': 'ko', 'Japanese': 'ja',
    'Chinese': 'zh', 'Tagalog': 'tl', 'Vietnamese': 'vi', 'Russian': 'ru', 'Arabic': 'ar',
}

def _parse_distro_tags(raw: str):
    if not raw:
        return "DistroTV", "en"
    tags = [t.strip() for t in raw.split(',') if t.strip()]
    genre_tags = []
    lang = 'en'
    for tag in tags:
        if tag in _LANG_TAGS:
            lang = _LANG_CODE.get(tag, tag.lower())
            break
        else:
            genre_tags.append(tag)
    category = genre_tags[0] if genre_tags else "DistroTV"
    return category, lang


def _sanitize_url(url: str) -> str:
    parts = urlsplit(url)
    q = parse_qsl(parts.query, keep_blank_values=True)
    sanitized = []
    for k, v in q:
        if v in MACRO_REPLACEMENTS:
            v = MACRO_REPLACEMENTS[v]()
        elif MACRO_RE.search(v or ""):
            v = ""
        sanitized.append((k, v))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(sanitized, doseq=True), ""))


def fetch_and_process():
    print("=== DistroTV Rescraper with Multi-Geo (including JP) ===")
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Geos to scrape: {', '.join(GEOS)}\n")

    session = requests.Session()
    session.headers.update({"User-Agent": ANDROID_UA, "Accept": "application/json,*/*"})

    all_channels = []
    m3u = ["#EXTM3U"]
    m3u.append(f'# Generated on {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} - Geos: {", ".join(GEOS)}')

    for geo in GEOS:
        url = f"{FEED_BASE}&geo={geo}" if geo != "US" else FEED_BASE
        print(f"Fetching geo={geo} → {url}")

        try:
            r = session.get(url, timeout=30)
            print(f"  Status: {r.status_code} | Size: {len(r.text):,} bytes")
            r.raise_for_status()
            feed = r.json()

            # Extract shows (handles dict or list under "shows")
            shows = []
            if isinstance(feed, dict) and "shows" in feed:
                shows_data = feed["shows"]
                shows = list(shows_data.values()) if isinstance(shows_data, dict) else shows_data if isinstance(shows_data, list) else []
            else:
                shows = [v for v in feed.values() if isinstance(v, dict)] if isinstance(feed, dict) else feed if isinstance(feed, list) else []

            print(f"  Found {len(shows)} items for geo={geo}")

            added_this_geo = 0
            for show in shows:
                if not isinstance(show, dict) or show.get("type") != "live":
                    continue

                name = (show.get("title") or "").strip()
                if not name:
                    continue

                logo = show.get("img_logo") or ""
                raw_genre = show.get("genre") or ""
                category, lang = _parse_distro_tags(raw_genre)

                # Get stream from first episode
                seasons = show.get("seasons") or []
                if not seasons or not isinstance(seasons[0], dict):
                    continue
                episodes = seasons[0].get("episodes") or []
                if not episodes or not isinstance(episodes[0], dict):
                    continue

                ep = episodes[0]
                content = ep.get("content") or {}
                raw_url = content.get("url")
                if not raw_url:
                    continue

                stream_url = _sanitize_url(raw_url)

                # Optional: Try to resolve master playlist (keeps it simple for now)
                if stream_url.endswith(".m3u8"):
                    try:
                        resp = session.get(stream_url, headers=HLS_HEADERS, timeout=10)
                        if resp.status_code == 200:
                            pass  # you can add best variant logic here later
                    except:
                        pass

                tvg_id = f"distro.{geo}.{show.get('id', len(all_channels))}"
                group = f"DistroTV • {geo} • {category}"

                m3u.append(f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-logo="{logo}" group-title="{group}",{name} [{geo}]')
                m3u.append(stream_url)

                added_this_geo += 1
                all_channels.append(show)

            print(f"  ✓ Added {added_this_geo} channels from geo={geo}\n")

        except Exception as e:
            print(f"  ❌ Failed for geo={geo}: {e}\n")

    # Save files
    with open(M3U_OUTPUT, "w", encoding="utf-8") as f:
        f.write("\n".join(m3u) + "\n")

    with open(JSON_OUTPUT, "w", encoding="utf-8") as f:
        json.dump(all_channels, f, indent=2, ensure_ascii=False)

    total = len(m3u) // 2 - 1   # rough count
    print(f"🎉 Finished! Total channels in M3U: {total}")
    print(f"   M3U  → {M3U_OUTPUT}")
    print(f"   JSON → {JSON_OUTPUT}")
    print(f"   Ready to update your apsattv.com distro.m3u")


if __name__ == "__main__":
    fetch_and_process()
    input("\nPress Enter to exit...")
