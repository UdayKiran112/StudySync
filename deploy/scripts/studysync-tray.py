"""
studysync-tray.py
-----------------
Windows system-tray monitor for StudySync (like the Bluetooth/McAfee icons).

Runs persistently in the notification area. The icon color reflects overall
health (green = all services running, amber = starting/pending, red = a
service stopped). Clicking the icon opens a small status window that lists
every backend service with its live state and a Restart button for each; the
tray menu also has "Restart All Stopped".

Performance design
------------------
The tray only repaints when something actually changes. Service states are
polled in *parallel* worker threads (one per service), so one hung `sc query`
can never stall the others or block the tray loop. The icon image and the
right-click menu are cached and only re-assigned when their content changes
(pystray's per-assignment Shell_NotifyIcon/menu rebuild is the expensive
part), the font is loaded once, and opening the status window requests an
immediate refresh so it never shows stale data.

Started at logon by the StudySyncTray scheduled task (elevated, so it can
restart services from the tray without a UAC prompt). Built windowless by
PyInstaller (--noconsole), so it never flashes a console window.

    python deploy\\scripts\\studysync-tray.py
"""

import ctypes
import queue
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, wait
from datetime import datetime
from pathlib import Path

import tkinter as tk
from PIL import Image, ImageDraw, ImageFont

from pystray import Icon, Menu, MenuItem

# (Windows service name, friendly name, restartable)
SERVICES = [
    ("StudySyncAPI", "StudySync API", True),
    ("StudySyncCaddy", "StudySync Web Server (Caddy)", True),
]

APP_DIR = Path(
    __import__("os").environ.get("STUDYSYNC_APP_DIR", r"C:\ProgramData\StudySync")
)
LOG_DIR = APP_DIR / "logs" / "tray"
LOG_FILE = LOG_DIR / "tray.log"

# Suppress the console window of sc.exe (this process itself is windowless).
CREATE_NO_WINDOW = 0x08000000

_OK = (46, 160, 67)
_BAD = (220, 60, 60)
_WARN = (230, 160, 40)
_ACCENT = (34, 120, 220)

cmd_queue: "queue.Queue[str]" = queue.Queue()

# Render the tray image at the real tray size instead of 64px: it is both
# cheaper and crisper on high-DPI displays (Windows renders it at 16/24/32).
_ICON_SIZE = 32

_FONT_CACHE: dict[int, object] = {}
_ICON_CACHE: dict[tuple, Image.Image] = {}


# ---------------------------------------------------------------- logging
def _log(msg: str) -> None:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        line = f"{datetime.now().isoformat(timespec='seconds')} | {msg}"
        print(line, flush=True)
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:  # noqa: BLE001 - logging must never crash the tray
        pass


# ------------------------------------------------------------ services
def run_cmd(args, timeout: int = 8):
    try:
        return subprocess.run(
            args,
            capture_output=True,
            text=True,
            creationflags=CREATE_NO_WINDOW,
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001
        _log(f"command failed: {' '.join(args)} -> {exc}")
        return None


def service_state(name: str) -> str:
    """Running/Stopped/STOP_PENDING/... via `sc query` (visible to any user).

    Fast and dependency-free. Runs in a worker thread so a slow query never
    blocks the tray; a hung one is cut off by run_cmd's timeout and the next
    tick simply re-queries.
    """
    out = run_cmd(["sc", "query", name])
    if out is None:
        return "UNKNOWN"
    if out.returncode != 0:
        return "NOT FOUND"
    for line in out.stdout.splitlines():
        if "STATE" in line:
            tokens = line.split(":", 1)[1].strip().split()
            return tokens[1] if len(tokens) > 1 else tokens[0]
    return "UNKNOWN"


def restart_service(name: str) -> None:
    _log(f"Restart requested for {name}")
    run_cmd(["sc", "stop", name])
    time.sleep(2)
    run_cmd(["sc", "start", name])


def _display_state(raw: str) -> str:
    mapping = {
        "RUNNING": "Running",
        "STOPPED": "Stopped",
        "STOP_PENDING": "Stopping\u2026",
        "START_PENDING": "Starting\u2026",
        "PAUSED": "Paused",
        "NOT FOUND": "Not installed",
        "UNKNOWN": "Unknown",
    }
    return mapping.get(raw, raw.title())


# --------------------------------------------------------- mDNS probe
def resolve_mdns_name() -> tuple[bool, str]:
    """Return (ok, detail) for whether studysync.local currently resolves.

    This is the user-facing question the whole advertisement feature answers:
    can devices (and this PC) reach the app by name right now? Runs in its own
    background thread so the (occasionally blocking) lookup never stalls the
    monitor loop or the tray.
    """
    try:
        infos = socket.getaddrinfo("studysync.local", 80, socket.AF_INET)
        addrs = sorted({i[4][0] for i in infos})
        if addrs:
            return True, "-> " + ", ".join(addrs)
        return False, "no address"
    except OSError as exc:
        return False, str(exc)


def _state_color(raw: str) -> str:
    if raw == "RUNNING":
        return "#2ea043"
    if raw in ("STOPPED", "NOT FOUND"):
        return "#dc3c3c"
    if raw in ("START_PENDING", "STOP_PENDING"):
        return "#e6a028"
    return "#8a8a8a"


# ---------------------------------------------------------------- icon
def _font(size: int):
    if size in _FONT_CACHE:
        return _FONT_CACHE[size]
    for path in (
        r"C:\Windows\Fonts\segoeuib.ttf",
        r"C:\Windows\Fonts\arialbd.ttf",
    ):
        try:
            f = ImageFont.truetype(path, size)
            _FONT_CACHE[size] = f
            return f
        except Exception:  # noqa: BLE001
            continue
    try:
        f = ImageFont.load_default()
    except Exception:  # noqa: BLE001
        f = None
    _FONT_CACHE[size] = f
    return f


def _render_icon(color) -> Image.Image:
    """Draw the "S" badge once per color; results are cached by color."""
    img = Image.new("RGBA", (_ICON_SIZE, _ICON_SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(
        [1, 1, _ICON_SIZE - 2, _ICON_SIZE - 2],
        radius=max(4, _ICON_SIZE // 4),
        fill=color + (255,),
    )
    f = _font(int(_ICON_SIZE * 0.6))
    if f is not None:
        d.text(
            (_ICON_SIZE // 2, _ICON_SIZE // 2),
            "S",
            fill=(255, 255, 255, 255),
            anchor="mm",
            font=f,
        )
    return img


def make_icon(color=_ACCENT) -> Image.Image:
    """Backward-compatible entry point (main() uses it for the initial icon)."""
    icon = _ICON_CACHE.get(color)
    if icon is None:
        icon = _render_icon(color)
        _ICON_CACHE[color] = icon
    return icon


# --------------------------------------------------------------- monitor
class Monitor:
    """Background thread that polls service states and updates the tray.

    Only re-assigns ``icon.icon`` / ``icon.menu`` when their content actually
    changed, so the expensive pystray Windows redraws happen on state changes
    rather than every poll tick.
    """

    def __init__(self, icon: Icon):
        self.icon = icon
        self.states: dict[str, str] = {name: "UNKNOWN" for name, _, _ in SERVICES}
        self.last_checked = ""
        self.mdns_ok = False
        self.mdns_detail = "checking\u2026"
        self.mdns_checked = ""
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self._wake = threading.Event()  # set to trigger an immediate poll
        self._executor = ThreadPoolExecutor(max_workers=len(SERVICES), thread_name_prefix="tray-sc")

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "states": dict(self.states),
                "last_checked": self.last_checked,
                "mdns_ok": self.mdns_ok,
                "mdns_detail": self.mdns_detail,
                "mdns_checked": self.mdns_checked,
            }

    def overall_color(self):
        snap = self.snapshot()["states"]
        vals = list(snap.values())
        if not vals:
            return _WARN
        if all(v == "RUNNING" for v in vals):
            return _OK
        if any(v in ("STOPPED", "NOT FOUND") for v in vals):
            return _BAD
        return _WARN

    def menu_fingerprint(self) -> str:
        snap = self.snapshot()
        return repr(sorted(snap["states"].items())) + "|" + str(snap["mdns_ok"]) + snap["mdns_detail"]

    def request_refresh(self) -> None:
        """Wake the monitor loop immediately (e.g. when the window is opened)."""
        self._wake.set()

    def _probe_mdns_async(self) -> None:
        def worker() -> None:
            ok, detail = resolve_mdns_name()
            with self.lock:
                self.mdns_ok = ok
                self.mdns_detail = detail
                self.mdns_checked = datetime.now().strftime("%H:%M:%S")

        threading.Thread(target=worker, daemon=True, name="tray-mdns").start()

    def run(self) -> None:
        _log("StudySync tray monitor started")
        inflight: dict[str, Future] = {}
        last_icon_color = None
        last_menu_fp = None
        mdns_due = time.monotonic()
        self._wake.set()  # poll immediately on startup
        while not self.stop.is_set():
            # 1) Poll services in parallel. A name with a query still in
            #    flight is left alone (its own 8s sc timeout bounds it), so a
            #    slow query can never stall the loop or pile up threads.
            for name, _, _ in SERVICES:
                if name not in inflight:
                    inflight[name] = self._executor.submit(service_state, name)
            if inflight:
                done, _ = wait(list(inflight.values()), timeout=1.5)
                for fut in done:
                    name = next(n for n, f in inflight.items() if f is fut)
                    inflight.pop(name)
                    try:
                        raw = fut.result()
                    except Exception as exc:  # noqa: BLE001
                        _log(f"service poll {name} failed: {exc}")
                        raw = "UNKNOWN"
                    with self.lock:
                        self.states[name] = raw
                        self.last_checked = datetime.now().strftime("%H:%M:%S")

            # 2) mDNS probe every 60 s, off the loop so a slow lookup cannot
            #    hold up the service polling cadence.
            if time.monotonic() >= mdns_due:
                mdns_due = time.monotonic() + 60
                self._probe_mdns_async()

            # 3) Repaint only when something actually changed.
            color = self.overall_color()
            if color != last_icon_color:
                last_icon_color = color
                try:
                    self.icon.icon = make_icon(color)
                except Exception:  # noqa: BLE001
                    pass
            fp = self.menu_fingerprint()
            if fp != last_menu_fp:
                last_menu_fp = fp
                try:
                    self.icon.menu = build_menu(self)
                except Exception:  # noqa: BLE001
                    pass

            # 4) Sleep up to 5 s, waking early when a refresh is requested.
            self._wake.wait(5)
            self._wake.clear()
        self._executor.shutdown(wait=False, cancel_futures=True)
        _log("StudySync tray monitor stopped")


# -------------------------------------------------------------- actions
def show_status_window():
    cmd_queue.put("show")


def quit_app():
    cmd_queue.put("quit")


def restart_all(monitor: Monitor):
    threading.Thread(target=_restart_all_worker, args=(monitor,), daemon=True).start()


def _restart_all_worker(monitor: Monitor) -> None:
    snap = monitor.snapshot()["states"]
    names = [name for name, _, _ in SERVICES if snap.get(name) != "RUNNING"]
    # Stop everything first, then start, so a down API doesn't come up as a
    # broken Caddy dependency. Runs in a worker thread, never on the UI.
    for name in names:
        run_cmd(["sc", "stop", name])
    time.sleep(1)
    for name in names:
        run_cmd(["sc", "start", name])
    monitor.request_refresh()


def build_menu(monitor: Monitor) -> Menu:
    snap = monitor.snapshot()
    states = snap["states"]
    items = [
        MenuItem("Open Status", lambda icon, item: show_status_window(), default=True),
        MenuItem("Refresh Now", lambda icon, item: monitor.request_refresh()),
        Menu.SEPARATOR,
    ]
    for name, display, _restartable in SERVICES:
        items.append(
            MenuItem(f"{display}: {_display_state(states.get(name, 'UNKNOWN'))}", None, enabled=False)
        )
    items.append(Menu.SEPARATOR)
    mdns_label = "mDNS studysync.local: " + (
        f"OK {snap['mdns_detail']}" if snap["mdns_ok"] else f"MISSING ({snap['mdns_detail']})"
    )
    items.append(MenuItem(mdns_label, None, enabled=False))
    items.append(Menu.SEPARATOR)
    items.append(MenuItem("Restart All Stopped", lambda icon, item: restart_all(monitor)))
    items.append(MenuItem("Exit", lambda icon, item: quit_app()))
    return Menu(*items)


# ----------------------------------------------------------- status window
class StatusWindow:
    def __init__(self, monitor: Monitor):
        self.monitor = monitor
        self._visible = False
        self.root = tk.Tk()
        self.root.title("StudySync - Service Status")
        self.root.resizable(False, False)
        self.root.withdraw()  # hidden until the user clicks the tray icon
        self.root.protocol("WM_DELETE_WINDOW", self.hide)
        self._rows = []
        self._mdns_state_lbl = None
        self._mdns_detail_lbl = None
        self._build()
        self.root.after(100, self._poll_queue)
        self.root.after(5000, self._auto_refresh)

    def _build(self) -> None:
        header = tk.Label(self.root, text="Backend services", font=("Segoe UI", 10, "bold"))
        header.grid(row=0, column=0, columnspan=3, sticky="w", padx=12, pady=(10, 2))

        for i, (name, display, restartable) in enumerate(SERVICES, start=1):
            name_lbl = tk.Label(self.root, text=display, font=("Segoe UI", 9), anchor="w")
            name_lbl.grid(row=i, column=0, sticky="w", padx=(12, 6), pady=3)

            state_lbl = tk.Label(self.root, text="", font=("Segoe UI", 9, "bold"), width=12, anchor="w")
            state_lbl.grid(row=i, column=1, sticky="w", padx=6, pady=3)

            btn = tk.Button(self.root, text="Restart", font=("Segoe UI", 8), width=8)
            btn.configure(
                command=lambda n=name: threading.Thread(
                    target=self._restart_one, args=(n,), daemon=True
                ).start()
            )
            btn.grid(row=i, column=2, sticky="e", padx=(6, 12), pady=3)
            self._rows.append((name, state_lbl, btn))

        mdns_row = len(SERVICES) + 1
        mdns_lbl = tk.Label(self.root, text="mDNS (studysync.local)", font=("Segoe UI", 9), anchor="w")
        mdns_lbl.grid(row=mdns_row, column=0, sticky="w", padx=(12, 6), pady=3)

        self._mdns_state_lbl = tk.Label(
            self.root, text="", font=("Segoe UI", 9, "bold"), width=12, anchor="w"
        )
        self._mdns_state_lbl.grid(row=mdns_row, column=1, sticky="w", padx=6, pady=3)

        self._mdns_detail_lbl = tk.Label(
            self.root, text="", font=("Segoe UI", 8), fg="#666666", anchor="w"
        )
        self._mdns_detail_lbl.grid(row=mdns_row, column=2, sticky="w", padx=(6, 12), pady=3)

        self._last = tk.Label(self.root, text="", font=("Segoe UI", 8), fg="#666666")
        self._last.grid(row=len(SERVICES) + 2, column=0, columnspan=3, sticky="w", padx=12, pady=(6, 0))

        footer = tk.Frame(self.root)
        footer.grid(row=len(SERVICES) + 3, column=0, columnspan=3, sticky="ew", padx=12, pady=(8, 12))
        tk.Button(footer, text="Refresh", font=("Segoe UI", 9), command=self.refresh).pack(side="left")
        tk.Button(footer, text="Restart All Stopped", font=("Segoe UI", 9),
                  command=lambda: restart_all(self.monitor)).pack(side="left", padx=6)
        tk.Button(footer, text="Close", font=("Segoe UI", 9), command=self.hide).pack(side="right")

    def _restart_one(self, name: str) -> None:
        restart_service(name)
        self.monitor.request_refresh()
        cmd_queue.put("refresh")  # applied on the Tk thread in _poll_queue

    def show(self) -> None:
        self._visible = True
        self.monitor.request_refresh()  # get fresh data before rendering
        self.refresh()
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(50, lambda: self.root.attributes("-topmost", False))

    def hide(self) -> None:
        self._visible = False
        self.root.withdraw()

    def refresh(self) -> None:
        snap = self.monitor.snapshot()
        for name, state_lbl, _btn in self._rows:
            raw = snap["states"].get(name, "UNKNOWN")
            state_lbl.config(text=_display_state(raw), fg=_state_color(raw))
        if self._mdns_state_lbl is not None:
            self._mdns_state_lbl.config(
                text="OK" if snap["mdns_ok"] else "MISSING",
                fg="#2ea043" if snap["mdns_ok"] else "#dc3c3c",
            )
        if self._mdns_detail_lbl is not None:
            self._mdns_detail_lbl.config(
                text=f"{snap['mdns_detail']}  ({snap['mdns_checked']})" if snap["mdns_checked"] else snap["mdns_detail"]
            )
        self._last.config(text=f"Last checked: {snap['last_checked']}")

    def _poll_queue(self) -> None:
        try:
            while True:
                cmd = cmd_queue.get_nowait()
                if cmd == "show":
                    self.show()
                elif cmd == "quit":
                    self.quit()
                elif cmd == "refresh":
                    self.refresh()
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    def _auto_refresh(self) -> None:
        if self._visible:
            self.refresh()
        self.root.after(5000, self._auto_refresh)

    def quit(self) -> None:
        self.monitor.stop.set()
        self.root.destroy()


# ------------------------------------------------------------ bootstrap
def _already_running() -> bool:
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW(None, False, "Global\\StudySyncTray")
        return kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS
    except Exception:  # noqa: BLE001
        return False


def _is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        return False


def main() -> int:
    if _already_running():
        _log("Another tray instance is already running - exiting")
        return 0
    if not _is_admin():
        _log("WARNING: not elevated - status works, but service restarts need admin")

    icon = Icon("StudySync", make_icon(), "StudySync - click for service status")
    monitor = Monitor(icon)
    threading.Thread(target=monitor.run, daemon=True, name="tray-monitor").start()
    window = StatusWindow(monitor)
    threading.Thread(target=icon.run, daemon=True, name="tray-icon").start()
    window.root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
