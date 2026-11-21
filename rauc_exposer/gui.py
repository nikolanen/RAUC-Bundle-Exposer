"""Tk desktop application (ttkbootstrap). All server logic lives in core.py."""

import ctypes
import queue
import re
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox

import ttkbootstrap as tb

from . import APP_NAME, COPYRIGHT, __version__
from .core import (
    DEFAULT_PORT,
    GUI_LICENSE_PACKAGES,
    BundleServer,
    ClientMonitor,
    ClientStats,
    bundle_urls,
    check_bundle_file,
    explain_server_error,
    fmt_size,
    fmt_speed,
    inspect_bundle,
    license_texts,
    network_interfaces,
    parse_port,
    resource_path,
    sha256_file,
    text_bar,
)

MAX_LOG_LINES = 3000
WINDOW_ICON_SIZES = (16, 32, 48, 256)  # assets/app_<size>.png, made by assets/make_icon.py

COLOR_OK = "#16a34a"
COLOR_WARN = "#d97706"
COLOR_INFO = "#60a5fa"
COLOR_ERROR = "#dc2626"
COLOR_IDLE = "#9ca3af"

THEME = "bootstrap-dark"
ALL_INTERFACES_LABEL = "All interfaces (0.0.0.0)"
LOCALHOST_LABEL = "127.0.0.1  (localhost)"


# --------------------------------------------------------------------------- platform helpers


def set_titlebar_dark(win: tk.Misc):
    """Dark native title bar on Windows 10 (1809+) and 11."""
    if sys.platform != "win32":
        return
    try:
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        value = ctypes.c_int(1)
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (new / old id)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                break
    except Exception:
        pass



def enable_dpi_awareness():
    if sys.platform == "win32":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass


# --------------------------------------------------------------------------- widgets


class DarkMenu:
    """Popup menu built from Tk widgets.

    Native Windows menus always get a light system border, which looks out of place
    in a dark window. Index numbers (for set_state) count separators, like tk.Menu.

    Closing does not depend on the window manager giving the popup keyboard focus
    (many Linux window managers do not focus borderless windows):
      - a mouse grab routes clicks to the menu; clicks outside it close it
        (on X11 a global grab, so this includes clicks into other applications)
      - Escape works whether the menu or the main window has the focus
      - moving / resizing the main window closes it
    """

    def __init__(self, root: tk.Misc, colors, px):
        self.root, self.c, self.px = root, colors, px
        self.items = []
        self.win = None
        self._root_bindings = []

    def add_command(self, label, command, accelerator=""):
        self.items.append({"label": label, "accel": accelerator, "command": command, "state": "normal"})

    def add_separator(self):
        self.items.append(None)

    def set_state(self, index: int, state: str):
        self.items[index]["state"] = state

    def hide(self):
        for sequence, funcid in self._root_bindings:
            try:
                self.root.unbind(sequence, funcid)
            except tk.TclError:
                pass
        self._root_bindings = []
        if self.win is not None:
            win, self.win = self.win, None
            try:
                win.grab_release()
                win.destroy()
            except tk.TclError:
                pass

    def _bind_root(self, sequence, handler):
        self._root_bindings.append((sequence, self.root.bind(sequence, handler, add="+")))

    def _on_click(self, event):
        """Every click in the app arrives here while the menu has the grab."""
        win = self.win
        if win is None or not win.winfo_exists():
            return
        x0, y0 = win.winfo_rootx(), win.winfo_rooty()
        inside = x0 <= event.x_root < x0 + win.winfo_width() and y0 <= event.y_root < y0 + win.winfo_height()
        if not inside:
            self.hide()

    def _on_root_focus_out(self, _event):
        # Only close when the focus left the application (focus_get() is None then).
        # Focus moving into the menu itself (Windows) must not close it.
        self.root.after(50, lambda: self.hide() if self.win is not None and self.root.focus_get() is None else None)

    def _on_root_configure(self, event):
        if event.widget is self.root:  # main window moved or resized
            self.hide()

    def _run(self, command):
        self.hide()
        self.root.after_idle(command)

    def show(self, x: int, y: int):
        self.hide()
        c, px = self.c, self.px
        win = self.win = tk.Toplevel(self.root)
        if win.tk.call("tk", "windowingsystem") == "x11":
            try:  # tells X11 window managers / compositors that this is a drop-down menu
                win.attributes("-type", "dropdown_menu")
            except tk.TclError:
                pass
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        outer = tk.Frame(win, bg=c.inputbg, highlightthickness=1, highlightbackground=c.border,
                         highlightcolor=c.border, pady=px(4))
        outer.pack(fill="both", expand=True)
        tk.Frame(outer, bg=c.inputbg, width=px(190), height=0).pack()  # minimum width

        for item in self.items:
            if item is None:
                tk.Frame(outer, bg=c.border, height=1).pack(fill="x", padx=px(8), pady=px(4))
                continue
            enabled = item["state"] == "normal"
            row = tk.Frame(outer, bg=c.inputbg)
            row.pack(fill="x")
            label = tk.Label(row, text=item["label"], bg=c.inputbg, fg=c.fg if enabled else c.secondary,
                             anchor="w", padx=px(14), pady=px(4))
            label.pack(side="left")
            accel = tk.Label(row, text=item["accel"], bg=c.inputbg, fg=c.secondary, padx=px(14), pady=px(4))
            accel.pack(side="right")
            if not enabled:
                continue

            def hover(on, row=row, label=label, accel=accel):
                bg = c.primary if on else c.inputbg
                for w in (row, label, accel):
                    w.configure(bg=bg)
                label.configure(fg="#ffffff" if on else c.fg)
                accel.configure(fg="#ffffff" if on else c.secondary)

            for w in (row, label, accel):
                w.bind("<Enter>", lambda e, h=hover: h(True))
                w.bind("<Leave>", lambda e, h=hover: h(False))
                w.bind("<Button-1>", lambda e, cmd=item["command"]: self._run(cmd))

        win.geometry(f"+{x}+{y}")
        win.bind("<Button-1>", self._on_click, add="+")
        win.bind("<Escape>", lambda e: self.hide())
        # Windows: the popup gets the focus, so losing it means "clicked another app"
        win.bind("<FocusOut>", lambda e: self.root.after(
            50, lambda: self.hide() if self.win is win and self.root.focus_get() is None else None))
        self._bind_root("<Escape>", lambda e: self.hide())
        self._bind_root("<FocusOut>", self._on_root_focus_out)
        self._bind_root("<Configure>", self._on_root_configure)

        win.update_idletasks()
        self._grab(win)
        win.focus_force()

    @staticmethod
    def _grab(win):
        """Route clicks to the menu so that a click outside of it closes it.

        X11: global grab, like native menus there - a click into *another application*
        also reaches the menu (focus events are not reliable while a grab is active).
        Windows: local grab; switching applications is detected via FocusOut, because
        Windows always gives the popup the focus.
        """
        if win.tk.call("tk", "windowingsystem") == "x11":
            try:
                win.grab_set_global()
                return
            except tk.TclError:
                pass  # another application holds a grab -> fall back to a local grab
        try:
            win.grab_set()
        except tk.TclError:
            pass  # window not viewable yet; the other close paths still work


class App(tb.Window):
    def __init__(self):
        super().__init__(title=APP_NAME, theme=THEME, iconphoto=None)
        self.withdraw()  # stay hidden until the dark title bar is applied (avoids a white flash)
        self.scale = self.winfo_fpixels("1i") / 96
        self.title(APP_NAME)
        self.geometry(f"{self.px(940)}x{self.px(680)}")
        self.minsize(self.px(780), self.px(560))
        # Window icon from PNGs in several sizes: works on Windows and Linux, and Tk
        # picks the best size for title bar / taskbar. True = also for all dialogs.
        self.icon_images = []
        for size in WINDOW_ICON_SIZES:
            try:
                self.icon_images.append(tk.PhotoImage(file=str(resource_path(f"app_{size}.png"))))
            except tk.TclError:
                pass
        if self.icon_images:
            self.iconphoto(True, *self.icon_images)
        try:
            self.about_img = tk.PhotoImage(file=str(resource_path("app_96.png")))
        except tk.TclError:
            self.about_img = None

        self.ui_queue = queue.Queue()
        self.stats = ClientStats()
        self.monitor = ClientMonitor(self.stats)
        self.server = BundleServer(self.log, self.stats)
        self.bundle_info = None
        self.info_key = None
        self.hash_token = 0
        self.statusbar_job = None
        self.poll_count = 0

        self.bundle_var = tk.StringVar()
        self.host_var = tk.StringVar(value=ALL_INTERFACES_LABEL)
        self.host_choices = {ALL_INTERFACES_LABEL: "0.0.0.0"}
        self.port_var = tk.StringVar(value=str(DEFAULT_PORT))
        self.url_var = tk.StringVar()
        self.sha_var = tk.StringVar(value="—")
        self.statusbar_var = tk.StringVar(value="Ready")

        self._build_menu()
        self._build_ui()
        self.apply_theme()
        self.set_status("stopped")
        set_titlebar_dark(self)
        self.deiconify()

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(100, self.poll)

    def px(self, n):
        return int(n * self.scale)

    # ----------------------------------------------------------------- layout

    def _build_menu(self):
        # Native Windows menus cannot be fully colored (light bar and border), so the
        # bar is built from flat buttons that open DarkMenu popups.
        style = tb.Style.get_instance() or tb.Style()
        c = style.colors
        style.configure("Menubar.TFrame", background=c.bg)
        style.configure("Menubar.TButton", background=c.bg, foreground=c.fg, bordercolor=c.bg,
                        darkcolor=c.bg, lightcolor=c.bg, focuscolor=c.bg, relief="flat",
                        padding=(self.px(10), self.px(3)))
        style.map("Menubar.TButton",
                  background=[("pressed", c.active), ("active", c.active)],
                  bordercolor=[("pressed", c.active), ("active", c.active)],
                  darkcolor=[("pressed", c.active), ("active", c.active)],
                  lightcolor=[("pressed", c.active), ("active", c.active)])

        self.file_menu = DarkMenu(self, c, self.px)
        self.file_menu.add_command("Open bundle…", self.browse, accelerator="Ctrl+O")
        self.file_menu.add_separator()
        self.file_menu.add_command("Exit", self.on_close)
        help_menu = DarkMenu(self, c, self.px)
        help_menu.add_command(f"About {APP_NAME}", self.show_about)

        bar = tb.Frame(self, style="Menubar.TFrame", padding=(self.px(4), self.px(2)))
        bar.pack(side="top", fill="x")
        for text, menu, key in (("File", self.file_menu, "f"), ("Help", help_menu, "h")):
            btn = tb.Button(bar, text=text, style="Menubar.TButton", takefocus=False)
            btn.configure(command=lambda b=btn, m=menu: self._open_menu(b, m))
            btn.pack(side="left")
            self.bind(f"<Alt-{key}>", lambda e, b=btn, m=menu: self._open_menu(b, m))
        tb.Separator(self).pack(side="top", fill="x")
        self.bind("<Control-o>", lambda e: self.browse())

    def _open_menu(self, button, menu):
        menu.show(button.winfo_rootx(), button.winfo_rooty() + button.winfo_height())

    def _style_combo_list(self, combo):
        """Highlight the selected entry of a combobox drop-down list in the accent color."""
        c = (tb.Style.get_instance() or tb.Style()).colors
        try:
            popdown = self.tk.call("ttk::combobox::PopdownWindow", str(combo))
            self.tk.call(f"{popdown}.f.l", "configure", "-selectbackground", c.primary,
                         "-selectforeground", "#ffffff")
        except tk.TclError:
            pass

    def _build_ui(self):
        root = tb.Frame(self, padding=(self.px(14), self.px(12)))
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(2, weight=1)
        gap = self.px(10)

        # Bundle ------------------------------------------------------------
        bf = tb.LabelFrame(root, text="Bundle", padding=(gap, self.px(6), gap, gap))
        bf.grid(row=0, column=0, sticky="ew")
        bf.columnconfigure(1, weight=1)

        tb.Label(bf, text="File").grid(row=0, column=0, sticky="w", padx=(0, gap))
        self.bundle_entry = tb.Entry(bf, textvariable=self.bundle_var)
        self.bundle_entry.grid(row=0, column=1, columnspan=2, sticky="ew")
        self.bundle_entry.bind("<Return>", self.refresh_bundle_info)
        self.bundle_entry.bind("<FocusOut>", self.refresh_bundle_info)
        self.browse_btn = tb.Button(bf, text="Browse…", bootstyle="secondary-outline", command=self.browse)
        self.browse_btn.grid(row=0, column=3, padx=(gap, 0))

        rows = (("Size", "size"), ("Modified", "mtime"), ("Format", "format"))
        self.info_labels = {}
        for r, (title, key) in enumerate(rows, start=1):
            tb.Label(bf, text=title, bootstyle="secondary").grid(row=r, column=0, sticky="w", padx=(0, gap), pady=(self.px(6), 0))
            lbl = tb.Label(bf, text="—")
            lbl.grid(row=r, column=1, columnspan=3, sticky="w", pady=(self.px(6), 0))
            self.info_labels[key] = lbl

        tb.Label(bf, text="SHA-256", bootstyle="secondary").grid(row=4, column=0, sticky="w", padx=(0, gap), pady=(self.px(6), 0))
        self.sha_entry = tb.Entry(bf, textvariable=self.sha_var, state="readonly", font=("Consolas", 9))
        self.sha_entry.grid(row=4, column=1, columnspan=2, sticky="ew", pady=(self.px(6), 0))
        tb.Button(bf, text="Copy", bootstyle="secondary-outline", command=self.copy_sha).grid(row=4, column=3, padx=(gap, 0), pady=(self.px(6), 0), sticky="ew")

        # Server ------------------------------------------------------------
        sf = tb.LabelFrame(root, text="Server", padding=(gap, self.px(6), gap, gap))
        sf.grid(row=1, column=0, sticky="ew", pady=(gap, 0))
        sf.columnconfigure(5, weight=1)

        tb.Label(sf, text="Host").grid(row=0, column=0, sticky="w", padx=(0, gap))
        # Re-read the adapters every time the list is opened (cable plugged in, Wi-Fi changed...)
        self.host_combo = tb.Combobox(sf, textvariable=self.host_var, state="readonly", width=36,
                                      postcommand=self.refresh_host_choices)
        self.host_combo.grid(row=0, column=1, sticky="w")
        self.refresh_host_choices()
        tb.Label(sf, text="Port").grid(row=0, column=2, sticky="w", padx=(self.px(18), gap))
        self.port_entry = tb.Entry(sf, textvariable=self.port_var, width=7)
        self.port_entry.grid(row=0, column=3, sticky="w")
        self.toggle_btn = tb.Button(sf, text="Start server", bootstyle="primary", width=14, command=self.toggle_server)
        self.toggle_btn.grid(row=0, column=4, padx=(self.px(18), 0))

        status = tb.Frame(sf)
        status.grid(row=0, column=6, sticky="e")
        self.status_dot = tk.Canvas(status, width=self.px(12), height=self.px(12), highlightthickness=0, bd=0)
        self.status_dot.pack(side="left", padx=(0, self.px(6)))
        self.status_lbl = tb.Label(status, text="Stopped")
        self.status_lbl.pack(side="left")

        tb.Label(sf, text="URL").grid(row=1, column=0, sticky="w", pady=(gap, 0))
        self.url_combo = tb.Combobox(sf, textvariable=self.url_var, state="readonly")
        self.url_combo.grid(row=1, column=1, columnspan=5, sticky="ew", pady=(gap, 0))
        for combo in (self.host_combo, self.url_combo):
            self._style_combo_list(combo)
        self.copy_url_btn = tb.Button(sf, text="Copy URL", bootstyle="secondary-outline", command=self.copy_url, state="disabled")
        self.copy_url_btn.grid(row=1, column=6, sticky="ew", padx=(gap, 0), pady=(gap, 0))

        # Tabs --------------------------------------------------------------
        nb = tb.Notebook(root)
        nb.grid(row=2, column=0, sticky="nsew", pady=(gap, 0))

        cf = tb.Frame(nb, padding=gap)
        cf.columnconfigure(0, weight=1)
        cf.rowconfigure(0, weight=1)
        cols = (
            ("client", "Client", 115, "w"),
            ("status", "Status", 85, "w"),
            ("mode", "Mode", 100, "w"),
            ("progress", "Progress", 175, "w"),
            ("downloaded", "Downloaded", 135, "e"),
            ("sent", "Transferred", 85, "e"),
            ("speed", "Speed", 75, "e"),
            ("last", "Last activity", 85, "center"),
        )
        self.tree = tb.Treeview(cf, columns=[c[0] for c in cols], show="headings", selectmode="browse", height=5)
        for key, title, width, anchor in cols:
            self.tree.heading(key, text=title, anchor=anchor)
            self.tree.column(key, width=self.px(width), anchor=anchor, stretch=key == "progress")
        self.tree.grid(row=0, column=0, sticky="nsew")
        tsb = tb.Scrollbar(cf, orient="vertical", command=self.tree.yview)
        tsb.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=tsb.set)

        self.progress = tb.Progressbar(cf, maximum=100, bootstyle="success")
        self.progress.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(gap, 0))
        self.progress_lbl = tb.Label(cf, text="No client has connected yet.", bootstyle="secondary")
        self.progress_lbl.grid(row=2, column=0, columnspan=2, sticky="w", pady=(self.px(4), 0))
        nb.add(cf, text="Clients")

        lf = tb.Frame(nb, padding=gap)
        lf.columnconfigure(0, weight=1)
        lf.rowconfigure(0, weight=1)
        self.log_text = tk.Text(lf, height=8, state="disabled", wrap="none", font=("Consolas", 9),
                                relief="flat", borderwidth=0, padx=self.px(6), pady=self.px(4))
        self.log_text.grid(row=0, column=0, sticky="nsew")
        lsb = tb.Scrollbar(lf, orient="vertical", command=self.log_text.yview)
        lsb.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=lsb.set)
        tb.Button(lf, text="Clear log", bootstyle="secondary-outline", command=self.clear_log).grid(row=1, column=0, sticky="w", pady=(gap, 0))
        nb.add(lf, text="Log")

        tb.Label(root, textvariable=self.statusbar_var, bootstyle="secondary").grid(
            row=3, column=0, sticky="w", pady=(self.px(8), 0))

    # ----------------------------------------------------------------- theme

    def apply_theme(self):
        style = tb.Style.get_instance() or tb.Style()
        style.configure("Treeview", rowheight=self.px(28))
        c = style.colors
        self.status_dot.configure(bg=c.bg)
        self.log_text.configure(bg=c.inputbg, fg=c.inputfg, insertbackground=c.inputfg,
                                selectbackground=c.selectbg, selectforeground=c.selectfg)

    # ----------------------------------------------------------------- status

    def set_status(self, state: str, text: str = ""):
        color, default = {
            "stopped": (COLOR_IDLE, "Stopped"),
            "running": (COLOR_OK, "Running"),
            "error": (COLOR_ERROR, "Failed to start"),
        }[state]
        s = self.px(12)
        self.status_dot.delete("all")
        self.status_dot.create_oval(1, 1, s - 1, s - 1, fill=color, outline=color)
        self.status_lbl.configure(text=text or default)

    def flash(self, msg: str):
        self.statusbar_var.set(msg)
        if self.statusbar_job:
            self.after_cancel(self.statusbar_job)
        self.statusbar_job = self.after(4000, lambda: self.statusbar_var.set(""))

    # ----------------------------------------------------------------- bundle

    def browse(self):
        if self.server.running:
            return
        path = filedialog.askopenfilename(
            title="Select RAUC bundle",
            filetypes=[("RAUC bundle", "*.raucb"), ("All files", "*.*")],
        )
        if path:
            self.bundle_var.set(str(Path(path)))
            self.refresh_bundle_info()

    def bundle_path(self):
        raw = self.bundle_var.get().strip().strip('"')
        return Path(raw) if raw else None

    def refresh_bundle_info(self, _event=None, force=False):
        path = self.bundle_path()
        try:
            key = (str(path), path.stat().st_mtime_ns) if path else None
        except OSError:
            key = (str(path), None)
        if key == self.info_key and not force:
            return
        self.info_key = key
        self.hash_token += 1
        self.bundle_info = None

        def show(size="—", mtime="—", fmt="—", color=None, sha="—"):
            self.info_labels["size"].configure(text=size)
            self.info_labels["mtime"].configure(text=mtime)
            self.info_labels["format"].configure(text=fmt, foreground=color or "")
            self.sha_var.set(sha)

        if path is None:
            show()
            return
        try:
            info = inspect_bundle(path)
        except FileNotFoundError:
            show(fmt="File not found", color=COLOR_ERROR)
            return
        except PermissionError:
            show(fmt="No permission to read this file", color=COLOR_ERROR)
            return
        except OSError as e:
            show(fmt=f"Cannot read file: {e.strerror or e}", color=COLOR_ERROR)
            return

        self.bundle_info = info
        color = {"ok": COLOR_OK, "info": COLOR_INFO, "warn": COLOR_WARN, "error": COLOR_ERROR}[info["level"]]
        fmt = info["format"] + (f"  —  {info['note']}" if info["note"] else "")
        show(
            size=f"{fmt_size(info['size'])}  ({info['size']:,} bytes)",
            mtime=datetime.fromtimestamp(info["mtime"]).strftime("%Y-%m-%d  %H:%M:%S"),
            fmt=fmt, color=color, sha="Calculating…",
        )
        token = self.hash_token
        threading.Thread(target=self._hash_worker, args=(path, token), daemon=True).start()

    def _hash_worker(self, path: Path, token: int):
        def post(text):
            self.ui_queue.put(lambda: self.sha_var.set(text) if token == self.hash_token else None)

        try:
            digest = sha256_file(
                path,
                on_progress=lambda pct: post(f"Calculating… {pct} %"),
                cancelled=lambda: token != self.hash_token,  # a different bundle was selected
            )
        except OSError as e:
            post(f"Could not read file: {e.strerror or e}")
            return
        if digest:
            post(digest)

    def copy_sha(self):
        sha = self.sha_var.get()
        if re.fullmatch(r"[0-9a-f]{64}", sha):
            self.clipboard_clear()
            self.clipboard_append(sha)
            self.flash("SHA-256 copied to clipboard")

    # ----------------------------------------------------------------- server

    def toggle_server(self):
        if self.server.running:
            self.stop_server()
        else:
            self.start_server()

    def start_server(self):
        path = self.bundle_path()
        if path is None:
            messagebox.showerror("No bundle selected", "Please choose a RAUC bundle (.raucb) with Browse… first.")
            return
        problem = check_bundle_file(path)
        if problem:
            messagebox.showerror(*problem)
            return

        port = parse_port(self.port_var.get())
        if port is None:
            messagebox.showerror("Invalid port", "The port must be a whole number between 1 and 65535 (for example 8080).")
            return
        self.refresh_host_choices()
        host = self.selected_host()

        self.refresh_bundle_info()
        info = self.bundle_info
        if info and info["level"] == "error":
            if not messagebox.askyesno(
                "Check bundle",
                f"{info['format']}: {info['note']}\n\nStart the server anyway?",
                icon="warning",
            ):
                return

        try:
            self.server.start(path, host, port)
        except Exception as e:
            title, msg = explain_server_error(e, host, port)
            self.set_status("error")
            self.log(f"ERROR: {title}: {e}")
            messagebox.showerror(title, msg)
            return

        self.stats.reset(path.stat().st_size)
        self.monitor.reset()
        self.tree.delete(*self.tree.get_children())
        self.progress["value"] = 0
        self.progress_lbl.configure(text="Waiting for a client to connect…")

        urls = bundle_urls(path, host, port)
        self.url_combo["values"] = urls
        self.url_var.set(urls[0])
        self.copy_url_btn.configure(state="normal")

        self.set_status("running", f"Running on port {port}")
        self.toggle_btn.configure(text="Stop server", bootstyle="danger")
        self.lock_inputs(True)
        self.log(f"Serving {path} on {host}:{port}")
        self.flash("Server started")

    def lock_inputs(self, locked: bool):
        """Settings cannot be changed while the server is running."""
        for w in (self.bundle_entry, self.browse_btn, self.port_entry):
            w.configure(state="disabled" if locked else "normal")
        self.host_combo.configure(state="disabled" if locked else "readonly")
        self.file_menu.set_state(0, "disabled" if locked else "normal")

    def refresh_host_choices(self):
        current = self.selected_host()
        choices = {ALL_INTERFACES_LABEL: "0.0.0.0"}
        for ip, name in network_interfaces():
            choices[f"{ip}  ({name})"] = ip
        choices[LOCALHOST_LABEL] = "127.0.0.1"
        self.host_choices = choices
        self.host_combo["values"] = list(choices)
        # keep the selected address; fall back to "All interfaces" if its adapter is gone
        self.host_var.set(next((k for k, v in choices.items() if v == current), ALL_INTERFACES_LABEL))

    def selected_host(self) -> str:
        return self.host_choices.get(self.host_var.get(), "0.0.0.0")

    def stop_server(self):
        self.server.stop()
        self.set_status("stopped")
        self.toggle_btn.configure(text="Start server", bootstyle="primary")
        self.lock_inputs(False)
        self.url_combo["values"] = []
        self.url_var.set("")
        self.copy_url_btn.configure(state="disabled")
        self.log("Server stopped")
        self.flash("Server stopped")

    def copy_url(self):
        url = self.url_var.get()
        if url:
            self.clipboard_clear()
            self.clipboard_append(url)
            self.flash(f"Copied: {url}")

    # ----------------------------------------------------------------- clients / log

    def refresh_clients(self):
        clients = {r.ip: r for r in self.monitor.rows()}
        for ip, r in clients.items():
            values = (
                ip,
                r.status,
                r.mode,
                f"{text_bar(r.pct)}  {r.pct:5.1f} %",
                f"{fmt_size(r.covered)} / {fmt_size(r.total)}",
                fmt_size(r.sent),
                fmt_speed(r.speed),
                datetime.fromtimestamp(r.last).strftime("%H:%M:%S"),
            )
            if self.tree.exists(ip):
                self.tree.item(ip, values=values)
            else:
                self.tree.insert("", "end", iid=ip, values=values)

        if not clients:
            return
        sel = self.tree.selection()
        ip = sel[0] if sel and sel[0] in clients else max(clients, key=lambda k: clients[k].last)
        r = clients[ip]
        self.progress["value"] = r.pct
        self.progress_lbl.configure(
            text=f"{ip}:  {r.pct:.1f} %  ({fmt_size(r.covered)} of {fmt_size(r.total)})"
            + ("" if sel else "  —  select a row to follow a specific client")
        )

    def log(self, msg: str):
        """Thread-safe: may be called from the server thread."""
        line = f"{datetime.now():%H:%M:%S}  {msg}"
        self.ui_queue.put(lambda: self._append_log(line))

    def _append_log(self, line: str):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line + "\n")
        lines = int(self.log_text.index("end-1c").split(".")[0])
        if lines > MAX_LOG_LINES:
            self.log_text.delete("1.0", f"{lines - MAX_LOG_LINES}.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def poll(self):
        try:
            while True:
                self.ui_queue.get_nowait()()
        except queue.Empty:
            pass
        self.poll_count += 1
        if self.poll_count % 5 == 0:
            self.refresh_clients()
        self.after(100, self.poll)

    # ----------------------------------------------------------------- about

    def show_about(self):
        win = tk.Toplevel(self)
        win.withdraw()
        win.title(f"About {APP_NAME}")
        win.resizable(False, False)
        win.transient(self)
        frm = tb.Frame(win, padding=(self.px(36), self.px(24)))
        frm.pack(fill="both", expand=True)

        if self.about_img:
            tb.Label(frm, image=self.about_img).pack()
        tb.Label(frm, text=APP_NAME, font=("Segoe UI", 16, "bold")).pack(pady=(self.px(12), 0))
        tb.Label(frm, text=f"Version {__version__}", bootstyle="secondary").pack()
        tb.Label(
            frm, justify="center",
            text="Serves RAUC update bundles over HTTP with\nRange request support for RAUC streaming.",
        ).pack(pady=(self.px(14), 0))
        tb.Label(frm, text=COPYRIGHT, bootstyle="secondary").pack(pady=(self.px(14), 0))
        tb.Label(frm, text="Licensed under the MIT License", bootstyle="secondary").pack()
        buttons = tb.Frame(frm)
        buttons.pack(pady=(self.px(18), 0))
        tb.Button(buttons, text="Licenses", bootstyle="secondary-outline", width=10,
                  command=lambda: self.show_licenses(win)).pack(side="left", padx=(0, self.px(8)))
        ok = tb.Button(buttons, text="OK", bootstyle="primary", width=10, command=win.destroy)
        ok.pack(side="left")

        win.update_idletasks()
        x = self.winfo_rootx() + (self.winfo_width() - win.winfo_reqwidth()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - win.winfo_reqheight()) // 3
        win.geometry(f"+{x}+{y}")
        set_titlebar_dark(win)
        win.deiconify()
        win.bind("<Escape>", lambda e: win.destroy())
        win.bind("<Return>", lambda e: win.destroy())
        ok.focus_set()
        win.grab_set()

    def show_licenses(self, parent: tk.Toplevel):
        own, third_party = license_texts(GUI_LICENSE_PACKAGES, include_tcl=True)
        c = (tb.Style.get_instance() or tb.Style()).colors

        win = tk.Toplevel(parent)
        win.withdraw()
        win.title("Licenses")
        win.transient(parent)
        win.geometry(f"{self.px(760)}x{self.px(600)}")
        win.minsize(self.px(480), self.px(320))
        frm = tb.Frame(win, padding=self.px(12))
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(0, weight=1)
        frm.rowconfigure(0, weight=1)

        nb = tb.Notebook(frm)
        nb.grid(row=0, column=0, sticky="nsew")
        for title, content in ((f"{APP_NAME} (MIT)", own), ("Third-party software", third_party)):
            tab = tb.Frame(nb, padding=self.px(8))
            tab.columnconfigure(0, weight=1)
            tab.rowconfigure(0, weight=1)
            text = tk.Text(tab, wrap="word", font=("Consolas", 9), relief="flat", borderwidth=0,
                           padx=self.px(8), pady=self.px(6), bg=c.inputbg, fg=c.inputfg,
                           insertbackground=c.inputfg, selectbackground=c.selectbg, selectforeground=c.selectfg)
            text.insert("1.0", content)
            text.configure(state="disabled")  # read-only, but text can still be selected and copied
            text.grid(row=0, column=0, sticky="nsew")
            sb = tb.Scrollbar(tab, orient="vertical", command=text.yview)
            sb.grid(row=0, column=1, sticky="ns")
            text.configure(yscrollcommand=sb.set)
            nb.add(tab, text=title)

        def close():
            win.destroy()
            if parent.winfo_exists():
                parent.grab_set()  # give the modal grab back to the About dialog

        tb.Button(frm, text="Close", bootstyle="primary", width=10, command=close).grid(
            row=1, column=0, sticky="e", pady=(self.px(10), 0))
        win.protocol("WM_DELETE_WINDOW", close)
        win.bind("<Escape>", lambda e: close())

        win.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - win.winfo_reqwidth()) // 2
        y = max(0, parent.winfo_rooty() - self.px(80))
        win.geometry(f"+{max(0, x)}+{y}")
        set_titlebar_dark(win)
        win.deiconify()
        win.grab_set()

    def on_close(self):
        self.server.stop()
        self.destroy()


def main():
    enable_dpi_awareness()
    App().mainloop()


if __name__ == "__main__":
    main()
