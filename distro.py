import requests
import json
import re
import time
import uuid
import os
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# ==================== CONFIG ====================
OUTPUT_DIR = "playlists"
JSON_OUTPUT = os.path.join(OUTPUT_DIR, "distrotv_raw.json")
EPG_OUTPUT = os.path.join(OUTPUT_DIR, "distrotv.xml")
M3U_ALL = os.path.join(OUTPUT_DIR, "distrotv_all.m3u")

GITHUB_REPO = os.getenv("GITHUB_REPOSITORY", "username/repo")
EPG_URL = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/playlists/distrotv.xml"

GEOS = ["US", "JP", "CA", "MX"]

# Standard Browser UA that Cloudfront/Distro expects
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"
REFERER = "https://distro.tv/"

FEED_BASE = "https://tv.jsrdn.com/tv_v5/getfeed.php?type=live"
MACRO_RE = re.compile(r"__[^_].*?__")

MACRO_REPLACEMENTS = {
    "__CACHE_BUSTER__":         lambda: str(int(time.time() * 1000)),
    "__DEVICE_ID__":            lambda: str(uuid.uuid4()),
    "__LIMIT_AD_TRACKING__":    lambda: "0",
    "__IS_GDPR__":              lambda: "0",
    "__IS_CCPA__":              lambda: "0",
    "__GEO_COUNTRY__":          lambda: "US",
    "__PAGEURL_ESC__":          lambda: "https%3A%2F%2Fdistro.tv%2F",
    "__STORE_URL__":            lambda: "https%3A%2F%2Fdistro.tv%2F",
    "__APP_BUNDLE__":           lambda: "distro.tv",
    "__WIDTH__":                lambda: "1920",
    "__HEIGHT__":               lambda: "1080",
    "__DEVICE__":               lambda: "Linux",
    "__DEVICE_ID_TYPE__":       lambda: "uuid",
    "__DEVICE_CATEGORY__":      lambda: "desktop",
    "__env.i__":                lambda: "web",
    "__env.u__":                lambda: "web",
}

_LANG_TAGS = {'English', 'Spanish', 'Asian', 'African', 'Arabic', 'Middle Eastern', 'French', 'Portuguese', 'Hindi', 'Urdu', 'Korean', 'Japanese', 'Chinese', 'Tagalog', 'Vietnamese', 'Russian'}
_LANG_CODE = {
    'English': 'en', 'Spanish': 'es', 'French': 'fr', 'Portuguese': 'pt',
    'Hindi': 'hi', 'Urdu': 'ur', 'Korean': 'ko', 'Japanese': 'ja',
    'Chinese': 'zh', 'Tagalog': 'tl', 'Vietnamese': 'vi', 'Russian': 'ru', 'Arabic': 'ar',
}

def _parse_distro_tags(raw: str):
    if not raw: return "DistroTV", "en"
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
    # Build URL and append player headers for TiviMate/VLC compatibility
    base_url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(sanitized, doseq=True), ""))
    return f"{base_url}|User-Agent={BROWSER_UA}&Referer={REFERER}"

def fetch_and_process():
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)

    session = requests.Session()
    # Using the same UA for the scraper itself
    session.headers.update({"User-Agent": BROWSER_UA, "Accept": "application/json,*/*"})
    
    unique_channels = {} 
    geo_playlists = {geo: [] for geo in GEOS}
    xml_root = ET.Element("tv", {"generator-info-name": "DistroTV-Scraper"})

    for geo in GEOS:
        print(f"Scraping Geo: {geo}...")
        url = f"{FEED_BASE}&geo={geo}" if geo != "US" else FEED_BASE
        try:
            r = session.get(url, timeout=30)
            r.raise_for_status()
            feed = r.json()
            
            shows_data = feed.get("shows", {})
            shows = list(shows_data.values()) if isinstance(shows_data, dict) else shows_data

            for show in shows:
                if not isinstance(show, dict) or show.get("type") != "live":
                    continue
                
                show_id = str(show.get('id'))
                name = (show.get("title") or "").strip()
                logo = show.get("img_logo") or ""
                category, lang = _parse_distro_tags(show.get("genre") or "")
                
                seasons = show.get("seasons") or []
                if not seasons or not seasons[0].get("episodes"):
                    continue
                
                ep = seasons[0]["episodes"][0]
                raw_url = ep.get("content", {}).get("url")
                if not raw_url: continue
                
                stream_url = _sanitize_url(raw_url)
                tvg_id = f"distro.{show_id}"
                group = f"DistroTV • {category}"

                entry = {
                    "id": tvg_id,
                    "name": name,
                    "logo": logo,
                    "group": group,
                    "url": stream_url,
                    "lang": lang,
                    "desc": show.get("description", ""),
                    "prog_title": ep.get("title", name)
                }

                geo_playlists[geo].append(entry)

                if show_id not in unique_channels:
                    unique_channels[show_id] = entry
                    
                    chan_el = ET.SubElement(xml_root, "channel", id=tvg_id)
                    ET.SubElement(chan_el, "display-name").text = name
                    ET.SubElement(chan_el, "icon", src=logo)

                    start_time = datetime.now().strftime("%Y%m%d%H0000 +0000")
                    stop_time = (datetime.now() + timedelta(hours=4)).strftime("%Y%m%d%H0000 +0000")
                    
                    prog_el = ET.SubElement(xml_root, "programme", {
                        "start": start_time,
                        "stop": stop_time,
                        "channel": tvg_id
                    })
                    ET.SubElement(prog_el, "title", lang=lang).text = entry["prog_title"]
                    ET.SubElement(prog_el, "desc", lang=lang).text = entry["desc"]
                    ET.SubElement(prog_el, "category", lang="en").text = category

        except Exception as e:
            print(f"Error for {geo}: {e}")

    m3u_header = f'#EXTM3U url-tvg="{EPG_URL}"\n'

    with open(M3U_ALL, "w", encoding="utf-8") as f:
        f.write(m3u_header)
        for c in unique_channels.values():
            f.write(f'#EXTINF:-1 tvg-id="{c["id"]}" tvg-logo="{c["logo"]}" group-title="{c["group"]}",{c["name"]}\n')
            f.write(f'{c["url"]}\n')

    for geo, channels in geo_playlists.items():
        geo_file = os.path.join(OUTPUT_DIR, f"distrotv_{geo}.m3u")
        with open(geo_file, "w", encoding="utf-8") as f:
            f.write(m3u_header)
            for c in channels:
                f.write(f'#EXTINF:-1 tvg-id="{c["id"]}" tvg-logo="{c["logo"]}" group-title="{c["group"]} [{geo}]",{c["name"]}\n')
                f.write(f'{c["url"]}\n')

    tree = ET.ElementTree(xml_root)
    ET.indent(tree, space="  ", level=0)
    tree.write(EPG_OUTPUT, encoding="utf-8", xml_declaration=True)

    with open(JSON_OUTPUT, "w", encoding="utf-8") as f:
        json.dump(list(unique_channels.values()), f, indent=2, ensure_ascii=False)

if __name__ == "__main__":
    fetch_and_process()
