import os
import re
import sys
import time
import queue
import sqlite3
import tempfile
import threading
import subprocess
import urllib.request
from io import BytesIO
from pathlib import Path

import customtkinter as ctk
from PIL import Image
from tkinter import filedialog, messagebox
import yt_dlp
from yt_dlp.utils import DownloadCancelled

from music_filter import (
    classify_flat,
    classify_full,
    load_archive_ids,
    remove_archive_id,
)

BG = "#12141c"
CARD = "#1c2030"
CARD_SOFT = "#171b28"
INK = "#f4f6fb"
MUTED = "#a7b0c4"
LINE = "#2c3348"
GOLD = "#e4b34a"
GOLD_HOVER = "#f0c56a"
GREEN = "#7dcea0"
DIM = "#8b93a7"


def get_bundle_dir():
    if getattr(sys, "frozen", False):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


def support_dir():
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "YTHistoryMusic"
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", str(Path.home()))) / "YTHistoryMusic"
    else:
        base = Path.home() / ".local" / "share" / "YTHistoryMusic"
    base.mkdir(parents=True, exist_ok=True)
    return base


def cookie_path(browser):
    return str(support_dir() / f"cookies-{browser}.txt")


def _chrome_login_error(source, err):
    code = getattr(err, "winerror", None)
    if not code:
        code = getattr(err, "errno", None)
    return OSError(f"Could not read Chrome's login file: {source} (Windows error {code})")


def _read_with_shared_access(source):
    """Read a file another program still has open."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel32.ReadFile.restype = wintypes.BOOL
    kernel32.ReadFile.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
    ]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    generic_read = 0x80000000
    share = 0x1 | 0x2 | 0x4
    open_existing = 3
    invalid = ctypes.c_void_p(-1).value

    handle = kernel32.CreateFileW(source, generic_read, share, None, open_existing, 0x80, None)
    if handle in (invalid, None, 0):
        raise OSError(f"Could not read Chrome's login file: {source} (Windows error {ctypes.get_last_error()})")
    try:
        chunk = ctypes.create_string_buffer(1024 * 1024)
        read = wintypes.DWORD()
        pieces = []
        while True:
            ok = kernel32.ReadFile(handle, chunk, len(chunk), ctypes.byref(read), None)
            if not ok:
                raise OSError(f"Could not read Chrome's login file: {source} (Windows error {ctypes.get_last_error()})")
            if read.value == 0:
                break
            pieces.append(chunk.raw[: read.value])
    finally:
        kernel32.CloseHandle(handle)
    return b"".join(pieces)


def _read_cookie_bytes(source):
    try:
        with open(source, "rb") as handle:
            return handle.read()
    except PermissionError as err:
        try:
            return _read_with_shared_access(source)
        except OSError:
            raise _chrome_login_error(source, err) from err


def _write_bytes(destination, data):
    with open(destination, "wb") as output:
        output.write(data)


def _copy_cookie_files(database_path, destination):
    data = _read_cookie_bytes(database_path)
    if not data:
        raise OSError(f"Could not read Chrome's login file: {database_path} (Windows error 0)")
    _write_bytes(destination, data)
    for suffix in ("-wal", "-shm"):
        side_file = database_path + suffix
        if os.path.isfile(side_file):
            try:
                _write_bytes(destination + suffix, _read_cookie_bytes(side_file))
            except OSError:
                pass


def _chrome_cookie_candidates(database_path):
    """User profiles only. The newest Cookies file can be a system profile we cannot read."""
    user_data = None
    current = os.path.dirname(os.path.abspath(database_path))
    for _ in range(8):
        if os.path.basename(current).lower() == "user data":
            user_data = current
            break
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    found = []
    if user_data and os.path.isdir(user_data):
        try:
            names = os.listdir(user_data)
        except OSError:
            names = []
        for name in names:
            if name in ("System Profile", "Guest Profile"):
                continue
            for parts in (("Network", "Cookies"), ("Cookies",)):
                candidate = os.path.join(user_data, name, *parts)
                if os.path.isfile(candidate):
                    found.append(candidate)
    if os.path.isfile(database_path) and "System Profile" not in database_path and "Guest Profile" not in database_path:
        found.append(database_path)
    unique = []
    for path in found:
        if path not in unique:
            unique.append(path)
    unique.sort(key=lambda path: os.path.getmtime(path), reverse=True)
    return unique or [database_path]


def _copy_while_unlocked(path, destination):
    """Close whatever is locking the file, copy it, then start that program again."""
    import ctypes
    from ctypes import wintypes

    rstrtmgr = ctypes.WinDLL("Rstrtmgr")
    rstrtmgr.RmStartSession.argtypes = [ctypes.POINTER(wintypes.DWORD), wintypes.DWORD, wintypes.LPWSTR]
    rstrtmgr.RmRegisterResources.argtypes = [
        wintypes.DWORD, wintypes.UINT, ctypes.POINTER(wintypes.LPCWSTR),
        wintypes.UINT, ctypes.c_void_p, wintypes.UINT, ctypes.c_void_p,
    ]
    rstrtmgr.RmGetList.argtypes = [
        wintypes.DWORD, ctypes.POINTER(wintypes.UINT), ctypes.POINTER(wintypes.UINT),
        ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD),
    ]
    rstrtmgr.RmShutdown.argtypes = [wintypes.DWORD, wintypes.ULONG, ctypes.c_void_p]
    rstrtmgr.RmRestart.argtypes = [wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    rstrtmgr.RmEndSession.argtypes = [wintypes.DWORD]
    for name in ("RmStartSession", "RmRegisterResources", "RmGetList", "RmShutdown", "RmRestart", "RmEndSession"):
        getattr(rstrtmgr, name).restype = wintypes.DWORD

    session = wintypes.DWORD()
    key = ctypes.create_unicode_buffer(33)
    if rstrtmgr.RmStartSession(ctypes.byref(session), 0, key) != 0:
        return False
    closed = False
    try:
        files = (wintypes.LPCWSTR * 1)(path)
        if rstrtmgr.RmRegisterResources(session, 1, files, 0, None, 0, None) != 0:
            return False
        needed = wintypes.UINT(0)
        count = wintypes.UINT(0)
        reasons = wintypes.DWORD()
        if rstrtmgr.RmGetList(session, ctypes.byref(needed), ctypes.byref(count), None, ctypes.byref(reasons)) not in (0, 234):
            return False
        if needed.value > 0 and rstrtmgr.RmShutdown(session, 1, None) != 0:
            return False
        closed = needed.value > 0
        for _ in range(20):
            try:
                _copy_cookie_files(path, destination)
                return True
            except OSError:
                if not closed:
                    return False
                time.sleep(0.25)
        return False
    finally:
        if closed:
            rstrtmgr.RmRestart(session, 0, None)
        rstrtmgr.RmEndSession(session)


def _file_lock_count(path):
    import ctypes
    from ctypes import wintypes

    rstrtmgr = ctypes.WinDLL("Rstrtmgr")
    rstrtmgr.RmStartSession.argtypes = [ctypes.POINTER(wintypes.DWORD), wintypes.DWORD, wintypes.LPWSTR]
    rstrtmgr.RmRegisterResources.argtypes = [
        wintypes.DWORD, wintypes.UINT, ctypes.POINTER(wintypes.LPCWSTR),
        wintypes.UINT, ctypes.c_void_p, wintypes.UINT, ctypes.c_void_p,
    ]
    rstrtmgr.RmGetList.argtypes = [
        wintypes.DWORD, ctypes.POINTER(wintypes.UINT), ctypes.POINTER(wintypes.UINT),
        ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD),
    ]
    rstrtmgr.RmEndSession.argtypes = [wintypes.DWORD]
    for name in ("RmStartSession", "RmRegisterResources", "RmGetList", "RmEndSession"):
        getattr(rstrtmgr, name).restype = wintypes.DWORD
    session = wintypes.DWORD()
    key = ctypes.create_unicode_buffer(33)
    if rstrtmgr.RmStartSession(ctypes.byref(session), 0, key) != 0:
        return 0
    try:
        files = (wintypes.LPCWSTR * 1)(path)
        if rstrtmgr.RmRegisterResources(session, 1, files, 0, None, 0, None) != 0:
            return 0
        needed = wintypes.UINT(0)
        count = wintypes.UINT(0)
        reasons = wintypes.DWORD()
        if rstrtmgr.RmGetList(session, ctypes.byref(needed), ctypes.byref(count), None, ctypes.byref(reasons)) not in (0, 234):
            return 0
        return needed.value
    finally:
        rstrtmgr.RmEndSession(session)


def _open_chrome_database(database_path, tmpdir):
    import sqlite3

    copy_path = os.path.join(tmpdir, "temporary.sqlite")
    candidates = _chrome_cookie_candidates(database_path)
    first_error = None
    for candidate in candidates:
        try:
            _copy_cookie_files(candidate, copy_path)
            return sqlite3.connect(copy_path).cursor()
        except OSError as err:
            if first_error is None:
                first_error = err
            if _file_lock_count(candidate) <= 0:
                continue
            if messagebox.askyesno(
                "Chrome is open",
                "Chrome is locking its login, so your watch history cannot be read.\n\n"
                "Chrome will close for a moment and then open again. Continue?",
            ) and _copy_while_unlocked(candidate, copy_path):
                return sqlite3.connect(copy_path).cursor()
            break
    if first_error is not None:
        raise RuntimeError(str(first_error))
    raise RuntimeError(f"Could not read Chrome's login file: {database_path}")


def _use_windows_cookie_copy():
    if sys.platform != "win32":
        return
    import yt_dlp.cookies as cookies
    cookies._open_database_copy = _open_chrome_database


_use_windows_cookie_copy()


def _cookie_failure_text(exc):
    seen = []
    current = exc
    while current is not None and current not in seen:
        seen.append(current)
        current = current.__cause__ or current.__context__
    for item in seen:
        text = str(item).replace("ERROR: ", "").strip()
        if "login file" in text:
            return text
    text = str(exc).replace("ERROR: ", "").strip()
    if "7271" in text or "cookie database" in text.lower():
        return "Windows blocked Chrome's login file. In Task Manager, end every Chrome process, including background processes, then press Start again."
    return text


def prepare_cookie_file(browser):
    """Read the browser login once, then reuse the saved cookie file.

    Chrome unlocks its cookie store through the macOS keychain. Doing that on
    every launch is what raises the password dialog. A later run uses the file
    and does not touch the keychain.
    """
    path = cookie_path(browser)
    if os.path.isfile(path) and os.path.getsize(path) > 0:
        return path
    try:
        with yt_dlp.YoutubeDL({
            "cookiesfrombrowser": (browser,),
            "cookiefile": path,
            "quiet": True,
            "no_warnings": True,
        }) as ydl:
            ydl.cookiejar  # load once; saving happens when the downloader closes
    except Exception as exc:
        raise RuntimeError(_cookie_failure_text(exc)) from exc
    if not os.path.isfile(path) or os.path.getsize(path) == 0:
        raise RuntimeError(f"Could not save a login from {browser}")
    return path


_VIDEO_ID = re.compile(r"(?:[?&]v=|youtu\.be/|shorts/|embed/)([A-Za-z0-9_-]{11})")


def _chromium_user_data(browser):
    local = os.environ.get("LOCALAPPDATA", "")
    roaming = os.environ.get("APPDATA", "")
    paths = {
        "chrome": [os.path.join(local, "Google", "Chrome", "User Data")],
        "edge": [os.path.join(local, "Microsoft", "Edge", "User Data")],
        "brave": [os.path.join(local, "BraveSoftware", "Brave-Browser", "User Data")],
        "opera": [
            os.path.join(roaming, "Opera Software", "Opera Stable"),
            os.path.join(local, "Opera Software", "Opera Stable"),
        ],
    }
    for path in paths.get(browser, []):
        if path and os.path.isdir(path):
            return path
    return None


def _history_files(browser):
    root = _chromium_user_data(browser)
    if not root:
        return []
    found = []
    try:
        names = os.listdir(root)
    except OSError:
        return []
    for name in names:
        if name in ("System Profile", "Guest Profile"):
            continue
        path = os.path.join(root, name, "History")
        if os.path.isfile(path):
            found.append(path)
    found.sort(key=os.path.getmtime, reverse=True)
    return found


def _copy_history_file(source, destination):
    try:
        _copy_cookie_files(source, destination)
        return
    except OSError:
        pass
    if _file_lock_count(source) > 0 and messagebox.askyesno(
        "Chrome is open",
        "Chrome is locking its history, so the video list cannot be read.\n\n"
        "Chrome will close for a moment and then open again. Continue?",
    ):
        if _copy_while_unlocked(source, destination):
            return
    raise RuntimeError("Could not read Chrome's history. Quit Chrome, including background processes, then press Start again.")


def local_history_entries(browser, depth):
    """YouTube videos Chrome has already stored. The login itself cannot be decrypted."""
    files = _history_files(browser)
    if not files:
        raise RuntimeError(f"Could not find {browser} history on this computer.")
    limit = None if depth == "All" else int(depth)
    seen = set()
    entries = []
    for source in files:
        with tempfile.TemporaryDirectory(prefix="ythistory") as tmpdir:
            copy_path = os.path.join(tmpdir, "History")
            _copy_history_file(source, copy_path)
            connection = sqlite3.connect(copy_path)
            try:
                rows = connection.execute(
                    "SELECT url, title FROM urls "
                    "WHERE url LIKE '%youtube.com/%' OR url LIKE '%youtu.be/%' "
                    "ORDER BY last_visit_time DESC"
                ).fetchall()
            except sqlite3.Error as err:
                raise RuntimeError("Chrome's history file could not be read.") from err
            finally:
                connection.close()
        for url, title in rows:
            match = _VIDEO_ID.search(url or "")
            if not match or match.group(1) in seen:
                continue
            seen.add(match.group(1))
            clean = (title or "Unknown").removesuffix(" - YouTube").strip() or "Unknown"
            entries.append({
                "id": match.group(1),
                "title": clean,
                "url": f"https://www.youtube.com/watch?v={match.group(1)}",
                "webpage_url": f"https://www.youtube.com/watch?v={match.group(1)}",
            })
            if limit is not None and len(entries) >= limit:
                return entries
        if entries:
            return entries
    return entries


def get_binary_path(name):
    exe_name = f"{name}.exe" if sys.platform == "win32" else name
    bundled = os.path.join(get_bundle_dir(), exe_name)
    if os.path.exists(bundled):
        return bundled
    return name


def video_url(entry):
    url = entry.get("webpage_url") or entry.get("url") or ""
    if isinstance(url, str) and url.startswith("http"):
        return url
    video_id = entry.get("id") or (url if isinstance(url, str) else "")
    if video_id:
        return f"https://www.youtube.com/watch?v={video_id}"
    return ""


def thumb_url(entry):
    if entry.get("thumbnail"):
        return entry["thumbnail"]
    thumbs = entry.get("thumbnails") or []
    if thumbs and isinstance(thumbs[-1], dict):
        return thumbs[-1].get("url") or ""
    video_id = entry.get("id") or ""
    if len(video_id) == 11:
        return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
    return ""


def song_item(entry):
    artist = entry.get("artist") or entry.get("track") or ""
    return {
        "id": entry.get("id") or "",
        "title": entry.get("title") or "Unknown",
        "uploader": entry.get("uploader") or entry.get("channel") or "",
        "artist": artist,
        "url": video_url(entry),
        "thumbnail": thumb_url(entry),
        "path": "",
    }


def notify(title, body):
    if sys.platform != "darwin":
        return

    def esc(text):
        return text.replace("\\", "\\\\").replace('"', '\\"')

    script = f'display notification "{esc(body)}" with title "{esc(title)}"'
    subprocess.run(["osascript", "-e", script], check=False)


def reveal_path(path, folder):
    if sys.platform == "darwin" and path and os.path.isfile(path):
        subprocess.run(["open", "-R", path], check=False)
        return
    os.makedirs(folder, exist_ok=True)
    if sys.platform == "darwin":
        subprocess.run(["open", folder], check=False)
    elif sys.platform == "win32":
        os.startfile(folder)


class YTHistoryMusicApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.title("YouTube History Songs")
        self.geometry("980x760")
        self.minsize(880, 680)
        self.configure(fg_color=BG)

        self.default_output_dir = str(Path.home() / "Downloads" / "Downloaded_Songs")
        self.output_dir_var = ctk.StringVar(value=self.default_output_dir)

        self.ui_queue = queue.Queue()
        self.song_queue = queue.Queue()
        self.scan_finished = threading.Event()
        self.cancel_event = threading.Event()
        self.skip_current = threading.Event()
        self.resume_event = threading.Event()
        self.resume_event.set()

        self.running = False
        self._cookie_file = None
        self._use_local_history = False
        self.upcoming = []
        self.archived_ids = set()
        self.archive_path = ""
        self._partials = []
        self._final_path = ""
        self._active_item = None
        self.saved_count = 0
        self.already_count = 0
        self._upcoming_dirty = False
        self._mini_labels = {}
        self._thumb_bytes = {}
        self._thumb_lock = threading.Lock()
        self._render_gen = 0
        self._now_id = ""

        self._build()
        self.after(60, self._poll_ui)

    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(4, weight=1)

        header = ctk.CTkFrame(self, fg_color=BG)
        header.grid(row=0, column=0, sticky="ew", padx=22, pady=(18, 8))
        ctk.CTkLabel(
            header,
            text="YouTube History Songs",
            font=ctk.CTkFont(family="Helvetica Neue", size=24, weight="bold"),
            text_color=INK,
        ).pack(anchor="w")
        ctk.CTkLabel(
            header,
            text="Saves songs from your own watch history, for personal use.",
            font=ctk.CTkFont(family="Helvetica Neue", size=13),
            text_color=MUTED,
        ).pack(anchor="w", pady=(2, 0))
        ctk.CTkLabel(
            header,
            text="For personal use only. You are responsible for complying with YouTube’s Terms of Service and copyright law.",
            font=ctk.CTkFont(family="Helvetica Neue", size=12),
            text_color=DIM,
            wraplength=920,
            justify="left",
        ).pack(anchor="w", pady=(8, 0))
        if sys.platform == "darwin":
            ctk.CTkLabel(
                header,
                text="On a Mac, the first time you press Start, the computer asks for your login password. That lets the app read Chrome. Allow it once. It does not ask again.",
                font=ctk.CTkFont(family="Helvetica Neue", size=12),
                text_color=MUTED,
                wraplength=920,
                justify="left",
            ).pack(anchor="w", pady=(4, 0))

        controls = ctk.CTkFrame(self, fg_color=CARD, corner_radius=16)
        controls.grid(row=1, column=0, sticky="ew", padx=22, pady=(4, 8))
        controls.grid_columnconfigure(3, weight=1)

        ctk.CTkLabel(controls, text="Browser", text_color=MUTED).grid(row=0, column=0, padx=(14, 6), pady=(14, 4), sticky="w")
        self.browser_menu = ctk.CTkOptionMenu(
            controls,
            values=["chrome", "firefox", "edge", "brave", "opera", "safari"],
            width=130,
            fg_color=CARD_SOFT,
            button_color=LINE,
            button_hover_color=GOLD,
        )
        self.browser_menu.set("chrome")
        self.browser_menu.grid(row=1, column=0, padx=(14, 6), pady=(0, 14), sticky="w")

        ctk.CTkLabel(controls, text="Scan depth", text_color=MUTED).grid(row=0, column=1, padx=6, pady=(14, 4), sticky="w")
        self.depth_menu = ctk.CTkOptionMenu(
            controls,
            values=["50", "100", "250", "500", "All"],
            width=110,
            fg_color=CARD_SOFT,
            button_color=LINE,
            button_hover_color=GOLD,
        )
        self.depth_menu.set("100")
        self.depth_menu.grid(row=1, column=1, padx=6, pady=(0, 14), sticky="w")

        ctk.CTkLabel(controls, text="Save to", text_color=MUTED).grid(row=0, column=3, padx=6, pady=(14, 4), sticky="w")
        self.folder_entry = ctk.CTkEntry(
            controls,
            textvariable=self.output_dir_var,
            height=32,
            fg_color=CARD_SOFT,
            border_color=LINE,
        )
        self.folder_entry.grid(row=1, column=3, padx=6, pady=(0, 14), sticky="ew")
        ctk.CTkButton(
            controls,
            text="Browse",
            width=88,
            fg_color=LINE,
            hover_color="#3a425c",
            command=self._choose_folder,
        ).grid(row=1, column=4, padx=(6, 14), pady=(0, 14))

        actions = ctk.CTkFrame(self, fg_color=BG)
        actions.grid(row=2, column=0, sticky="ew", padx=22, pady=(0, 8))
        self.start_btn = ctk.CTkButton(
            actions,
            text="Start",
            width=120,
            height=36,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color=GOLD,
            hover_color=GOLD_HOVER,
            text_color="#1a1406",
            command=self._start,
        )
        self.start_btn.pack(side="left")
        self.cancel_btn = ctk.CTkButton(
            actions,
            text="Cancel",
            width=100,
            height=36,
            fg_color=LINE,
            hover_color="#3a425c",
            state="disabled",
            command=self._cancel,
        )
        self.cancel_btn.pack(side="left", padx=(8, 0))
        ctk.CTkButton(
            actions,
            text="Open Folder",
            width=120,
            height=36,
            fg_color=LINE,
            hover_color="#3a425c",
            command=self._open_folder,
        ).pack(side="left", padx=(8, 0))

        self.scanned_var = ctk.StringVar(value="0")
        self.queued_var = ctk.StringVar(value="0")
        self.skipped_var = ctk.StringVar(value="0")
        self.saved_var = ctk.StringVar(value="0")
        counts = ctk.CTkFrame(actions, fg_color=BG)
        counts.pack(side="right")
        for caption, var in (
            ("Scanned", self.scanned_var),
            ("Queued", self.queued_var),
            ("Skipped", self.skipped_var),
            ("Saved", self.saved_var),
        ):
            pill = ctk.CTkFrame(counts, fg_color=CARD, corner_radius=12)
            pill.pack(side="left", padx=(8, 0))
            ctk.CTkLabel(pill, text=caption, text_color=MUTED, font=ctk.CTkFont(size=11)).pack(padx=12, pady=(6, 0))
            ctk.CTkLabel(pill, textvariable=var, text_color=INK, font=ctk.CTkFont(size=16, weight="bold")).pack(padx=12, pady=(0, 6))

        self.status_var = ctk.StringVar(value="Ready.")
        ctk.CTkLabel(
            self,
            textvariable=self.status_var,
            text_color=MUTED,
            anchor="w",
            font=ctk.CTkFont(size=13),
        ).grid(row=3, column=0, sticky="ew", padx=26, pady=(0, 6))

        body = ctk.CTkFrame(self, fg_color=BG)
        body.grid(row=4, column=0, sticky="nsew", padx=22, pady=(0, 16))
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(4, weight=1)

        now = ctk.CTkFrame(body, fg_color=CARD, corner_radius=16)
        now.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        now.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(now, text="Now downloading", text_color=GOLD, font=ctk.CTkFont(size=12, weight="bold")).grid(
            row=0, column=1, sticky="w", padx=8, pady=(12, 0)
        )
        self.now_thumb = ctk.CTkLabel(now, text="", width=168, height=94, fg_color=CARD_SOFT, corner_radius=10)
        self.now_thumb.grid(row=0, column=0, rowspan=4, padx=14, pady=14, sticky="n")
        self.now_title = ctk.CTkLabel(
            now,
            text="Nothing playing yet",
            text_color=INK,
            font=ctk.CTkFont(size=18, weight="bold"),
            anchor="w",
            justify="left",
            wraplength=640,
        )
        self.now_title.grid(row=1, column=1, sticky="ew", padx=8, pady=(4, 0))
        self.now_artist = ctk.CTkLabel(now, text="Songs you download show up here.", text_color=MUTED, anchor="w")
        self.now_artist.grid(row=2, column=1, sticky="ew", padx=8, pady=(2, 6))
        self.progress = ctk.CTkProgressBar(now, height=8, progress_color=GOLD, fg_color=LINE)
        self.progress.grid(row=3, column=1, sticky="ew", padx=8, pady=(0, 8))
        self.progress.set(0)
        buttons = ctk.CTkFrame(now, fg_color="transparent")
        buttons.grid(row=4, column=1, sticky="w", padx=8, pady=(0, 14))
        self.skip_btn = ctk.CTkButton(
            buttons, text="Skip this song", width=130, fg_color=LINE, hover_color="#3a425c", state="disabled", command=self._skip
        )
        self.skip_btn.pack(side="left")
        self.pause_btn = ctk.CTkButton(
            buttons, text="Pause", width=100, fg_color=LINE, hover_color="#3a425c", state="disabled", command=self._toggle_pause
        )
        self.pause_btn.pack(side="left", padx=(8, 0))

        ctk.CTkLabel(body, text="Up next", text_color=INK, font=ctk.CTkFont(size=14, weight="bold"), anchor="w").grid(
            row=1, column=0, sticky="w", pady=(4, 4)
        )
        self.upcoming_frame = ctk.CTkFrame(body, fg_color=BG, height=120)
        self.upcoming_frame.grid(row=2, column=0, sticky="ew")
        self.upcoming_frame.grid_propagate(False)

        saved_head = ctk.CTkLabel(body, text="Saved", text_color=INK, font=ctk.CTkFont(size=14, weight="bold"), anchor="w")
        saved_head.grid(row=3, column=0, sticky="w", pady=(8, 4))
        self.saved_frame = ctk.CTkScrollableFrame(body, fg_color=CARD, corner_radius=16, height=220)
        self.saved_frame.grid(row=4, column=0, sticky="nsew")
        self._saved_empty = ctk.CTkLabel(self.saved_frame, text="Finished songs show up here.", text_color=MUTED)
        self._saved_empty.pack(anchor="w", padx=8, pady=8)
        self._render_upcoming()

    def _choose_folder(self):
        chosen = filedialog.askdirectory(initialdir=self.output_dir_var.get())
        if chosen:
            self.output_dir_var.set(chosen)

    def _open_folder(self):
        reveal_path("", self.output_dir_var.get())

    def _set_running(self, running):
        self.running = running
        self.start_btn.configure(state="disabled" if running else "normal")
        self.cancel_btn.configure(state="normal" if running else "disabled")
        self.pause_btn.configure(state="normal" if running else "disabled", text="Pause")
        self.skip_btn.configure(state="disabled")
        self.browser_menu.configure(state="disabled" if running else "normal")
        self.depth_menu.configure(state="disabled" if running else "normal")

    def _start(self):
        if self.running:
            return
        self._set_running(True)
        self.cancel_event.clear()
        self.skip_current.clear()
        self.resume_event.set()
        self.scan_finished.clear()
        self.song_queue = queue.Queue()
        self.upcoming.clear()
        self.saved_count = 0
        self.already_count = 0
        self._active_item = None
        self._partials = []
        self._final_path = ""
        self.scanned_var.set("0")
        self.queued_var.set("0")
        self.skipped_var.set("0")
        self.saved_var.set("0")
        self.progress.set(0)
        self._show_idle_now("Looking through your history…", "Non-songs are skipped.")
        for child in self.saved_frame.winfo_children():
            child.destroy()
        self._saved_empty = None
        self._render_upcoming()
        self._browser = self.browser_menu.get()
        self._depth = self.depth_menu.get()
        self._output = self.output_dir_var.get()
        self.archive_path = os.path.join(self._output, "downloaded_archive.txt")
        self.archived_ids = load_archive_ids(self.archive_path)
        self.status_var.set("Reading the browser login…")
        self.update_idletasks()
        self._use_local_history = False
        try:
            self._cookie_file = prepare_cookie_file(self._browser)
        except Exception as exc:
            if sys.platform == "win32" and _chromium_user_data(self._browser):
                self._cookie_file = None
                self._use_local_history = True
                self.status_var.set("Chrome encrypts its login, so this uses the videos saved in Chrome's history.")
            else:
                self._set_running(False)
                self.status_var.set(f"Could not read the browser login: {exc}")
                return

        threading.Thread(target=self._scanner, daemon=True).start()
        threading.Thread(target=self._downloader, daemon=True).start()

    def _cancel(self):
        if not self.running:
            return
        self.cancel_event.set()
        self.resume_event.set()
        self.status_var.set("Stopping after this song…")

    def _skip(self):
        if self._active_item is None:
            return
        self.skip_current.set()
        self.status_var.set("Skipping this song…")

    def _toggle_pause(self):
        if not self.running:
            return
        if self.resume_event.is_set():
            self.resume_event.clear()
            self.pause_btn.configure(text="Resume")
            self.status_var.set("Paused. The current song will finish, then the queue waits.")
        else:
            self.resume_event.set()
            self.pause_btn.configure(text="Pause")
            self.status_var.set("Resumed.")

    def _scanner(self):
        depth = self._depth
        deno_bin = get_binary_path("deno")
        scan_opts = {
            "extract_flat": True,
            "quiet": True,
            "no_warnings": True,
            "js_runtimes": {"deno": {"path": deno_bin}},
        }
        if self._cookie_file:
            scan_opts["cookiefile"] = self._cookie_file
        if depth != "All":
            try:
                scan_opts["playlistend"] = int(depth)
            except ValueError:
                scan_opts["playlistend"] = 100
        meta_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "js_runtimes": {"deno": {"path": deno_bin}},
        }
        if self._cookie_file:
            meta_opts["cookiefile"] = self._cookie_file
        try:
            with yt_dlp.YoutubeDL(scan_opts) as scanner, yt_dlp.YoutubeDL(meta_opts) as meta:
                if self._use_local_history:
                    entries = local_history_entries(self._browser, depth)
                    if not entries:
                        self.ui_queue.put(("status", "No YouTube videos were found in Chrome's history."))
                else:
                    info = scanner.extract_info("https://www.youtube.com/feed/history", download=False, process=False)
                    entries = info.get("entries") if isinstance(info, dict) else None
                    if entries is None:
                        self.ui_queue.put(("status", "No history entries came back. Is the browser signed in?"))
                if entries:
                    for entry in entries:
                        if self.cancel_event.is_set():
                            break
                        if not entry:
                            continue
                        self.ui_queue.put(("scanned", None))
                        self._consider(entry, meta)
        except Exception as exc:
            self.ui_queue.put(("status", f"Could not read history: {exc}"))
        finally:
            self.scan_finished.set()

    def _consider(self, entry, meta):
        title = entry.get("title") or "Unknown"
        decision, reason = classify_flat(entry)
        merged = dict(entry)
        if decision == "maybe":
            url = video_url(entry)
            if not url:
                self.ui_queue.put(("skipped", f"Skipped {title} — no link"))
                return
            self.ui_queue.put(("status", f"Checking {title}"))
            try:
                full = meta.extract_info(url, download=False)
            except Exception:
                self.ui_queue.put(("skipped", f"Skipped {title} — could not check it"))
                return
            if not isinstance(full, dict):
                self.ui_queue.put(("skipped", f"Skipped {title} — could not check it"))
                return
            merged.update({k: v for k, v in full.items() if v})
            keep, reason = classify_full(merged)
            decision = "music" if keep else "junk"
        if decision != "music":
            self.ui_queue.put(("skipped", f"Skipped {title} — {reason}"))
            return

        item = song_item(merged)
        if not item["url"]:
            self.ui_queue.put(("skipped", f"Skipped {title} — no link"))
            return
        if item["id"] and item["id"] in self.archived_ids:
            self.ui_queue.put(("already", item))
            return
        self.ui_queue.put(("queue", item))
        self.song_queue.put(item)

    def _downloader(self):
        output_dir = self._output
        os.makedirs(output_dir, exist_ok=True)
        self.archive_path = os.path.join(output_dir, "downloaded_archive.txt")
        cancelled = False
        ydl = self._make_ydl(output_dir)
        try:
            while True:
                if self.cancel_event.is_set():
                    cancelled = True
                    break
                while not self.resume_event.is_set():
                    if self.cancel_event.is_set():
                        cancelled = True
                        break
                    self.resume_event.wait(0.2)
                if cancelled or self.cancel_event.is_set():
                    cancelled = True
                    break
                try:
                    item = self.song_queue.get(timeout=0.3)
                except queue.Empty:
                    if self.scan_finished.is_set():
                        break
                    continue
                if item.get("id") and item["id"] in self.archived_ids:
                    self.ui_queue.put(("already", item))
                    self.song_queue.task_done()
                    continue
                if self._download_one(ydl, item, output_dir):
                    ydl = self._make_ydl(output_dir)
                self.song_queue.task_done()
        finally:
            self.ui_queue.put(("done", {"saved": self.saved_count, "already": self.already_count, "cancelled": cancelled or self.cancel_event.is_set()}))

    def _make_ydl(self, output_dir):
        self._last_progress = 0.0

        def hook(status):
            filename = status.get("filename") or status.get("tmpfilename")
            if filename and filename not in self._partials:
                self._partials.append(filename)
            if status.get("status") == "downloading":
                now = time.time()
                total = status.get("total_bytes") or status.get("total_bytes_estimate") or 0
                done = status.get("downloaded_bytes") or 0
                if total and now - self._last_progress > 0.08:
                    self._last_progress = now
                    self.ui_queue.put(("progress", min(1.0, done / total)))
            if self.skip_current.is_set():
                raise DownloadCancelled("Skipped by user")

        def pp_hook(status):
            info = status.get("info_dict") or {}
            path = info.get("filepath") or ""
            if path.endswith(".mp3"):
                self._final_path = path

        options = {
            "ffmpeg_location": get_binary_path("ffmpeg"),
            "js_runtimes": {"deno": {"path": get_binary_path("deno")}},
            "format": "bestaudio/best",
            "postprocessors": [
                {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "0"},
                {"key": "FFmpegMetadata"},
                {"key": "EmbedThumbnail"},
            ],
            "download_archive": self.archive_path,
            "outtmpl": os.path.join(output_dir, "%(title)s.%(ext)s"),
            "writethumbnail": True,
            "quiet": True,
            "no_warnings": True,
            "progress_hooks": [hook],
            "postprocessor_hooks": [pp_hook],
        }
        if self._cookie_file:
            options["cookiefile"] = self._cookie_file
        return yt_dlp.YoutubeDL(options)

    def _download_one(self, ydl, item, output_dir):
        self.skip_current.clear()
        self._partials = []
        self._final_path = ""
        self._active_item = item
        self.ui_queue.put(("start", item))
        skipped = False
        failed = ""
        try:
            ydl.download([item["url"]])
            skipped = self.skip_current.is_set()
        except DownloadCancelled:
            skipped = True
        except Exception as exc:
            if self.skip_current.is_set():
                skipped = True
            else:
                failed = str(exc).splitlines()[0][:160]
        self._active_item = None
        if skipped:
            self._cleanup_partials(item.get("id"))
            self.ui_queue.put(("user_skip", item))
            return True
        if failed:
            self._cleanup_partials(None)
            self.ui_queue.put(("failed", {"title": item["title"], "error": failed}))
            return True
        path = self._final_path
        if not path or not os.path.isfile(path):
            path = self._guess_mp3(output_dir, item["title"])
        if not path:
            self.ui_queue.put(("already", item))
            return False
        item = dict(item)
        item["path"] = path
        if item.get("id"):
            self.archived_ids.add(item["id"])
        self.saved_count += 1
        self.ui_queue.put(("saved", item))
        return False

    def _guess_mp3(self, folder, title):
        direct = os.path.join(folder, f"{title}.mp3")
        if os.path.isfile(direct):
            return direct
        return ""

    def _cleanup_partials(self, video_id):
        for path in list(self._partials):
            for candidate in {path, path + ".part"}:
                if candidate and os.path.isfile(candidate):
                    try:
                        os.remove(candidate)
                    except OSError:
                        pass
        if self._final_path and os.path.isfile(self._final_path):
            try:
                os.remove(self._final_path)
            except OSError:
                pass
        if video_id:
            self.archived_ids.discard(video_id)
            remove_archive_id(self.archive_path, video_id)
        self._partials = []
        self._final_path = ""

    def _poll_ui(self):
        processed = 0
        try:
            while processed < 40:
                kind, payload = self.ui_queue.get_nowait()
                self._on_ui(kind, payload)
                processed += 1
        except queue.Empty:
            pass
        if self._upcoming_dirty:
            self._upcoming_dirty = False
            self._render_upcoming()
        self.after(60, self._poll_ui)

    def _on_ui(self, kind, payload):
        if kind == "scanned":
            self.scanned_var.set(str(int(self.scanned_var.get()) + 1))
        elif kind == "skipped":
            self.skipped_var.set(str(int(self.skipped_var.get()) + 1))
            self.status_var.set(payload)
        elif kind == "status":
            self.status_var.set(payload)
        elif kind == "queue":
            self.upcoming.append(payload)
            self.queued_var.set(str(len(self.upcoming)))
            self._upcoming_dirty = True
        elif kind == "already":
            self._drop_upcoming(payload.get("id"))
            self.already_count += 1
            self._add_saved_card(payload, already=True)
            self.status_var.set(f"Already saved: {payload.get('title', '')}")
        elif kind == "start":
            self._drop_upcoming(payload.get("id"))
            self._show_now(payload)
            self.skip_btn.configure(state="normal")
            self.progress.set(0)
            self.status_var.set(f"Downloading {payload.get('title', '')}")
            self._request_thumb(payload, "now", (168, 94))
        elif kind == "progress":
            self.progress.set(payload)
        elif kind == "saved":
            self.saved_var.set(str(self.saved_count))
            self.progress.set(1)
            self.skip_btn.configure(state="disabled")
            self._add_saved_card(payload, already=False)
            self.status_var.set(f"Saved {payload.get('title', '')}")
        elif kind == "user_skip":
            self._show_idle_now("Skipped", payload.get("title", ""))
            self.skip_btn.configure(state="disabled")
            self.progress.set(0)
            self.status_var.set(f"Skipped {payload.get('title', '')}")
        elif kind == "failed":
            self._show_idle_now("Could not save this one", payload.get("title", ""))
            self.skip_btn.configure(state="disabled")
            self.progress.set(0)
            self.status_var.set(f"Could not save {payload.get('title', '')}. {payload.get('error', '')}")
        elif kind == "thumb":
            self._apply_thumb(payload)
        elif kind == "done":
            self._finish(payload)

    def _drop_upcoming(self, video_id):
        if not video_id:
            return
        before = len(self.upcoming)
        self.upcoming = [item for item in self.upcoming if item.get("id") != video_id]
        if len(self.upcoming) != before:
            self.queued_var.set(str(len(self.upcoming)))
            self._upcoming_dirty = True

    def _show_idle_now(self, title, subtitle):
        self._now_id = ""
        self.now_title.configure(text=title)
        self.now_artist.configure(text=subtitle)
        self.now_thumb.configure(image=None, text="")
        self.progress.set(0)

    def _show_now(self, item):
        self._now_id = item.get("id") or ""
        artist = item.get("artist") or item.get("uploader") or "YouTube"
        self.now_title.configure(text=item.get("title") or "Unknown")
        self.now_artist.configure(text=artist)
        self.now_thumb.configure(image=None, text="")

    def _render_upcoming(self):
        self._render_gen += 1
        generation = self._render_gen
        for child in self.upcoming_frame.winfo_children():
            child.destroy()
        self._mini_labels = {}
        if not self.upcoming:
            ctk.CTkLabel(self.upcoming_frame, text="Queued songs show up here.", text_color=MUTED).pack(anchor="w", pady=8)
            return
        for item in self.upcoming[:6]:
            card = ctk.CTkFrame(self.upcoming_frame, fg_color=CARD, corner_radius=12, width=150, height=108)
            card.pack(side="left", padx=(0, 8), pady=4)
            card.pack_propagate(False)
            thumb = ctk.CTkLabel(card, text="", fg_color=CARD_SOFT, width=130, height=64, corner_radius=8)
            thumb.pack(padx=10, pady=(8, 2))
            ctk.CTkLabel(
                card,
                text=item.get("title") or "Song",
                text_color=INK,
                font=ctk.CTkFont(size=11),
                wraplength=130,
                anchor="w",
                justify="left",
            ).pack(fill="x", padx=10)
            self._mini_labels[item.get("id")] = thumb
            self._request_thumb(item, "next", (130, 64), generation)
        extra = len(self.upcoming) - 6
        if extra > 0:
            ctk.CTkLabel(self.upcoming_frame, text=f"+{extra} more", text_color=MUTED).pack(side="left", padx=8)

    def _add_saved_card(self, item, already):
        if self._saved_empty is not None:
            self._saved_empty.destroy()
            self._saved_empty = None
        color = CARD_SOFT if already else "#22283a"
        title_color = DIM if already else INK
        card = ctk.CTkFrame(self.saved_frame, fg_color=color, corner_radius=12)
        card.pack(fill="x", padx=6, pady=4)
        thumb = ctk.CTkLabel(card, text="", fg_color=BG, width=72, height=40, corner_radius=8)
        thumb.pack(side="left", padx=10, pady=8)
        text = ctk.CTkFrame(card, fg_color="transparent")
        text.pack(side="left", fill="x", expand=True, pady=8)
        ctk.CTkLabel(
            text,
            text=item.get("title") or "Song",
            text_color=title_color,
            anchor="w",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="w")
        caption = "Already saved" if already else (item.get("artist") or item.get("uploader") or "Saved")
        ctk.CTkLabel(text, text=caption, text_color=GREEN if not already else DIM, anchor="w").pack(anchor="w")
        path = item.get("path") or ""
        folder = self.output_dir_var.get()

        def open_card(_event=None, path=path, folder=folder):
            reveal_path(path, folder)

        def bind_click(widget):
            widget.bind("<Button-1>", open_card)
            for child in widget.winfo_children():
                bind_click(child)

        bind_click(card)
        item = dict(item)
        item["_thumb"] = thumb
        self._request_thumb(item, "saved", (72, 40))

    def _request_thumb(self, item, slot, size, generation=None):
        url = item.get("thumbnail") or ""
        if not url:
            return
        video_id = item.get("id") or ""
        target = item.get("_thumb")

        def run():
            data = self._fetch_bytes(url)
            if not data:
                return
            self.ui_queue.put(("thumb", {
                "data": data,
                "slot": slot,
                "id": video_id,
                "size": size,
                "generation": generation,
                "widget": target,
            }))

        threading.Thread(target=run, daemon=True).start()

    def _fetch_bytes(self, url):
        with self._thumb_lock:
            cached = self._thumb_bytes.get(url)
        if cached is not None:
            return cached
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(request, timeout=12) as response:
                data = response.read()
        except Exception:
            return None
        with self._thumb_lock:
            self._thumb_bytes[url] = data
        return data

    def _apply_thumb(self, payload):
        if payload.get("slot") == "next" and payload.get("generation") != self._render_gen:
            return
        try:
            image = Image.open(BytesIO(payload["data"])).convert("RGB")
            photo = ctk.CTkImage(light_image=image, dark_image=image, size=payload["size"])
        except Exception:
            return
        if payload.get("slot") == "now":
            if payload.get("id") != getattr(self, "_now_id", None):
                return
            widget = self.now_thumb
        elif payload.get("slot") == "next":
            widget = self._mini_labels.get(payload.get("id"))
        else:
            widget = payload.get("widget")
        if widget is None:
            return
        try:
            widget.configure(image=photo, text="")
            widget._photo = photo
        except Exception:
            pass

    def _finish(self, payload):
        saved = payload.get("saved", 0)
        already = payload.get("already", 0)
        cancelled = payload.get("cancelled", False)
        self._set_running(False)
        self.upcoming.clear()
        self.queued_var.set("0")
        self._render_upcoming()
        self.skip_btn.configure(state="disabled")
        if self.now_title.cget("text") in ("Looking through your history…",):
            self._show_idle_now("Nothing playing yet", "Songs you download show up here.")
        if cancelled:
            message = f"Stopped. Saved {saved}. {already} already in the folder."
        else:
            message = f"Done. Saved {saved}. {already} already in the folder."
            self.progress.set(1 if saved else 0)
        self.status_var.set(message)
        notify("YouTube History Songs", message)


if __name__ == "__main__":
    app = YTHistoryMusicApp()
    app.mainloop()
