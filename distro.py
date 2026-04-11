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
    base_url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(sanitized, doseq=True), ""))
    return f"{base_url}|User-Agent={BROWSER_UA}&Referer={REFERER}"

def fetch_and_process():
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)

    session = requests.Session()
    session.headers.update({"User-Agent": BROWSER_UA})
    
    all_extracted_channels = {} 
    geo_to_ids = {geo: [] for geo in GEOS}

    for geo in GEOS:
        print(f"Fetching Geo: {geo}...")
        url = f"{FEED_BASE}&geo={geo}" if geo != "US" else FEED_BASE
        try:
            r = session.get(url, timeout=30)
            r.raise_for_status()
            feed = r.json()
            
            # Handle different JSON structures (dict vs list)
            shows_data = feed.get("shows", {})
            if isinstance(shows_data, dict):
                shows = list(shows_data.values())
            elif isinstance(shows_data, list):
                shows = shows_data
            else:
                shows = []

            for show in shows:
                if not isinstance(show, dict): continue
                
                # Check for live stream content
                seasons = show.get("seasons", [])
                if not seasons or not isinstance(seasons[0], dict): continue
                episodes = seasons[0].get("episodes", [])
                if not episodes or not isinstance(episodes[0], dict): continue
                
                ep = episodes[0]
                raw_url = ep.get("content", {}).get("url")
                if not raw_url: continue

                show_id = str(show.get('id', ''))
                if not show_id: continue

                # Store for geo-specific playlist
                geo_to_ids[geo].append(show_id)

                # Store unique channel info if not already captured
                if show_id not in all_extracted_channels:
                    name = (show.get("title") or "Unknown Channel").strip()
                    logo = show.get("img_logo") or ""
                    category, lang = _parse_distro_tags(show.get("genre") or "")
                    
                    all_extracted_channels[show_id] = {
                        "id": f"distro.{show_id}",
                        "name": name,
                        "logo": logo,
                        "category": category,
                        "url": _sanitize_url(raw_url),
                        "lang": lang,
                        "desc": show.get("description", ""),
                        "prog_title": ep.get("title", name)
                    }

        except Exception as e:
            print(f"Error fetching {geo}: {e}")

    # --- GENERATE FILES ---
    
    # 1. XMLTV EPG
    xml_root = ET.Element("tv", {"generator-info-name": "DistroTV-Scraper"})
    for sid, c in all_extracted_channels.items():
        chan_el = ET.SubElement(xml_root, "channel", id=c["id"])
        ET.SubElement(chan_el, "display-name").text = c["name"]
        ET.SubElement(chan_el, "icon", src=c["logo"])

        start_time = datetime.now().strftime("%Y%m%d%H0000 +0000")
        stop_time = (datetime.now() + timedelta(hours=4)).strftime("%Y%m%d%H0000 +0000")
        prog_el = ET.SubElement(xml_root, "programme", {"start": start_time, "stop": stop_time, "channel": c["id"]})
        ET.SubElement(prog_el, "title", lang=c["lang"]).text = c["prog_title"]
        ET.SubElement(prog_el, "desc", lang=c["lang"]).text = c["desc"]
        ET.SubElement(prog_el, "category", lang="en").text = c["category"]

    tree = ET.ElementTree(xml_root)
    ET.indent(tree, space="  ", level=0)
    tree.write(EPG_OUTPUT, encoding="utf-8", xml_declaration=True)

    # 2. Master M3U (All Unique Channels)
    m3u_header = f'#EXTM3U url-tvg="{EPG_URL}"\n'
    with open(M3U_ALL, "w", encoding="utf-8") as f:
        f.write(m3u_header)
        for c in all_extracted_channels.values():
            f.write(f'#EXTINF:-1 tvg-id="{c["id"]}" tvg-logo="{c["logo"]}" group-title="DistroTV • {c["category"]}",{c["name"]}\n')
            f.write(f'{c["url"]}\n')

    # 3. Regional M3Us
    for geo, ids in geo_to_ids.items():
        if not ids: continue
        geo_file = os.path.join(OUTPUT_DIR, f"distrotv_{geo}.m3u")
        with open(geo_file, "w", encoding="utf-8") as f:
            f.write(m3u_header)
            for sid in ids:
                c = all_extracted_channels[sid]
                f.write(f'#EXTINF:-1 tvg-id="{c["id"]}" tvg-logo="{c["logo"]}" group-title="DistroTV • {geo} • {c["category"]}",{c["name"]}\n')
                f.write(f'{c["url"]}\n')

    print(f"Success! Found {len(all_extracted_channels)} total unique channels.")

if __name__ == "__main__":
    fetch_and_process()
