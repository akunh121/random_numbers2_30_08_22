#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Emby → Telegram Pipeline
========================
מבחר ויזואלי של ספריות/סדרות/סרטים מ-Emby, הורדה אחת אחת,
העלאה ישירה לטלגרם (דרך בוט או חשבון אישי), ומחיקת הקובץ אחרי כל העלאה.

תכונות:
- GUI עם tkinter
- חיבור ל-Emby עם cloudscraper (עוקף Cloudflare TLS fingerprint)
- שליפת DirectStreamUrl דרך PlaybackInfo (עוקף הגבלת EnableContentDownloading)
- העלאה לטלגרם עם Pyrogram (חשבון אישי) או Bot API
- תור עבודה - הורדה ואז העלאה ואז מחיקה, אחד אחרי השני
- שמירת קונפיג ב-~/.emby_telegram_pipeline/config.json
"""

from __future__ import annotations

import json
import os
import queue
import re
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import tkinter as tk
from tkinter import ttk, messagebox, filedialog, scrolledtext

try:
    import cloudscraper
except ImportError:
    print("נדרש: pip install cloudscraper", file=sys.stderr)
    raise

# Optional: BiDi text shaping for platforms (Linux without fribidi)
# whose Tk build doesn't reorder RTL text correctly.
_LINUX = sys.platform.startswith("linux")
try:
    from bidi.algorithm import get_display as _bidi_get_display  # type: ignore
except ImportError:
    _bidi_get_display = None


def b(s: str) -> str:
    """Apply BiDi reordering only where Tk doesn't do it (Linux).
    On macOS / Windows, Tk shapes RTL natively, so return unchanged."""
    if _LINUX and _bidi_get_display is not None and s:
        try:
            return _bidi_get_display(s)
        except Exception:
            return s
    return s

try:
    from pyrogram import Client as PyroClient
    from pyrogram.errors import RPCError
    HAS_PYROGRAM = True
except ImportError:
    HAS_PYROGRAM = False

import requests


# ============================================================================
# Config

APP_DIR = Path.home() / ".emby_telegram_pipeline"
CONFIG_PATH = APP_DIR / "config.json"
SESSIONS_DIR = APP_DIR / "sessions"
DOWNLOAD_DIR_DEFAULT = APP_DIR / "downloads"


def load_config() -> Dict[str, Any]:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_config(cfg: Dict[str, Any]) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass


_INVALID_FS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize(name: str, mx: int = 180) -> str:
    n = _INVALID_FS.sub(" ", name).strip().rstrip(". ")
    n = re.sub(r"\s+", " ", n)
    return n[:mx] or "untitled"


def human_size(n: int) -> str:
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f}{u}"
        n /= 1024
    return f"{n:.1f}PB"


# ============================================================================
# Emby client

class EmbyClient:
    def __init__(self, base_url: str):
        self.base = base_url.rstrip("/")
        self.s = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "desktop": True}
        )
        self.token: Optional[str] = None
        self.user_id: Optional[str] = None
        self.device_id = "emby-pipeline-cli-0fa4e360"

    def login(self, username: str, password: str) -> None:
        url = f"{self.base}/Users/AuthenticateByName"
        headers = {
            "Content-Type": "application/json",
            "X-Emby-Authorization": (
                f'MediaBrowser Client="Emby Web", Device="Safari macOS", '
                f'DeviceId="{self.device_id}", Version="4.9.0.42"'
            ),
        }
        r = self.s.post(url, headers=headers,
                        json={"Username": username, "Pw": password}, timeout=30)
        r.raise_for_status()
        d = r.json()
        self.token = d["AccessToken"]
        self.user_id = d["User"]["Id"]

    def login_with_token(self, token: str) -> None:
        self.token = token
        r = self.s.get(f"{self.base}/Users/Me",
                       headers={"X-Emby-Token": token}, timeout=30)
        r.raise_for_status()
        self.user_id = r.json()["Id"]

    def _h(self) -> Dict[str, str]:
        return {"X-Emby-Token": self.token}

    def libraries(self) -> List[Dict[str, Any]]:
        r = self.s.get(f"{self.base}/Library/MediaFolders",
                       headers=self._h(), timeout=30)
        r.raise_for_status()
        return r.json().get("Items", [])

    def items(self, parent_id: Optional[str] = None,
              item_types: str = "Series",
              search_term: Optional[str] = None,
              limit: int = 2000,
              start_index: int = 0) -> Tuple[List[Dict[str, Any]], int]:
        params = {
            "IncludeItemTypes": item_types,
            "Recursive": "true",
            "Limit": limit,
            "StartIndex": start_index,
            "EnableTotalRecordCount": "true",
            "SortBy": "SortName",
            "SortOrder": "Ascending",
        }
        if parent_id:
            params["ParentId"] = parent_id
        if search_term:
            params["SearchTerm"] = search_term
        r = self.s.get(f"{self.base}/Items", headers=self._h(),
                       params=params, timeout=60)
        r.raise_for_status()
        j = r.json()
        return j.get("Items", []), int(j.get("TotalRecordCount", 0))

    def dedup_by_index(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        seen, out = set(), []
        for it in sorted(items, key=lambda x: (x.get("IndexNumber", 0), x.get("Id"))):
            n = it.get("IndexNumber", -1)
            if n != -1 and n in seen:
                continue
            if n != -1:
                seen.add(n)
            out.append(it)
        return out

    SUBTITLE_TEXT_FORMATS = {"srt", "vtt", "ass", "ssa", "sub"}

    def playback_url(self, item_id: str) -> Optional[Tuple[str, str, int, List[Dict[str, str]]]]:
        """החזרת (URL, container, size, subtitles).
        subtitles: רשימה של {url, language, format, is_external} עבור כתוביות
        טקסטואליות זמינות. מחזיר None אם נכשל."""
        url = f"{self.base}/Items/{item_id}/PlaybackInfo?UserId={self.user_id}"
        headers = {**self._h(), "Content-Type": "application/json"}
        payload = {
            "UserId": self.user_id,
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
                "TranscodingProfiles": [], "ContainerProfiles": [],
                "CodecProfiles": [], "SubtitleProfiles": [],
            },
        }
        r = self.s.post(url, headers=headers, json=payload, timeout=30)
        if r.status_code != 200:
            return None
        sources = r.json().get("MediaSources") or []
        if not sources:
            return None
        src = sources[0]
        direct = src.get("DirectStreamUrl")
        if not direct:
            return None
        if direct.startswith("/"):
            direct = self.base + direct

        # Collect subtitle streams (text-only). Image-based subs (PGS/VOBSUB)
        # can't be played as standalone files, so skip them.
        ms_id = src.get("Id") or f"mediasource_{item_id}"
        # Try to extract the api_key the server gave us in DirectStreamUrl,
        # to reuse it for subtitle endpoints which require it too.
        api_key_match = re.search(r"api_key=([0-9a-f]+)", direct)
        api_key = api_key_match.group(1) if api_key_match else self.token
        subs: List[Dict[str, str]] = []
        for stream in (src.get("MediaStreams") or []):
            if stream.get("Type") != "Subtitle":
                continue
            codec = (stream.get("Codec") or "").lower()
            if codec not in self.SUBTITLE_TEXT_FORMATS:
                continue
            idx = stream.get("Index")
            if idx is None:
                continue
            lang = stream.get("Language") or "und"
            fmt = "srt" if codec in ("srt", "sub") else codec
            sub_url = (
                f"{self.base}/Videos/{item_id}/{ms_id}/Subtitles/"
                f"{idx}/Stream.{fmt}?api_key={api_key}"
            )
            subs.append({
                "url": sub_url, "language": lang, "format": fmt,
                "is_external": "true" if stream.get("IsExternal") else "false",
                "display_title": stream.get("DisplayTitle") or lang,
            })

        return direct, src.get("Container") or "mp4", int(src.get("Size") or 0), subs


# ============================================================================
# Download

def stream_download(session, url: str, dest: Path,
                    progress_cb: Callable[[int, int], None]) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with session.get(url, stream=True, timeout=60, allow_redirects=True) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length") or 0)
        done = 0
        last_emit = 0
        with dest.open("wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)
                    done += len(chunk)
                    now = time.time()
                    if now - last_emit >= 0.3:
                        progress_cb(done, total)
                        last_emit = now
        progress_cb(done, total)


# ============================================================================
# Telegram uploader

class TelegramUploader:
    def __init__(self, mode: str, api_id: Optional[int], api_hash: Optional[str],
                 bot_token: Optional[str], phone: Optional[str],
                 chat_id: str):
        self.mode = mode  # 'user' or 'bot'
        self.api_id = api_id
        self.api_hash = api_hash
        self.bot_token = bot_token
        self.phone = phone
        self.chat_id = self._parse_chat(chat_id)
        self.client: Optional[PyroClient] = None
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _parse_chat(s: str) -> Any:
        s = s.strip()
        if s.startswith("@"):
            return s
        try:
            return int(s)
        except ValueError:
            return s

    def start(self) -> None:
        if not HAS_PYROGRAM:
            raise RuntimeError("נדרש: pip install pyrogram tgcrypto")
        name = "user_session" if self.mode == "user" else "bot_session"
        kwargs: Dict[str, Any] = {
            "name": name,
            "workdir": str(SESSIONS_DIR),
            "api_id": self.api_id,
            "api_hash": self.api_hash,
        }
        if self.mode == "bot":
            kwargs["bot_token"] = self.bot_token
        else:
            kwargs["phone_number"] = self.phone
        self.client = PyroClient(**kwargs)
        self.client.start()

    def stop(self) -> None:
        if self.client:
            try:
                self.client.stop()
            except Exception:
                pass
            self.client = None

    def upload(self, file_path: Path, caption: str,
               progress_cb: Callable[[int, int], None]) -> None:
        if not self.client:
            raise RuntimeError(b("Telegram client לא הופעל"))

        def _prog(current, total):
            progress_cb(current, total)

        ext = file_path.suffix.lower()
        if ext in (".mp4", ".mkv", ".webm", ".m4v", ".mov", ".avi", ".ts"):
            self.client.send_video(
                chat_id=self.chat_id, video=str(file_path),
                caption=caption, progress=_prog, supports_streaming=True,
            )
        else:
            self.client.send_document(
                chat_id=self.chat_id, document=str(file_path),
                caption=caption, progress=_prog,
            )


class LocalNoopUploader:
    """Stand-in for TelegramUploader when running in 'local only' mode.
    Skips the upload step so files just get downloaded to disk."""
    def __init__(self):
        pass

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def upload(self, file_path: Path, caption: str,
               progress_cb: Callable[[int, int], None]) -> None:
        # No-op: file is already on disk after download.
        size = file_path.stat().st_size
        progress_cb(size, size)


# ============================================================================
# Job model

@dataclass
class Job:
    title: str          # תצוגה
    item_id: str
    file_basename: str  # ללא סיומת
    status: str = "ממתין"
    progress: float = 0.0
    error: Optional[str] = None


# ============================================================================
# Worker

class _SkippedError(Exception):
    """Raised inside the worker to indicate a job was skipped (not retried)."""
    pass


class PipelineWorker(threading.Thread):
    def __init__(self, emby: EmbyClient, uploader: TelegramUploader,
                 download_dir: Path,
                 status_cb: Callable[[int, str, float], None],
                 log_cb: Callable[[str], None],
                 delete_after_upload: bool = True,
                 size_limit_mb: int = 0,
                 auto_split: bool = False,
                 retry_count: int = 0,
                 retry_delay: int = 5,
                 history_cb: Optional[Callable[[Dict[str, Any]], None]] = None):
        super().__init__(daemon=True)
        self.emby = emby
        self.uploader = uploader
        self.download_dir = download_dir
        self.q: "queue.Queue[Tuple[int, Job]]" = queue.Queue()
        self.status_cb = status_cb
        self.log_cb = log_cb
        self.stop_flag = threading.Event()
        self.current_idx: Optional[int] = None
        self.delete_after_upload = delete_after_upload
        # 0 = unlimited
        self.size_limit_bytes = int(size_limit_mb) * 1024 * 1024 if size_limit_mb else 0
        self.auto_split = bool(auto_split)
        self.retry_count = max(0, int(retry_count))
        self.retry_delay = max(1, int(retry_delay))
        self.history_cb = history_cb

    def enqueue(self, jobs: List[Tuple[int, Job]]) -> None:
        for j in jobs:
            self.q.put(j)

    def stop(self) -> None:
        self.stop_flag.set()

    def run(self) -> None:
        self.log_cb(b("Worker התחיל"))
        try:
            self.uploader.start()
            self.log_cb(b("Telegram מחובר"))
        except Exception as e:
            self.log_cb(f"שגיאת Telegram: {e}")
            return

        while not self.stop_flag.is_set():
            try:
                idx, job = self.q.get(timeout=0.5)
            except queue.Empty:
                continue
            self.current_idx = idx
            attempts = 0
            t_start = time.time()
            while True:
                attempts += 1
                try:
                    self._process(idx, job)
                    if self.history_cb:
                        self.history_cb({
                            "title": job.title, "status": "ok",
                            "ts": int(time.time()),
                            "elapsed": int(time.time() - t_start),
                        })
                    break
                except _SkippedError as e:
                    self.log_cb(f"[{job.title}] דילוג: {e}")
                    self.status_cb(idx, b(f"דילוג: {e}"), 0)
                    if self.history_cb:
                        self.history_cb({
                            "title": job.title, "status": "skipped",
                            "ts": int(time.time()),
                            "reason": str(e),
                        })
                    break
                except Exception as e:
                    if attempts <= self.retry_count and not self.stop_flag.is_set():
                        self.log_cb(
                            f"[{job.title}] ניסיון {attempts}/{self.retry_count+1} "
                            f"נכשל: {e}. נסה שוב בעוד {self.retry_delay}s")
                        self.status_cb(
                            idx, b(f"נכשל - ממתין {self.retry_delay}s "
                                   f"לניסיון {attempts+1}/{self.retry_count+1}"), 0)
                        for _ in range(self.retry_delay):
                            if self.stop_flag.is_set(): break
                            time.sleep(1)
                        continue
                    tb = traceback.format_exc()
                    self.log_cb(f"[{job.title}] FATAL: {e}\n{tb}")
                    self.status_cb(idx, b(f"שגיאה: {e}"), 0)
                    if self.history_cb:
                        self.history_cb({
                            "title": job.title, "status": "error",
                            "ts": int(time.time()),
                            "reason": str(e),
                        })
                    break
            self.q.task_done()

        try:
            self.uploader.stop()
        except Exception:
            pass
        self.log_cb(b("Worker נעצר"))

    def _upload_with_optional_split(self, idx: int, job: Job,
                                     dest: Path, up_prog) -> None:
        """Upload a file, splitting it if it exceeds size_limit_bytes
        and auto_split is enabled."""
        actual = dest.stat().st_size
        if self.size_limit_bytes and actual > self.size_limit_bytes:
            if not self.auto_split:
                raise _SkippedError(
                    f"קובץ {human_size(actual)} מעל המגבלה "
                    f"{human_size(self.size_limit_bytes)}")
            # Split
            chunk_size = int(self.size_limit_bytes * 0.95)
            n_parts = (actual + chunk_size - 1) // chunk_size
            self.log_cb(f"[{job.title}] מפצל ל-{n_parts} חלקים")
            parts: List[Path] = []
            try:
                with dest.open("rb") as f_in:
                    for i in range(n_parts):
                        part_path = dest.with_suffix(
                            dest.suffix + f".part{i+1:02d}")
                        with part_path.open("wb") as f_out:
                            written = 0
                            while written < chunk_size:
                                buf = f_in.read(min(1024 * 1024,
                                                     chunk_size - written))
                                if not buf:
                                    break
                                f_out.write(buf); written += len(buf)
                        parts.append(part_path)
                        self.status_cb(idx, b(f"פיצול {i+1}/{n_parts}"),
                                       (i + 1) / n_parts * 100)
                for i, part in enumerate(parts, 1):
                    caption = f"{job.title} [{i}/{n_parts}]"
                    def pp(done, total, i=i, n=n_parts):
                        pct = ((i - 1) + (done / total if total else 0)) / n * 100
                        self.status_cb(
                            idx, b(f"מעלה חלק {i}/{n} {human_size(done)}"), pct)
                    self.uploader.upload(part, caption=caption, progress_cb=pp)
            finally:
                for p in parts:
                    try: p.unlink()
                    except OSError: pass
        else:
            self.uploader.upload(dest, caption=job.title, progress_cb=up_prog)

    def _process(self, idx: int, job: Job) -> None:
        self.status_cb(idx, b("שולף URL"), 0)
        info = self.emby.playback_url(job.item_id)
        if not info:
            raise RuntimeError(b("PlaybackInfo החזיר שגיאה"))
        url, container, size, subs = info
        if container not in ("mp4", "mkv", "webm", "m4v", "mov", "avi", "ts"):
            container = "mp4"
        file_name = sanitize(f"{job.file_basename}.{container}")
        dest = self.download_dir / file_name

        # Download video
        self.log_cb(f"[{job.title}] מוריד {human_size(size) if size else '?'}")
        def dl_prog(done, total):
            pct = (done / total * 100) if total else 0
            self.status_cb(idx, b(f"מוריד {human_size(done)}/{human_size(total)}"), pct)
        stream_download(self.emby.s, url, dest, dl_prog)

        # Upload video (with size check / optional split)
        actual = dest.stat().st_size
        self.log_cb(f"[{job.title}] מעלה {human_size(actual)}")
        def up_prog(done, total):
            pct = (done / total * 100) if total else 0
            self.status_cb(idx, b(f"מעלה {human_size(done)}/{human_size(total)}"), pct)
        try:
            self._upload_with_optional_split(idx, job, dest, up_prog)
        except _SkippedError:
            # Skipped due to size: cleanup file and propagate up
            if self.delete_after_upload and dest.exists():
                try: dest.unlink()
                except OSError: pass
            raise
        except Exception as e:
            self.log_cb(f"[{job.title}] העלאה נכשלה: {e}")
            raise

        # Delete video (if enabled)
        if self.delete_after_upload:
            try:
                dest.unlink()
                self.log_cb(f"[{job.title}] קובץ נמחק")
            except OSError as e:
                self.log_cb(f"[{job.title}] לא הצלחתי למחוק: {e}")
        else:
            self.log_cb(f"[{job.title}] קובץ נשמר: {dest}")

        # Subtitles - download and upload each text-format sub
        if subs:
            self.log_cb(f"[{job.title}] נמצאו {len(subs)} כתוביות")
            for i, sub in enumerate(subs):
                lang = sub["language"]
                fmt = sub["format"]
                sub_basename = sanitize(f"{job.file_basename}.{lang}.{fmt}")
                sub_dest = self.download_dir / sub_basename
                try:
                    self.status_cb(idx, b(f"מוריד כתוביות ({lang})"), 0)
                    def sub_prog(done, total):
                        pct = (done / total * 100) if total else 0
                        self.status_cb(
                            idx, b(f"כתוביות {lang} {human_size(done)}"), pct)
                    stream_download(self.emby.s, sub["url"], sub_dest, sub_prog)
                    self.status_cb(idx, b(f"מעלה כתוביות ({lang})"), 0)
                    self.uploader.upload(
                        sub_dest,
                        caption=f"{job.title} — כתוביות [{lang}]",
                        progress_cb=lambda d, t: None,
                    )
                    self.log_cb(f"[{job.title}] כתוביות {lang} נשלחו")
                except Exception as e:
                    self.log_cb(f"[{job.title}] כתוביות {lang} נכשלו: {e}")
                finally:
                    if self.delete_after_upload and sub_dest.exists():
                        try: sub_dest.unlink()
                        except OSError: pass

        self.status_cb(idx, b("הושלם"), 100)


# ============================================================================
# GUI

class App:
    def __init__(self) -> None:
        self.cfg = load_config()
        self.emby: Optional[EmbyClient] = None
        self.worker: Optional[PipelineWorker] = None
        self.jobs: List[Job] = []
        self.tree_items: Dict[str, Dict[str, Any]] = {}
        self._stats_t0: Optional[float] = None

        self.root = tk.Tk()
        self.root.title("Emby → Telegram Pipeline")
        geom = self.cfg.get("window_geometry") or "1180x780"
        self.root.geometry(geom)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # Theme + fonts
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        import tkinter.font as tkfont
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            try:
                tkfont.nametofont(name).configure(size=11)
            except tk.TclError:
                pass
        try:
            tkfont.nametofont("TkFixedFont").configure(size=10)
        except tk.TclError:
            pass
        # Status row colors
        style.configure("Done.Treeview.Item", foreground="#1b5e20")
        style.map("TButton", foreground=[("active", "#0d47a1")])
        style.configure("Accent.TButton", foreground="white",
                        background="#1976d2", borderwidth=0, padding=6)
        style.map("Accent.TButton",
                  background=[("active", "#0d47a1"), ("pressed", "#0a3a8a")])
        style.configure("Danger.TButton", foreground="white",
                        background="#c62828", borderwidth=0, padding=6)
        style.map("Danger.TButton",
                  background=[("active", "#b71c1c"), ("pressed", "#8d0000")])
        style.configure("Treeview", rowheight=24)

        # Main layout: notebook (top) + status bar (bottom)
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=8, pady=(8, 0))

        self._build_header()
        self._build_config_tab()
        self._build_browse_tab()
        self._build_queue_tab()
        self._build_history_tab()
        self._build_log_tab()
        self._build_status_bar()
        self._bind_shortcuts()

        # Restore saved queue from previous run (only pending items)
        self._restore_queue()

        # Live tick to update stats
        self.root.after(500, self._tick_stats)

    # ---- Config tab ----
    def _build_config_tab(self) -> None:
        f = ttk.Frame(self.notebook)
        self.notebook.add(f, text="⚙  " + b("הגדרות"))

        # Emby
        em = ttk.LabelFrame(f, text="Emby")
        em.pack(fill=tk.X, padx=8, pady=4)
        self.v_base = tk.StringVar(value=self.cfg.get("base_url",
                                                      "https://play.embyil.tv"))
        self.v_user = tk.StringVar(value=self.cfg.get("username", ""))
        self.v_pass = tk.StringVar(value=self.cfg.get("password", ""))
        for label, var, show in [("Base URL", self.v_base, None),
                                 ("Username", self.v_user, None),
                                 ("Password", self.v_pass, "*")]:
            row = ttk.Frame(em); row.pack(fill=tk.X, padx=6, pady=2)
            ttk.Label(row, text=label, width=14).pack(side=tk.RIGHT)
            ttk.Entry(row, textvariable=var, show=show).pack(
                side=tk.RIGHT, fill=tk.X, expand=True)

        # Telegram
        tg = ttk.LabelFrame(f, text="Telegram")
        tg.pack(fill=tk.X, padx=8, pady=4)
        self.v_mode = tk.StringVar(value=self.cfg.get("tg_mode", "bot"))
        mr = ttk.Frame(tg); mr.pack(fill=tk.X, padx=6, pady=2)
        ttk.Label(mr, text=b("מצב"), width=14).pack(side=tk.RIGHT)
        ttk.Radiobutton(mr, text=b("חשבון משתמש (עד 2GB)"), variable=self.v_mode,
                        value="user").pack(side=tk.RIGHT, padx=4)
        ttk.Radiobutton(mr, text=b("בוט (עד 50MB)"), variable=self.v_mode,
                        value="bot").pack(side=tk.RIGHT, padx=4)
        ttk.Radiobutton(mr, text=b("מקומי (בלי טלגרם)"), variable=self.v_mode,
                        value="local").pack(side=tk.RIGHT, padx=4)

        self.v_api_id = tk.StringVar(value=str(self.cfg.get("api_id", "")))
        self.v_api_hash = tk.StringVar(value=self.cfg.get("api_hash", ""))
        self.v_phone = tk.StringVar(value=self.cfg.get("phone", ""))
        self.v_bot_token = tk.StringVar(value=self.cfg.get("bot_token", ""))
        self.v_chat = tk.StringVar(value=self.cfg.get("chat_id", "me"))

        for label, var, show in [
            ("API ID", self.v_api_id, None),
            ("API Hash", self.v_api_hash, "*"),
            (b("טלפון (user)"), self.v_phone, None),
            ("Bot Token", self.v_bot_token, "*"),
            ("Chat ID / @user", self.v_chat, None),
        ]:
            row = ttk.Frame(tg); row.pack(fill=tk.X, padx=6, pady=2)
            ttk.Label(row, text=label, width=14).pack(side=tk.RIGHT)
            ttk.Entry(row, textvariable=var, show=show).pack(
                side=tk.RIGHT, fill=tk.X, expand=True)

        ttk.Label(tg, text=(
            "API ID/Hash: https://my.telegram.org → API Development Tools\n"
            + b('Chat ID: "me" לעצמך, או -100xxxx לערוץ, או @username לאדם')
        ), foreground="gray").pack(anchor="w", padx=6, pady=4)

        # Download dir
        dl = ttk.LabelFrame(f, text=b("הורדה"))
        dl.pack(fill=tk.X, padx=8, pady=4)
        self.v_dldir = tk.StringVar(value=self.cfg.get(
            "download_dir", str(DOWNLOAD_DIR_DEFAULT)))
        row = ttk.Frame(dl); row.pack(fill=tk.X, padx=6, pady=2)
        ttk.Label(row, text=b("תיקיית הורדה"), width=14).pack(side=tk.RIGHT)
        ttk.Entry(row, textvariable=self.v_dldir).pack(
            side=tk.RIGHT, fill=tk.X, expand=True)
        ttk.Button(row, text=b("בחר..."),
                   command=self._choose_dl_dir).pack(side=tk.RIGHT, padx=4)

        # Delete after upload checkbox
        self.v_delete = tk.BooleanVar(
            value=bool(self.cfg.get("delete_after_upload", True)))
        row2 = ttk.Frame(dl); row2.pack(fill=tk.X, padx=6, pady=2)
        ttk.Checkbutton(row2,
                        text=b("מחק קובץ מהדיסק אחרי העלאה מוצלחת"),
                        variable=self.v_delete).pack(side=tk.RIGHT, padx=4)

        # Automation panel
        au = ttk.LabelFrame(f, text=b("אוטומציה"))
        au.pack(fill=tk.X, padx=8, pady=4)

        self.v_size_limit = tk.StringVar(
            value=str(self.cfg.get("size_limit_mb", 1900)))
        self.v_auto_split = tk.BooleanVar(
            value=bool(self.cfg.get("auto_split", True)))
        self.v_retry_count = tk.StringVar(
            value=str(self.cfg.get("retry_count", 2)))
        self.v_retry_delay = tk.StringVar(
            value=str(self.cfg.get("retry_delay", 10)))

        row3 = ttk.Frame(au); row3.pack(fill=tk.X, padx=6, pady=2)
        ttk.Label(row3, text=b("מגבלת גודל (MB)"), width=18).pack(side=tk.RIGHT)
        ttk.Entry(row3, textvariable=self.v_size_limit, width=10).pack(
            side=tk.RIGHT, padx=4)
        ttk.Label(row3, text=b("(0 = ללא מגבלה. ברירות: 50 לבוט, 1900 למשתמש, 3900 ל-Premium)"),
                  foreground="#666").pack(side=tk.RIGHT, padx=8)

        row4 = ttk.Frame(au); row4.pack(fill=tk.X, padx=6, pady=2)
        ttk.Checkbutton(row4,
                        text=b("פיצול אוטומטי לקבצים גדולים מהמגבלה (במקום דילוג)"),
                        variable=self.v_auto_split).pack(side=tk.RIGHT, padx=4)

        row5 = ttk.Frame(au); row5.pack(fill=tk.X, padx=6, pady=2)
        ttk.Label(row5, text=b("ניסיונות חוזרים"), width=18).pack(side=tk.RIGHT)
        ttk.Entry(row5, textvariable=self.v_retry_count, width=6).pack(
            side=tk.RIGHT, padx=4)
        ttk.Label(row5, text=b("השהיה בין ניסיונות (שניות)")).pack(
            side=tk.RIGHT, padx=8)
        ttk.Entry(row5, textvariable=self.v_retry_delay, width=6).pack(
            side=tk.RIGHT, padx=4)

        # Buttons
        br = ttk.Frame(f); br.pack(fill=tk.X, padx=8, pady=10)
        ttk.Button(br, text=b("שמור הגדרות"), command=self._save_cfg).pack(
            side=tk.RIGHT, padx=4)
        ttk.Button(br, text=b("התחבר ל-Emby ↓"), command=self._connect_emby,
                   style="Accent.TButton").pack(
            side=tk.RIGHT, padx=4)
        self.v_status = tk.StringVar(value=b("לא מחובר"))
        ttk.Label(br, textvariable=self.v_status,
                  foreground="#666").pack(side=tk.RIGHT, padx=12)

    def _choose_dl_dir(self) -> None:
        d = filedialog.askdirectory(initialdir=self.v_dldir.get() or str(Path.home()))
        if d:
            self.v_dldir.set(d)

    def _save_cfg(self) -> None:
        self.cfg.update({
            "base_url": self.v_base.get().strip(),
            "username": self.v_user.get().strip(),
            "password": self.v_pass.get(),
            "tg_mode": self.v_mode.get(),
            "api_id": int(self.v_api_id.get()) if self.v_api_id.get().strip() else 0,
            "api_hash": self.v_api_hash.get().strip(),
            "phone": self.v_phone.get().strip(),
            "bot_token": self.v_bot_token.get().strip(),
            "chat_id": self.v_chat.get().strip(),
            "download_dir": self.v_dldir.get().strip(),
            "delete_after_upload": bool(self.v_delete.get()),
            "size_limit_mb": int(self.v_size_limit.get() or 0),
            "auto_split": bool(self.v_auto_split.get()),
            "retry_count": int(self.v_retry_count.get() or 0),
            "retry_delay": int(self.v_retry_delay.get() or 5),
        })
        save_config(self.cfg)
        messagebox.showinfo(b("נשמר"), b("ההגדרות נשמרו ל-") + str(CONFIG_PATH))

    def _connect_emby(self) -> None:
        self.v_status.set(b("מתחבר..."))
        self.root.update()
        try:
            self.emby = EmbyClient(self.v_base.get().strip())
            self.emby.login(self.v_user.get().strip(), self.v_pass.get())
            self.v_status.set(b(f"מחובר ({self.emby.user_id[:8]}...)"))
            self.v_hdr_sub.set(b(f"מחובר כ-{self.v_user.get()} · "
                                 f"בחר תוכן בלשונית עיון ובחירה"))
            self._load_libraries()
            self.notebook.select(1)
        except Exception as e:
            self.v_status.set(b("נכשל"))
            self.v_hdr_sub.set(b("התחברות נכשלה"))
            messagebox.showerror(b("שגיאת חיבור"), str(e))

    # ---- Browse tab ----
    def _build_browse_tab(self) -> None:
        f = ttk.Frame(self.notebook)
        self.notebook.add(f, text="🔎  " + b("עיון ובחירה"))

        top = ttk.Frame(f); top.pack(fill=tk.X, padx=6, pady=4)
        ttk.Label(top, text=b("חיפוש:")).pack(side=tk.RIGHT)
        self.v_search = tk.StringVar()
        ent = ttk.Entry(top, textvariable=self.v_search)
        ent.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=4)
        ent.bind("<Return>", lambda e: self._do_search())
        self.search_entry = ent
        ttk.Button(top, text=b("חפש"), command=self._do_search).pack(side=tk.RIGHT)
        ttk.Button(top, text="✕", width=3,
                   command=lambda: (self.v_search.set(""), self._load_libraries())
                   ).pack(side=tk.RIGHT, padx=2)

        # Tree
        cols = ("type", "size")
        self.tree = ttk.Treeview(f, columns=cols, selectmode="extended")
        self.tree.heading("#0", text=b("שם"))
        self.tree.heading("type", text=b("סוג"))
        self.tree.heading("size", text=b("פרטים"))
        self.tree.column("#0", width=600, anchor="e")
        self.tree.column("type", width=80, anchor="e")
        self.tree.column("size", width=200, anchor="e")
        self.tree.heading("#0", anchor="e")
        self.tree.heading("type", anchor="e")
        self.tree.heading("size", anchor="e")
        self.tree.pack(fill=tk.BOTH, expand=True, padx=6, pady=4)
        self.tree.bind("<<TreeviewOpen>>", self._on_tree_open)

        # Right-click context menu in browse tree
        self.bmenu = tk.Menu(self.tree, tearoff=0)
        self.bmenu.add_command(label=b("הוסף לתור ←"),
                               command=self._add_selected_to_queue)
        self.bmenu.add_command(label=b("הוסף סדרה שלמה"),
                               command=self._add_series_to_queue)
        self.bmenu.add_separator()
        self.bmenu.add_command(label=b("פתח/סגור"),
                               command=self._toggle_tree_node)
        self.tree.bind("<Button-3>", self._show_bmenu)
        self.tree.bind("<Button-2>", self._show_bmenu)  # macOS

        bot = ttk.Frame(f); bot.pack(fill=tk.X, padx=6, pady=4)
        ttk.Button(bot, text=b("הוסף לתור ←"), command=self._add_selected_to_queue
                   ).pack(side=tk.RIGHT, padx=4)
        ttk.Button(bot, text=b("הוסף סדרה שלמה"),
                   command=self._add_series_to_queue).pack(side=tk.RIGHT, padx=4)
        self.v_browse_status = tk.StringVar(value="")
        ttk.Label(bot, textvariable=self.v_browse_status,
                  foreground="gray").pack(side=tk.RIGHT, padx=12)

    def _load_libraries(self) -> None:
        if not self.emby:
            return
        for n in self.tree.get_children():
            self.tree.delete(n)
        self.tree_items.clear()
        try:
            libs = self.emby.libraries()
        except Exception as e:
            messagebox.showerror(b("שגיאה"), b(f"שליפת ספריות: {e}"))
            return
        for lib in libs:
            ctype = lib.get("CollectionType") or "?"
            node = self.tree.insert("", "end", text=b(lib.get("Name", "?")),
                                    values=(ctype, ""), open=False)
            self.tree_items[node] = {"kind": "library", "item": lib}
            self.tree.insert(node, "end", text=b("טוען..."),
                             values=("", ""), tags=("placeholder",))
        self.v_browse_status.set(b(f"{len(libs)} ספריות"))

    def _on_tree_open(self, _evt) -> None:
        node = self.tree.focus()
        info = self.tree_items.get(node)
        if not info:
            return
        # Lazy load children
        children = self.tree.get_children(node)
        if len(children) == 1:
            child = children[0]
            if "placeholder" in self.tree.item(child, "tags"):
                self.tree.delete(child)
                self._populate(node, info)

    def _populate(self, parent_node: str, info: Dict[str, Any]) -> None:
        if not self.emby:
            return
        kind = info["kind"]
        item = info["item"]
        try:
            if kind == "library":
                ctype = item.get("CollectionType")
                if ctype == "movies":
                    children, total = self.emby.items(
                        parent_id=item["Id"], item_types="Movie", limit=10000)
                    for m in children:
                        n = self.tree.insert(parent_node, "end",
                                             text=b(m.get("Name", "?")),
                                             values=(b("סרט"), ""))
                        self.tree_items[n] = {"kind": "movie", "item": m}
                else:
                    children, total = self.emby.items(
                        parent_id=item["Id"], item_types="Series", limit=10000)
                    for sr in children:
                        n = self.tree.insert(parent_node, "end",
                                             text=b(sr.get("Name", "?")),
                                             values=(b("סדרה"), ""))
                        self.tree_items[n] = {"kind": "series", "item": sr}
                        self.tree.insert(n, "end", text=b("טוען..."),
                                         values=("", ""), tags=("placeholder",))
                self.v_browse_status.set(
                    f"{item.get('Name')}: {total}")
            elif kind == "series":
                seasons_raw, _ = self.emby.items(
                    parent_id=item["Id"], item_types="Season", limit=200)
                seasons = self.emby.dedup_by_index(seasons_raw)
                for sea in seasons:
                    sn = sea.get("IndexNumber", 0)
                    n = self.tree.insert(parent_node, "end",
                                         text=b(f"עונה {sn} - {sea.get('Name','')}"),
                                         values=(b("עונה"), ""))
                    self.tree_items[n] = {"kind": "season", "item": sea,
                                          "series": item}
                    self.tree.insert(n, "end", text=b("טוען..."),
                                     values=("", ""), tags=("placeholder",))
            elif kind == "season":
                eps_raw, _ = self.emby.items(
                    parent_id=item["Id"], item_types="Episode", limit=5000)
                eps = self.emby.dedup_by_index(eps_raw)
                for ep in eps:
                    en = ep.get("IndexNumber", 0)
                    n = self.tree.insert(parent_node, "end",
                                         text=b(f"פרק {en} - {ep.get('Name','')}"),
                                         values=(b("פרק"), ""))
                    self.tree_items[n] = {"kind": "episode", "item": ep,
                                          "season": item,
                                          "series": info.get("series")}
        except Exception as e:
            messagebox.showerror(b("שגיאה"), b(f"טעינת תוכן: {e}"))

    def _do_search(self) -> None:
        if not self.emby:
            return
        term = self.v_search.get().strip()
        if not term:
            self._load_libraries(); return
        for n in self.tree.get_children():
            self.tree.delete(n)
        self.tree_items.clear()
        try:
            series, _ = self.emby.items(
                item_types="Series", search_term=term, limit=200)
            movies, _ = self.emby.items(
                item_types="Movie", search_term=term, limit=200)
            for sr in series:
                n = self.tree.insert("", "end", text=b(sr.get("Name", "?")),
                                     values=(b("סדרה"), ""))
                self.tree_items[n] = {"kind": "series", "item": sr}
                self.tree.insert(n, "end", text=b("טוען..."),
                                 values=("", ""), tags=("placeholder",))
            for m in movies:
                n = self.tree.insert("", "end", text=b(m.get("Name", "?")),
                                     values=(b("סרט"), ""))
                self.tree_items[n] = {"kind": "movie", "item": m}
            self.v_browse_status.set(
                b(f"חיפוש '{term}': {len(series)} סדרות, {len(movies)} סרטים"))
        except Exception as e:
            messagebox.showerror(b("שגיאה"), str(e))

    def _add_selected_to_queue(self) -> None:
        sel = self.tree.selection()
        added = 0
        for node in sel:
            info = self.tree_items.get(node)
            if not info:
                continue
            kind = info["kind"]
            if kind in ("movie", "episode"):
                added += self._add_item_to_queue(info)
        self.v_browse_status.set(b(f"נוסף לתור: {added}"))
        if added:
            self._refresh_queue()
            self._filter_completed(added)

    def _add_series_to_queue(self) -> None:
        sel = self.tree.selection()
        added = 0
        for node in sel:
            info = self.tree_items.get(node)
            if not info or info["kind"] != "series":
                continue
            added += self._enumerate_series(info["item"])
        self.v_browse_status.set(b(f"נוסף לתור: {added} פרקים"))
        if added:
            self._refresh_queue()
            self._filter_completed(added)

    def _enumerate_series(self, series: Dict[str, Any]) -> int:
        seasons_raw, _ = self.emby.items(
            parent_id=series["Id"], item_types="Season", limit=200)
        seasons = self.emby.dedup_by_index(seasons_raw)
        n = 0
        for sea in seasons:
            eps_raw, _ = self.emby.items(
                parent_id=sea["Id"], item_types="Episode", limit=5000)
            eps = self.emby.dedup_by_index(eps_raw)
            for ep in eps:
                n += self._add_item_to_queue({
                    "kind": "episode", "item": ep,
                    "season": sea, "series": series,
                })
        return n

    def _add_item_to_queue(self, info: Dict[str, Any]) -> int:
        kind = info["kind"]
        item = info["item"]
        if kind == "movie":
            title = item.get("Name", "?")
            file_base = sanitize(title)
        else:  # episode
            sr = info.get("series", {})
            sea = info.get("season", {})
            sn = sea.get("IndexNumber", item.get("ParentIndexNumber", 0))
            en = item.get("IndexNumber", 0)
            sr_name = sr.get("Name", item.get("SeriesName", "?"))
            title = f"{sr_name} S{sn:02d}E{en:02d} - {item.get('Name','')}"
            file_base = sanitize(f"{sr_name} עונה {sn} פרק {en}")
        # Dedup: skip if already in queue (same item_id)
        if any(j.item_id == item["Id"] for j in self.jobs):
            return 0
        self.jobs.append(Job(title=title, item_id=item["Id"],
                             file_basename=file_base))
        return 1

    def _show_bmenu(self, event) -> None:
        iid = self.tree.identify_row(event.y)
        if iid and iid not in self.tree.selection():
            self.tree.selection_set(iid)
        try:
            self.bmenu.tk_popup(event.x_root, event.y_root)
        finally:
            self.bmenu.grab_release()

    def _toggle_tree_node(self) -> None:
        for n in self.tree.selection():
            self.tree.item(n, open=not self.tree.item(n, "open"))
            self._on_tree_open(None)

    def _filter_completed(self, added_count: int) -> None:
        """If history contains entries with status=ok matching newly-added
        items, ask user whether to remove those duplicates."""
        if added_count == 0:
            return
        completed_titles = {
            e["title"] for e in self.cfg.get("history", [])
            if e.get("status") == "ok"
        }
        # Find duplicates in the LAST added_count jobs
        dups = [j for j in self.jobs[-added_count:]
                if j.title in completed_titles]
        if not dups:
            return
        if messagebox.askyesno(
                b("פריטים שכבר הועלו"),
                b(f"{len(dups)} פריטים מתוך {added_count} כבר הועלו "
                  f"בעבר בהצלחה. לדלג עליהם?")):
            dup_titles = {j.title for j in dups}
            self.jobs = [j for j in self.jobs if j.title not in dup_titles]
            self._refresh_queue()

    # ---- Queue tab ----
    def _build_queue_tab(self) -> None:
        f = ttk.Frame(self.notebook)
        self.notebook.add(f, text="📥  " + b("תור"))

        # Overall progress bar at top
        topbar = ttk.Frame(f); topbar.pack(fill=tk.X, padx=6, pady=(4, 0))
        self.v_overall_text = tk.StringVar(value="")
        ttk.Label(topbar, textvariable=self.v_overall_text,
                  foreground="#555").pack(side=tk.RIGHT, padx=4)
        self.overall_progress = ttk.Progressbar(
            topbar, orient="horizontal", mode="determinate", maximum=100)
        self.overall_progress.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=4)

        # Filter row
        filterbar = ttk.Frame(f); filterbar.pack(fill=tk.X, padx=6, pady=(4, 0))
        self.v_qfilter = tk.StringVar()
        ttk.Label(filterbar, text=b("סנן:")).pack(side=tk.RIGHT)
        fe = ttk.Entry(filterbar, textvariable=self.v_qfilter)
        fe.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=4)
        fe.bind("<KeyRelease>", lambda e: self._refresh_queue())
        ttk.Button(filterbar, text="✕", width=3,
                   command=lambda: (self.v_qfilter.set(""),
                                    self._refresh_queue())
                   ).pack(side=tk.RIGHT, padx=2)

        cols = ("status", "progress")
        self.qtree = ttk.Treeview(f, columns=cols, show="tree headings")
        self.qtree.heading("#0", text=b("שם"), anchor="e")
        self.qtree.heading("status", text=b("סטטוס"), anchor="e")
        self.qtree.heading("progress", text=b("התקדמות"), anchor="e")
        self.qtree.column("#0", width=600, anchor="e")
        self.qtree.column("status", width=260, anchor="e")
        self.qtree.column("progress", width=110, anchor="e")
        # Color tags by state
        self.qtree.tag_configure("done", background="#dcedc8", foreground="#1b5e20")
        self.qtree.tag_configure("active", background="#bbdefb", foreground="#0d47a1")
        self.qtree.tag_configure("error", background="#ffcdd2", foreground="#b71c1c")
        self.qtree.tag_configure("skipped", background="#fff3e0", foreground="#e65100")
        self.qtree.tag_configure("pending", foreground="#666666")
        self.qtree.pack(fill=tk.BOTH, expand=True, padx=6, pady=4)

        # Right-click menu for queue
        self.qmenu = tk.Menu(self.qtree, tearoff=0)
        self.qmenu.add_command(label=b("הסר מהתור"), command=self._remove_selected)
        self.qmenu.add_command(label=b("נסה שוב"), command=self._retry_selected)
        self.qtree.bind("<Button-3>", self._show_qmenu)
        self.qtree.bind("<Button-2>", self._show_qmenu)  # macOS

        bot = ttk.Frame(f); bot.pack(fill=tk.X, padx=6, pady=4)
        ttk.Button(bot, text=b("▶ התחל"), command=self._start_worker,
                   style="Accent.TButton").pack(side=tk.RIGHT, padx=4)
        ttk.Button(bot, text=b("⏹ עצור"), command=self._stop_worker,
                   style="Danger.TButton").pack(side=tk.RIGHT, padx=4)
        ttk.Button(bot, text=b("הסר נבחרים"),
                   command=self._remove_selected).pack(side=tk.RIGHT, padx=4)
        ttk.Button(bot, text=b("נקה הושלמו"),
                   command=self._clear_done).pack(side=tk.RIGHT, padx=4)
        ttk.Button(bot, text=b("✕ נקה תור"),
                   command=self._clear_queue).pack(side=tk.RIGHT, padx=4)
        self.v_qstatus = tk.StringVar(value="")
        ttk.Label(bot, textvariable=self.v_qstatus,
                  foreground="#555").pack(side=tk.RIGHT, padx=12)

    def _status_tag(self, status: str, progress: float) -> str:
        # status may be already bidi-reordered on Linux, so check both
        # the original Hebrew words and their character-reversed forms.
        s = status
        DONE = ("הושלם", "םלשוה", "✓", "completed", "done")
        ERR = ("שגיאה", "האיגש", "נכשל", "לשכנ", "error", "FAIL", "TOO_BIG")
        SKIP = ("דילוג", "גוליד", "skipped", "מעל המגבלה", "הלבגמה לעמ")
        ACTIVE = ("מוריד", "דירומ", "מעלה", "הלעמ",
                  "שולף", "ףלוש", "כתוביות", "תויבותכ",
                  "פיצול", "לוציפ", "ממתין", "ןיתממ")
        if any(k in s for k in DONE):
            return "done"
        if any(k in s for k in SKIP):
            return "skipped"
        if any(k in s for k in ERR):
            return "error"
        if 0 < progress < 100 or any(k in s for k in ACTIVE if k not in
                                     ("ממתין","ןיתממ")):
            return "active"
        return "pending"

    def _refresh_queue(self) -> None:
        for c in self.qtree.get_children():
            self.qtree.delete(c)
        flt = (self.v_qfilter.get() if hasattr(self, "v_qfilter") else "").strip()
        shown = 0
        for i, j in enumerate(self.jobs):
            if flt and flt not in j.title:
                continue
            tag = self._status_tag(j.status, j.progress)
            self.qtree.insert("", "end", iid=str(i), text=b(j.title),
                              values=(j.status, f"{j.progress:.0f}%"),
                              tags=(tag,))
            shown += 1
        self._update_overall()
        if not self.jobs:
            # Empty state hint
            self.qtree.insert(
                "", "end",
                text=b("⌥ התור ריק — עבור ללשונית 'עיון ובחירה' והוסף תוכן"),
                values=("", ""), tags=("pending",))
            self.v_qstatus.set(b("התור ריק"))
        elif flt:
            self.v_qstatus.set(b(f"{shown}/{len(self.jobs)} פריטים (סינון פעיל)"))
        else:
            self.v_qstatus.set(b(f"{len(self.jobs)} פריטים בתור"))

    def _show_qmenu(self, event) -> None:
        iid = self.qtree.identify_row(event.y)
        if iid and iid not in self.qtree.selection():
            self.qtree.selection_set(iid)
        try:
            self.qmenu.tk_popup(event.x_root, event.y_root)
        finally:
            self.qmenu.grab_release()

    def _remove_selected(self) -> None:
        sel = sorted([int(s) for s in self.qtree.selection() if s.isdigit()],
                     reverse=True)
        if not sel:
            return
        if self.worker and self.worker.is_alive() and \
           self.worker.current_idx in sel:
            messagebox.showwarning(b("פעיל"),
                                   b("הפריט הזה רץ עכשיו - אי אפשר להסיר"))
            return
        for i in sel:
            if 0 <= i < len(self.jobs):
                del self.jobs[i]
        self._refresh_queue()

    def _clear_done(self) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showwarning(b("פעיל"),
                                   b("אי אפשר לערוך את התור בזמן עבודה"))
            return
        self.jobs = [j for j in self.jobs if "הושלם" not in j.status]
        self._refresh_queue()

    def _retry_selected(self) -> None:
        sel = [int(s) for s in self.qtree.selection() if s.isdigit()]
        for i in sel:
            if 0 <= i < len(self.jobs):
                self.jobs[i].status = "ממתין"
                self.jobs[i].progress = 0.0
                self.jobs[i].error = None
        self._refresh_queue()

    def _update_overall(self) -> None:
        if not self.jobs:
            self.overall_progress["value"] = 0
            self.v_overall_text.set("")
            return
        done = sum(1 for j in self.jobs if "הושלם" in j.status)
        err = sum(1 for j in self.jobs if "שגיאה" in j.status or "נכשל" in j.status)
        running = sum(1 for j in self.jobs
                      if 0 < j.progress < 100 and "הושלם" not in j.status)
        # Overall percent: sum of progress / (n*100)
        total = len(self.jobs) * 100
        cur = sum(j.progress if "הושלם" in j.status else min(j.progress, 100)
                  for j in self.jobs)
        # Completed contributes 100, others contribute progress
        cur = sum(100 if "הושלם" in j.status else j.progress for j in self.jobs)
        pct = (cur / total * 100) if total else 0
        self.overall_progress["value"] = pct
        eta = ""
        if self._stats_t0 and done:
            elapsed = time.time() - self._stats_t0
            per_item = elapsed / done
            left = len(self.jobs) - done
            eta_sec = int(per_item * left)
            eta = f" · ETA {eta_sec//60}m{eta_sec%60:02d}s"
        self.v_overall_text.set(
            b(f"{pct:.1f}% · הושלמו {done}/{len(self.jobs)} · בעבודה {running}"
              f" · שגיאות {err}{eta}"))

    def _tick_stats(self) -> None:
        try:
            self._update_overall()
            # Status bar updates
            if self.emby:
                self.v_sb_emby.set(b("Emby ✓"))
            else:
                self.v_sb_emby.set(b("Emby ✗"))
            running = self.worker and self.worker.is_alive()
            mode = self.v_mode.get() if hasattr(self, "v_mode") else "?"
            mode_text = {"bot":"בוט","user":"משתמש","local":"מקומי"}.get(mode, "?")
            self.v_sb_mode.set(b(f"מצב: {mode_text}"))
            self.v_sb_worker.set(b("Worker: רץ" if running else "Worker: עצור"))
        except Exception:
            pass
        self.root.after(800, self._tick_stats)

    def _clear_queue(self) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showwarning(b("פעיל"), b("אי אפשר לנקות בזמן עבודה"))
            return
        if not self.jobs:
            return
        if not messagebox.askyesno(
                b("נקה תור"),
                b(f"האם למחוק את כל {len(self.jobs)} הפריטים בתור?")):
            return
        self.jobs.clear()
        self._refresh_queue()

    def _start_worker(self) -> None:
        if not self.emby:
            messagebox.showwarning("Emby", b("התחבר ל-Emby קודם")); return
        if not self.jobs:
            messagebox.showwarning(b("ריק"), b("אין פריטים בתור")); return
        if self.worker and self.worker.is_alive():
            messagebox.showinfo(b("פעיל"), b("ה-worker כבר רץ")); return

        mode = self.v_mode.get()
        if mode == "local":
            uploader = LocalNoopUploader()
        else:
            api_id_s = self.v_api_id.get().strip()
            if not api_id_s or not self.v_api_hash.get().strip():
                messagebox.showerror("Telegram", b("חסרים API ID / API Hash")); return
            try:
                api_id = int(api_id_s)
            except ValueError:
                messagebox.showerror("Telegram", b("API ID חייב להיות מספר")); return
            if mode == "bot" and not self.v_bot_token.get().strip():
                messagebox.showerror("Telegram", b("חסר Bot Token")); return
            if mode == "user" and not self.v_phone.get().strip():
                messagebox.showerror("Telegram", b("חסר מספר טלפון")); return
            uploader = TelegramUploader(
                mode=mode, api_id=api_id,
                api_hash=self.v_api_hash.get().strip(),
                bot_token=self.v_bot_token.get().strip() or None,
                phone=self.v_phone.get().strip() or None,
                chat_id=self.v_chat.get().strip() or "me",
            )

        dl_dir = Path(self.v_dldir.get().strip() or DOWNLOAD_DIR_DEFAULT)
        dl_dir.mkdir(parents=True, exist_ok=True)

        self._refresh_queue()
        # In local mode we don't want to delete the file (the whole point is
        # to keep it locally). And the size limit is ignored.
        delete_after = bool(self.v_delete.get()) and mode != "local"
        size_limit = int(self.v_size_limit.get() or 0) if mode != "local" else 0
        self.worker = PipelineWorker(
            emby=self.emby, uploader=uploader, download_dir=dl_dir,
            status_cb=self._on_status, log_cb=self._log,
            delete_after_upload=delete_after,
            size_limit_mb=size_limit,
            auto_split=bool(self.v_auto_split.get()),
            retry_count=int(self.v_retry_count.get() or 0),
            retry_delay=int(self.v_retry_delay.get() or 5),
            history_cb=self._add_history,
        )
        self._stats_t0 = time.time()
        self.worker.enqueue([(i, j) for i, j in enumerate(self.jobs)])
        self.worker.start()
        self.v_qstatus.set(b(f"רץ ({len(self.jobs)} בתור)"))

    def _stop_worker(self) -> None:
        if self.worker:
            self.worker.stop()
            self.v_qstatus.set(b("מבקש עצירה..."))

    def _on_status(self, idx: int, text: str, pct: float) -> None:
        if idx >= len(self.jobs):
            return
        self.jobs[idx].status = text
        self.jobs[idx].progress = pct
        try:
            tag = self._status_tag(text, pct)
            self.qtree.item(str(idx), values=(text, f"{pct:.0f}%"),
                            tags=(tag,))
        except tk.TclError:
            pass

    # ---- Log tab ----
    def _build_log_tab(self) -> None:
        f = ttk.Frame(self.notebook)
        self.notebook.add(f, text="📋  " + b("לוג"))
        self.txt_log = scrolledtext.ScrolledText(f, wrap=tk.WORD)
        self.txt_log.pack(fill=tk.BOTH, expand=True, padx=6, pady=4)

    def _log(self, msg: str) -> None:
        ts = time.strftime("%H:%M:%S")
        try:
            self.txt_log.insert(tk.END, f"[{ts}] {msg}\n")
            self.txt_log.see(tk.END)
        except tk.TclError:
            pass

    # ---- Header ----
    def _build_header(self) -> None:
        h = ttk.Frame(self.root, padding=(12, 8))
        h.pack(side=tk.TOP, fill=tk.X)
        style = ttk.Style()
        style.configure("Header.TLabel", font=("TkDefaultFont", 16, "bold"),
                        foreground="#0d47a1")
        ttk.Label(h, text="🎬  Emby → Telegram Pipeline",
                  style="Header.TLabel").pack(side=tk.RIGHT)
        self.v_hdr_sub = tk.StringVar(value=b("התחבר ל-Emby כדי להתחיל"))
        ttk.Label(h, textvariable=self.v_hdr_sub,
                  foreground="#666").pack(side=tk.RIGHT, padx=12)
        ttk.Separator(self.root, orient="horizontal").pack(
            side=tk.TOP, fill=tk.X)

    # ---- History tab ----
    def _build_history_tab(self) -> None:
        f = ttk.Frame(self.notebook)
        self.notebook.add(f, text="📜  " + b("היסטוריה"))

        top = ttk.Frame(f); top.pack(fill=tk.X, padx=6, pady=4)
        ttk.Button(top, text=b("נקה היסטוריה"),
                   command=self._clear_history).pack(side=tk.RIGHT, padx=4)
        self.v_hist_count = tk.StringVar(value="")
        ttk.Label(top, textvariable=self.v_hist_count,
                  foreground="#666").pack(side=tk.RIGHT, padx=12)

        cols = ("status", "when", "elapsed")
        self.htree = ttk.Treeview(f, columns=cols, show="tree headings")
        self.htree.heading("#0", text=b("שם"), anchor="e")
        self.htree.heading("status", text=b("תוצאה"), anchor="e")
        self.htree.heading("when", text=b("מתי"), anchor="e")
        self.htree.heading("elapsed", text=b("משך"), anchor="e")
        self.htree.column("#0", width=600, anchor="e")
        self.htree.column("status", width=120, anchor="e")
        self.htree.column("when", width=160, anchor="e")
        self.htree.column("elapsed", width=80, anchor="e")
        self.htree.tag_configure("ok", foreground="#1b5e20")
        self.htree.tag_configure("skipped", foreground="#ef6c00")
        self.htree.tag_configure("error", foreground="#b71c1c")
        self.htree.pack(fill=tk.BOTH, expand=True, padx=6, pady=4)
        self._refresh_history()

    def _refresh_history(self) -> None:
        for c in self.htree.get_children():
            self.htree.delete(c)
        history = self.cfg.get("history", [])
        if not history:
            self.htree.insert("", "end",
                              text=b("⌥ אין היסטוריה - הפעל worker כדי להתחיל"),
                              values=("", "", ""))
            self.v_hist_count.set(b("ריק"))
            return
        for entry in reversed(history[-500:]):
            ts = entry.get("ts", 0)
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else "?"
            elapsed = entry.get("elapsed", 0)
            elapsed_s = f"{elapsed//60}m{elapsed%60:02d}s" if elapsed else "-"
            status = entry.get("status", "?")
            status_he = {"ok": "✓ הושלם", "skipped": "⊝ דילוג",
                         "error": "✗ שגיאה"}.get(status, status)
            tag = status if status in ("ok", "skipped", "error") else ""
            self.htree.insert("", "end", text=b(entry.get("title", "?")),
                              values=(b(status_he), when, elapsed_s),
                              tags=(tag,))
        self.v_hist_count.set(b(f"סה\"כ {len(history)} פריטים"))

    def _clear_history(self) -> None:
        if not messagebox.askyesno(b("נקה היסטוריה"),
                                   b("האם למחוק את כל היסטוריית ההורדות?")):
            return
        self.cfg["history"] = []
        save_config(self.cfg)
        self._refresh_history()

    def _add_history(self, entry: Dict[str, Any]) -> None:
        history = self.cfg.get("history", [])
        history.append(entry)
        # Cap at 500 most recent
        self.cfg["history"] = history[-500:]
        save_config(self.cfg)
        try:
            self.root.after(0, self._refresh_history)
        except Exception:
            pass

    # ---- Keyboard shortcuts ----
    def _bind_shortcuts(self) -> None:
        self.root.bind("<Control-Return>", lambda e: self._start_worker())
        self.root.bind("<Control-period>", lambda e: self._stop_worker())
        self.root.bind("<Control-s>", lambda e: self._save_cfg())
        self.root.bind("<Control-q>", lambda e: self._on_close())
        self.root.bind("<Control-f>", lambda e: self._focus_search())
        self.root.bind("<F5>", lambda e: self._connect_emby())

    def _focus_search(self) -> None:
        try:
            self.notebook.select(1)
            self.search_entry.focus_set()
        except Exception:
            pass

    # ---- Status bar ----
    def _build_status_bar(self) -> None:
        sb = ttk.Frame(self.root, relief="sunken", padding=(8, 3))
        sb.pack(side=tk.BOTTOM, fill=tk.X)
        self.v_sb_emby = tk.StringVar(value=b("Emby ✗"))
        self.v_sb_mode = tk.StringVar(value=b("מצב: ?"))
        self.v_sb_worker = tk.StringVar(value=b("Worker: עצור"))
        self.v_sb_hint = tk.StringVar(value=b(
            "קיצורים: Ctrl+Enter = התחל · Ctrl+. = עצור · Ctrl+F = חיפוש · F5 = התחבר"))
        ttk.Label(sb, textvariable=self.v_sb_emby).pack(side=tk.RIGHT, padx=8)
        ttk.Separator(sb, orient="vertical").pack(side=tk.RIGHT, fill=tk.Y, padx=4)
        ttk.Label(sb, textvariable=self.v_sb_mode).pack(side=tk.RIGHT, padx=8)
        ttk.Separator(sb, orient="vertical").pack(side=tk.RIGHT, fill=tk.Y, padx=4)
        ttk.Label(sb, textvariable=self.v_sb_worker).pack(side=tk.RIGHT, padx=8)
        ttk.Label(sb, textvariable=self.v_sb_hint,
                  foreground="#888").pack(side=tk.LEFT, padx=8)

    def _on_close(self) -> None:
        try:
            geom = self.root.geometry()
            self.cfg["window_geometry"] = geom
            self._persist_queue()
            save_config(self.cfg)
        except Exception:
            pass
        if self.worker and self.worker.is_alive():
            self.worker.stop()
        self.root.destroy()

    # ---- Queue persistence ----
    def _persist_queue(self) -> None:
        """Save non-completed jobs to config so the queue survives restart."""
        pending = []
        for j in self.jobs:
            if "הושלם" in j.status or "םלשוה" in j.status:
                continue
            pending.append({
                "title": j.title,
                "item_id": j.item_id,
                "file_basename": j.file_basename,
            })
        self.cfg["saved_queue"] = pending

    def _restore_queue(self) -> None:
        saved = self.cfg.get("saved_queue") or []
        if not saved:
            return
        for q in saved:
            self.jobs.append(Job(
                title=q.get("title", "?"),
                item_id=q.get("item_id", ""),
                file_basename=q.get("file_basename", "?"),
            ))
        self._refresh_queue()
        self._log(b(f"שוחזרו {len(saved)} פריטים מהתור הקודם"))

    # ---- Run ----
    def run(self) -> None:
        self.root.mainloop()


def main() -> int:
    if not HAS_PYROGRAM:
        print("התקן: pip install pyrogram tgcrypto cloudscraper", file=sys.stderr)
        return 1
    App().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
