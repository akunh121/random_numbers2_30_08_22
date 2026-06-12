#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Emby Series Downloader Link Exporter
-------------------------------------
שולף עבור כל סדרה תחת ParentId נתון את כל הפרקים שלה
ושומר קובץ JSON עם שמות קבצים וקישורי הורדה ישירים.

דוגמאות הרצה:
    # שימוש בקובץ הגדרות ברירת מחדל (token.json ליד הסקריפט)
    python fetch_series.py --parent-id 1070346

    # התחברות עם שם משתמש וסיסמה (שומר token חדש ל-token.json)
    python fetch_series.py --base-url https://play.embyil.tv:443 \\
        --username oren121 --password 'SECRET' --parent-id 1070346

    # מיקום פלט מותאם ודילוג על סדרות שכבר נשמרו
    python fetch_series.py --parent-id 1070346 \\
        --out-dir ~/EmbySeries --skip-existing
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import cloudscraper
    HAS_CLOUDSCRAPER = True
except ImportError:
    HAS_CLOUDSCRAPER = False

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "token.json"
DEFAULT_OUT_DIR = "/storage/emulated/0/Download/EmbySeries"
DEFAULT_PARENT_ID = "1070346"
REQUEST_TIMEOUT = 30


# ----------------------------------------------------------------------------
# Config

def load_config(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"[!] לא ניתן לקרוא {path}: {e}", file=sys.stderr)
        return {}


def save_config(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


# ----------------------------------------------------------------------------
# HTTP

def build_session() -> requests.Session:
    if HAS_CLOUDSCRAPER:
        session = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "desktop": True}
        )
    else:
        session = requests.Session()
        retry = Retry(
            total=4,
            backoff_factor=1.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset(["GET", "POST"]),
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
    return session


def emby_login(session: requests.Session, base_url: str,
               username: str, password: str) -> Tuple[str, str]:
    """מבצע התחברות ומחזיר (AccessToken, UserId)."""
    url = f"{base_url.rstrip('/')}/Users/AuthenticateByName"
    headers = {
        "X-Emby-Authorization": (
            'MediaBrowser Client="Emby Web", Device="Chrome", '
            'DeviceId="fetch_series_cli", Version="4.9.0"'
        ),
        "Content-Type": "application/json",
    }
    payload = {"Username": username, "Pw": password}
    r = session.post(url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    token = data.get("AccessToken")
    user_id = (data.get("User") or {}).get("Id")
    if not token or not user_id:
        raise RuntimeError("שרת Emby לא החזיר AccessToken/UserId")
    return token, user_id


def emby_get_user_id(session: requests.Session, base_url: str, token: str) -> str:
    """משיג UserId מתוך טוקן קיים (Users/Me)."""
    r = session.get(f"{base_url.rstrip('/')}/Users/Me",
                    headers={"X-Emby-Token": token}, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json()["Id"]


def emby_playback_url(session: requests.Session, base_url: str, token: str,
                      user_id: str, item_id: str) -> Tuple[Optional[str], str]:
    """מחזיר (DirectStreamUrl, container) דרך PlaybackInfo - עוקף את ההגבלה
    על /Items/{id}/Download כשיש למשתמש הרשאת Playback."""
    url = f"{base_url.rstrip('/')}/Items/{item_id}/PlaybackInfo?UserId={user_id}"
    headers = {"X-Emby-Token": token, "Content-Type": "application/json"}
    payload = {
        "UserId": user_id,
        "MaxStreamingBitrate": 140_000_000,
        "AutoOpenLiveStream": True,
        "MediaSourceId": f"mediasource_{item_id}",
        "AllowVideoStreamCopy": True,
        "AllowAudioStreamCopy": True,
        "DeviceProfile": {
            "MaxStreamingBitrate": 140_000_000,
            "DirectPlayProfiles": [{
                "Container": "mp4,mkv,webm,ts,m4v,mov,avi",
                "Type": "Video",
                "VideoCodec": "h264,hevc,vp8,vp9,av1,mpeg4",
                "AudioCodec": "aac,mp3,ac3,eac3,opus,vorbis,flac",
            }],
            "TranscodingProfiles": [],
            "ContainerProfiles": [],
            "CodecProfiles": [],
            "SubtitleProfiles": [],
        },
    }
    r = session.post(url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT)
    if r.status_code != 200:
        return None, ""
    sources = r.json().get("MediaSources") or []
    if not sources:
        return None, ""
    src = sources[0]
    direct = src.get("DirectStreamUrl")
    if not direct:
        return None, ""
    if direct.startswith("/"):
        direct = base_url.rstrip("/") + direct
    return direct, src.get("Container") or "mp4"


def emby_get_items(session: requests.Session, base_url: str, token: str,
                   parent_id: Optional[str] = None,
                   item_types: str = "Series",
                   search_term: Optional[str] = None,
                   limit: int = 1000) -> List[Dict[str, Any]]:
    url = f"{base_url.rstrip('/')}/Items"
    params: Dict[str, Any] = {
        "IncludeItemTypes": item_types,
        "Recursive": "true",
        "Limit": limit,
    }
    if parent_id:
        params["ParentId"] = parent_id
    if search_term:
        params["SearchTerm"] = search_term
    headers = {"X-Emby-Token": token}
    r = session.get(url, headers=headers, params=params, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json().get("Items", [])


# ----------------------------------------------------------------------------
# Helpers

_INVALID_FS_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_filename(name: str, max_len: int = 150) -> str:
    cleaned = _INVALID_FS_CHARS.sub(" ", name).strip().rstrip(". ")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:max_len] or "untitled"


def expand_path(p: str) -> Path:
    return Path(os.path.expanduser(os.path.expandvars(p)))


# ----------------------------------------------------------------------------
# Main fetch logic

def fetch_series(session: requests.Session, base_url: str, token: str,
                 user_id: str,
                 parent_id: Optional[str], out_dir: Path,
                 skip_existing: bool = False,
                 series_name: Optional[str] = None,
                 use_download_endpoint: bool = False) -> None:
    if series_name:
        print(f"מחפש סדרה בשם: {series_name}")
        series_items = emby_get_items(
            session, base_url, token,
            parent_id=parent_id, item_types="Series",
            search_term=series_name, limit=200,
        )
        target = series_name.strip().casefold()
        exact = [s for s in series_items
                 if (s.get("Name") or "").strip().casefold() == target]
        if exact:
            series_items = exact
        else:
            partial = [s for s in series_items
                       if target in (s.get("Name") or "").casefold()]
            if partial:
                series_items = partial
    else:
        print(f"שולף סדרות מתחת ParentId={parent_id} ...")
        series_items = emby_get_items(
            session, base_url, token,
            parent_id=parent_id, item_types="Series", limit=1000,
        )

    if not series_items:
        print("לא נמצאו סדרות.")
        return

    print(f"נמצאו {len(series_items)} סדרות.")
    out_dir.mkdir(parents=True, exist_ok=True)

    for idx, series in enumerate(series_items, start=1):
        s_name = series.get("Name") or "סדרה ללא שם"
        series_id = series["Id"]
        safe_name = sanitize_filename(s_name)
        out_path = out_dir / f"{safe_name}.json"

        if skip_existing and out_path.exists():
            print(f"[{idx}/{len(series_items)}] דילוג (קיים): {s_name}")
            continue

        print(f"[{idx}/{len(series_items)}] מעבד: {s_name} (Id={series_id})")

        episodes_list: List[Dict[str, str]] = []

        # Dedup seasons by IndexNumber (Emby sometimes returns duplicates
        # when the same season appears under multiple paths).
        seasons_raw = emby_get_items(
            session, base_url, token, series_id, "Season", limit=200
        )
        seen_seasons: set = set()
        seasons = []
        for sea in sorted(seasons_raw,
                          key=lambda x: (x.get("IndexNumber", 0), x.get("Id"))):
            n = sea.get("IndexNumber", 0)
            if n in seen_seasons:
                continue
            seen_seasons.add(n)
            seasons.append(sea)

        failed = 0
        for season in seasons:
            season_number = season.get("IndexNumber", 1)
            season_id = season["Id"]
            season_label = f"עונה {season_number}"

            episodes_raw = emby_get_items(
                session, base_url, token, season_id, "Episode", limit=5000
            )
            seen_eps: set = set()
            episodes = []
            for e in sorted(episodes_raw,
                            key=lambda x: (x.get("IndexNumber", 0), x.get("Id"))):
                n = e.get("IndexNumber", 0)
                if n in seen_eps:
                    continue
                seen_eps.add(n)
                episodes.append(e)

            for episode in episodes:
                episode_number = episode.get("IndexNumber", 0)
                episode_id = episode["Id"]

                if use_download_endpoint:
                    link = (
                        f"{base_url.rstrip('/')}/Items/{episode_id}"
                        f"/Download?X-Emby-Token={token}"
                    )
                    container = "mkv"
                else:
                    link, container = emby_playback_url(
                        session, base_url, token, user_id, episode_id
                    )
                    if not link:
                        failed += 1
                        continue

                ext = container if container in (
                    "mp4", "mkv", "webm", "m4v", "mov", "ts", "avi"
                ) else "mp4"
                file_name = sanitize_filename(
                    f"{s_name} {season_label} פרק {episode_number}.{ext}"
                )
                episodes_list.append({
                    "file_name": file_name,
                    "download_link": link,
                })

        with out_path.open("w", encoding="utf-8") as f_out:
            json.dump(episodes_list, f_out, ensure_ascii=False, indent=2)

        suffix = f" ({failed} כשלו)" if failed else ""
        print(f"    -> נשמר {out_path.name} ({len(episodes_list)} פרקים){suffix}")


# ----------------------------------------------------------------------------
# CLI

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="ייצוא קישורי הורדה לסדרות מ-Emby לקבצי JSON.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--config", default=str(DEFAULT_CONFIG_PATH),
                   help=f"נתיב לקובץ הגדרות JSON (ברירת מחדל: {DEFAULT_CONFIG_PATH})")
    p.add_argument("--base-url", help="כתובת שרת Emby (למשל https://play.embyil.tv:443)")
    p.add_argument("--token", help="X-Emby-Token (אם קיים, מועדף על username/password)")
    p.add_argument("--username", help="שם משתמש להתחברות (אם אין token)")
    p.add_argument("--password", help="סיסמה להתחברות")
    p.add_argument("--parent-id", default=None,
                   help=f"ParentId של קטגוריית הסדרות (ברירת מחדל: {DEFAULT_PARENT_ID})")
    p.add_argument("--series-name", default=None,
                   help="חיפוש ועיבוד של סדרה ספציפית לפי שם (אם נתון, --parent-id אופציונלי)")
    p.add_argument("--out-dir", default=None,
                   help=f"תיקיית פלט (ברירת מחדל: {DEFAULT_OUT_DIR})")
    p.add_argument("--skip-existing", action="store_true",
                   help="דלג על סדרות שכבר נשמרו ל-JSON")
    p.add_argument("--save-token", action="store_true",
                   help="שמור את ה-token לקובץ ההגדרות אחרי לוגין מוצלח")
    p.add_argument("--use-download-endpoint", action="store_true",
                   help="השתמש ב-/Items/{id}/Download (דורש EnableContentDownloading) במקום PlaybackInfo")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    config_path = expand_path(args.config)
    config = load_config(config_path)

    base_url = args.base_url or config.get("base_url")
    if not base_url:
        print("[!] חסר base_url (--base-url או בקובץ ההגדרות)", file=sys.stderr)
        return 2

    token = args.token or config.get("token")
    user_id = config.get("user_id")
    username = args.username or config.get("username")
    password = args.password or config.get("password")

    session = build_session()
    if not HAS_CLOUDSCRAPER:
        print("[!] שים לב: cloudscraper לא מותקן. אם השרת מאחורי Cloudflare ייתכן שתחסם.",
              file=sys.stderr)
        print("    התקנה: pip install cloudscraper", file=sys.stderr)

    if not token:
        if not (username and password):
            print("[!] חסר token, וגם שם משתמש/סיסמה ללוגין", file=sys.stderr)
            return 2
        print(f"מתחבר ל-{base_url} כ-{username} ...")
        try:
            token, user_id = emby_login(session, base_url, username, password)
        except requests.HTTPError as e:
            print(f"[!] לוגין נכשל: {e}", file=sys.stderr)
            return 1
        print("התחברות הצליחה.")
        if args.save_token or not config.get("token"):
            config.update({
                "base_url": base_url, "username": username,
                "token": token, "user_id": user_id,
            })
            config.pop("password", None)
            save_config(config_path, config)
            print(f"Token נשמר ב-{config_path}")
    elif not user_id:
        try:
            user_id = emby_get_user_id(session, base_url, token)
        except requests.HTTPError as e:
            print(f"[!] לא הצלחתי להשיג UserId מהטוקן: {e}", file=sys.stderr)
            return 1

    series_name = args.series_name
    if series_name:
        parent_id = args.parent_id or config.get("parent_id")
    else:
        parent_id = args.parent_id or config.get("parent_id") or DEFAULT_PARENT_ID
    out_dir = expand_path(args.out_dir or config.get("out_dir") or DEFAULT_OUT_DIR)

    try:
        fetch_series(session, base_url, token, user_id, parent_id, out_dir,
                     skip_existing=args.skip_existing,
                     series_name=series_name,
                     use_download_endpoint=args.use_download_endpoint)
    except requests.HTTPError as e:
        print(f"[!] שגיאת HTTP מ-Emby: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nהופסק על ידי המשתמש.", file=sys.stderr)
        return 130

    print("\nסיום.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
