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

    def playback_url(self, item_id: str) -> Optional[Tuple[str, str, int]]:
        """החזרת (URL, container, size). מחזיר None אם נכשל."""
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
        return direct, src.get("Container") or "mp4", int(src.get("Size") or 0)


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

class PipelineWorker(threading.Thread):
    def __init__(self, emby: EmbyClient, uploader: TelegramUploader,
                 download_dir: Path,
                 status_cb: Callable[[int, str, float], None],
                 log_cb: Callable[[str], None]):
        super().__init__(daemon=True)
        self.emby = emby
        self.uploader = uploader
        self.download_dir = download_dir
        self.q: "queue.Queue[Tuple[int, Job]]" = queue.Queue()
        self.status_cb = status_cb
        self.log_cb = log_cb
        self.stop_flag = threading.Event()
        self.current_idx: Optional[int] = None

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
            try:
                self._process(idx, job)
            except Exception as e:
                tb = traceback.format_exc()
                self.log_cb(f"[{job.title}] FATAL: {e}\n{tb}")
                self.status_cb(idx, b(f"שגיאה: {e}"), 0)
            self.q.task_done()

        try:
            self.uploader.stop()
        except Exception:
            pass
        self.log_cb(b("Worker נעצר"))

    def _process(self, idx: int, job: Job) -> None:
        self.status_cb(idx, b("שולף URL"), 0)
        info = self.emby.playback_url(job.item_id)
        if not info:
            raise RuntimeError(b("PlaybackInfo החזיר שגיאה"))
        url, container, size = info
        if container not in ("mp4", "mkv", "webm", "m4v", "mov", "avi", "ts"):
            container = "mp4"
        file_name = sanitize(f"{job.file_basename}.{container}")
        dest = self.download_dir / file_name

        # Download
        self.log_cb(f"[{job.title}] מוריד {human_size(size) if size else '?'}")
        def dl_prog(done, total):
            pct = (done / total * 100) if total else 0
            self.status_cb(idx, b(f"מוריד {human_size(done)}/{human_size(total)}"), pct)
        stream_download(self.emby.s, url, dest, dl_prog)

        # Upload
        actual = dest.stat().st_size
        self.log_cb(f"[{job.title}] מעלה {human_size(actual)}")
        def up_prog(done, total):
            pct = (done / total * 100) if total else 0
            self.status_cb(idx, b(f"מעלה {human_size(done)}/{human_size(total)}"), pct)
        try:
            self.uploader.upload(dest, caption=job.title, progress_cb=up_prog)
        except Exception as e:
            self.log_cb(f"[{job.title}] העלאה נכשלה: {e}")
            raise

        # Delete
        try:
            dest.unlink()
            self.log_cb(f"[{job.title}] קובץ נמחק")
        except OSError as e:
            self.log_cb(f"[{job.title}] לא הצלחתי למחוק: {e}")

        self.status_cb(idx, b("הושלם"), 100)


# ============================================================================
# GUI

class App:
    def __init__(self) -> None:
        self.cfg = load_config()
        self.emby: Optional[EmbyClient] = None
        self.worker: Optional[PipelineWorker] = None

        self.root = tk.Tk()
        self.root.title("Emby → Telegram Pipeline")
        self.root.geometry("1100x720")

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        self._build_config_tab()
        self._build_browse_tab()
        self._build_queue_tab()
        self._build_log_tab()

        self.jobs: List[Job] = []  # selected items waiting to run
        self.tree_items: Dict[str, Dict[str, Any]] = {}  # tree node id -> item dict

    # ---- Config tab ----
    def _build_config_tab(self) -> None:
        f = ttk.Frame(self.notebook)
        self.notebook.add(f, text=b("הגדרות"))

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
            ttk.Label(row, text=label, width=14).pack(side=tk.LEFT)
            ttk.Entry(row, textvariable=var, show=show).pack(
                side=tk.LEFT, fill=tk.X, expand=True)

        # Telegram
        tg = ttk.LabelFrame(f, text="Telegram")
        tg.pack(fill=tk.X, padx=8, pady=4)
        self.v_mode = tk.StringVar(value=self.cfg.get("tg_mode", "bot"))
        mr = ttk.Frame(tg); mr.pack(fill=tk.X, padx=6, pady=2)
        ttk.Label(mr, text=b("מצב"), width=14).pack(side=tk.LEFT)
        ttk.Radiobutton(mr, text=b("בוט (עד 50MB)"), variable=self.v_mode,
                        value="bot").pack(side=tk.LEFT)
        ttk.Radiobutton(mr, text=b("חשבון משתמש (עד 2GB)"), variable=self.v_mode,
                        value="user").pack(side=tk.LEFT)

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
            ttk.Label(row, text=label, width=14).pack(side=tk.LEFT)
            ttk.Entry(row, textvariable=var, show=show).pack(
                side=tk.LEFT, fill=tk.X, expand=True)

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
        ttk.Label(row, text=b("תיקייה זמנית"), width=14).pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=self.v_dldir).pack(
            side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(row, text=b("בחר..."),
                   command=self._choose_dl_dir).pack(side=tk.LEFT, padx=4)

        # Buttons
        br = ttk.Frame(f); br.pack(fill=tk.X, padx=8, pady=10)
        ttk.Button(br, text=b("שמור הגדרות"), command=self._save_cfg).pack(
            side=tk.LEFT, padx=4)
        ttk.Button(br, text=b("התחבר ל-Emby ↓"), command=self._connect_emby).pack(
            side=tk.LEFT, padx=4)
        self.v_status = tk.StringVar(value=b("לא מחובר"))
        ttk.Label(br, textvariable=self.v_status,
                  foreground="gray").pack(side=tk.LEFT, padx=12)

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
            self._load_libraries()
            self.notebook.select(1)
        except Exception as e:
            self.v_status.set(b("נכשל"))
            messagebox.showerror(b("שגיאת חיבור"), str(e))

    # ---- Browse tab ----
    def _build_browse_tab(self) -> None:
        f = ttk.Frame(self.notebook)
        self.notebook.add(f, text=b("עיון ובחירה"))

        top = ttk.Frame(f); top.pack(fill=tk.X, padx=6, pady=4)
        ttk.Label(top, text=b("חיפוש:")).pack(side=tk.LEFT)
        self.v_search = tk.StringVar()
        ent = ttk.Entry(top, textvariable=self.v_search)
        ent.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)
        ent.bind("<Return>", lambda e: self._do_search())
        ttk.Button(top, text=b("חפש"), command=self._do_search).pack(side=tk.LEFT)
        ttk.Button(top, text="✕", width=3,
                   command=lambda: (self.v_search.set(""), self._load_libraries())
                   ).pack(side=tk.LEFT, padx=2)

        # Tree
        cols = ("type", "size")
        self.tree = ttk.Treeview(f, columns=cols, selectmode="extended")
        self.tree.heading("#0", text=b("שם"))
        self.tree.heading("type", text=b("סוג"))
        self.tree.heading("size", text=b("פרטים"))
        self.tree.column("#0", width=600)
        self.tree.column("type", width=80, anchor="center")
        self.tree.column("size", width=200)
        self.tree.pack(fill=tk.BOTH, expand=True, padx=6, pady=4)
        self.tree.bind("<<TreeviewOpen>>", self._on_tree_open)

        bot = ttk.Frame(f); bot.pack(fill=tk.X, padx=6, pady=4)
        ttk.Button(bot, text=b("הוסף לתור ←"), command=self._add_selected_to_queue
                   ).pack(side=tk.LEFT, padx=4)
        ttk.Button(bot, text=b("הוסף סדרה שלמה"),
                   command=self._add_series_to_queue).pack(side=tk.LEFT, padx=4)
        self.v_browse_status = tk.StringVar(value="")
        ttk.Label(bot, textvariable=self.v_browse_status,
                  foreground="gray").pack(side=tk.LEFT, padx=12)

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
        self.jobs.append(Job(title=title, item_id=item["Id"],
                             file_basename=file_base))
        return 1

    # ---- Queue tab ----
    def _build_queue_tab(self) -> None:
        f = ttk.Frame(self.notebook)
        self.notebook.add(f, text=b("תור"))

        cols = ("status", "progress")
        self.qtree = ttk.Treeview(f, columns=cols, show="tree headings")
        self.qtree.heading("#0", text=b("שם"))
        self.qtree.heading("status", text=b("סטטוס"))
        self.qtree.heading("progress", text=b("התקדמות"))
        self.qtree.column("#0", width=600)
        self.qtree.column("status", width=240)
        self.qtree.column("progress", width=120)
        self.qtree.pack(fill=tk.BOTH, expand=True, padx=6, pady=4)

        bot = ttk.Frame(f); bot.pack(fill=tk.X, padx=6, pady=4)
        ttk.Button(bot, text=b("▶ התחל"), command=self._start_worker).pack(
            side=tk.LEFT, padx=4)
        ttk.Button(bot, text=b("✕ נקה תור"), command=self._clear_queue).pack(
            side=tk.LEFT, padx=4)
        ttk.Button(bot, text=b("⏹ עצור"), command=self._stop_worker).pack(
            side=tk.LEFT, padx=4)
        self.v_qstatus = tk.StringVar(value="")
        ttk.Label(bot, textvariable=self.v_qstatus,
                  foreground="gray").pack(side=tk.LEFT, padx=12)

    def _refresh_queue(self) -> None:
        for c in self.qtree.get_children():
            self.qtree.delete(c)
        for i, j in enumerate(self.jobs):
            self.qtree.insert("", "end", iid=str(i), text=j.title,
                              values=(j.status, f"{j.progress:.0f}%"))
        self.v_qstatus.set(b(f"{len(self.jobs)} פריטים בתור"))

    def _clear_queue(self) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showwarning(b("פעיל"), b("אי אפשר לנקות בזמן עבודה"))
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
        self.worker = PipelineWorker(
            emby=self.emby, uploader=uploader, download_dir=dl_dir,
            status_cb=self._on_status, log_cb=self._log,
        )
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
            self.qtree.item(str(idx), values=(text, f"{pct:.0f}%"))
        except tk.TclError:
            pass

    # ---- Log tab ----
    def _build_log_tab(self) -> None:
        f = ttk.Frame(self.notebook)
        self.notebook.add(f, text=b("לוג"))
        self.txt_log = scrolledtext.ScrolledText(f, wrap=tk.WORD)
        self.txt_log.pack(fill=tk.BOTH, expand=True, padx=6, pady=4)

    def _log(self, msg: str) -> None:
        ts = time.strftime("%H:%M:%S")
        try:
            self.txt_log.insert(tk.END, f"[{ts}] {msg}\n")
            self.txt_log.see(tk.END)
        except tk.TclError:
            pass

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
