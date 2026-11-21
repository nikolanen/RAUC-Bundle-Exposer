"""Shared logic of the GUI and the CLI: HTTP server, per-client statistics, bundle
inspection, networking helpers and license collection.

RAUC streaming (`rauc install http://...`) needs a server that supports HTTP Range
requests. The bundle handler below implements single-range requests, HEAD and
If-Range itself so that every byte sent can be counted for the per-client progress.

This module must not import tkinter, ttkbootstrap or Pillow: the CLI uses it on
machines without a display.
"""

import asyncio
import bisect
import email.utils
import errno
import hashlib
import math
import re
import socket
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import psutil
from aiohttp import web

from . import APP_NAME

CHUNK_SIZE = 256 * 1024
ALIAS_NAME = "bundle.raucb"
DEFAULT_PORT = 8080
IDLE_AFTER_S = 3  # a client without any transfer for this long is shown as "Idle"

# Project folder (source checkout) or the unpack folder of a PyInstaller executable
BASE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))


# --------------------------------------------------------------------------- helpers


def resource_path(name: str) -> Path:
    """Path to a bundled asset, both when run from source and from a PyInstaller exe."""
    return BASE_DIR / "assets" / name


def fmt_size(n: float) -> str:
    if n < 1024:
        return f"{int(n)} B"
    for unit in ("KB", "MB", "GB", "TB"):
        n /= 1024
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}"


def fmt_speed(bytes_per_s: float) -> str:
    return f"{fmt_size(bytes_per_s)}/s" if bytes_per_s >= 1024 else "—"


def text_bar(pct: float, width: int = 10) -> str:
    filled = min(width, max(0, round(pct / 100 * width)))
    return "█" * filled + "░" * (width - filled)


def parse_port(text: str):
    """Port number from user input, or None if it is not a valid TCP port."""
    text = str(text).strip()
    if text.isdigit() and 1 <= int(text) <= 65535:
        return int(text)
    return None


def sha256_file(path: Path, on_progress=None, cancelled=None):
    """SHA-256 hex digest of a file.

    on_progress(pct) is called whenever the whole percentage changes; cancelled() is
    checked between chunks and makes the function return None. Raises OSError.
    """
    size = path.stat().st_size
    h = hashlib.sha256()
    done, last_pct = 0, -1
    with path.open("rb") as f:
        while chunk := f.read(4 * 1024 * 1024):
            if cancelled and cancelled():
                return None
            h.update(chunk)
            done += len(chunk)
            pct = done * 100 // max(size, 1)
            if on_progress and pct != last_pct:
                last_pct = pct
                on_progress(pct)
    return h.hexdigest()


# Parts of adapter names that mark virtual adapters (listed after the real networks)
VIRTUAL_ADAPTER_HINTS = (
    # Windows
    "vethernet", "virtual", "vmware", "virtualbox", "hyper-v", "wsl",
    # Linux: Docker (docker0, br-<id>), container veth pairs, libvirt/KVM, LXC/LXD, Podman
    "docker", "br-", "veth", "virbr", "lxcbr", "lxdbr", "podman",
)


def primary_ip():
    """IPv4 address of the adapter that holds the default route (no packet is sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return None


def network_interfaces():
    """Active IPv4 adapters as (ip, adapter name).

    Default-route adapter first, virtual adapters (WSL, Hyper-V, VMware...) last.
    Link-local 169.254.x.x addresses are kept: a laptop cabled directly to a device
    without DHCP gets exactly such an address.
    """
    primary = primary_ip()
    result = []
    try:
        stats = psutil.net_if_stats()
        for name, addrs in psutil.net_if_addrs().items():
            if name in stats and not stats[name].isup:
                continue
            for a in addrs:
                if a.family == socket.AF_INET and not a.address.startswith("127."):
                    result.append((a.address, name))
    except Exception:
        pass
    if primary and all(ip != primary for ip, _ in result):
        result.append((primary, "default route"))

    def order(item):
        ip, name = item
        virtual = any(h in name.lower() for h in VIRTUAL_ADAPTER_HINTS)
        return ip != primary, virtual, name.lower(), ip

    return sorted(result, key=order)


def local_ips():
    return [ip for ip, _ in network_interfaces()] or ["127.0.0.1"]


def bundle_urls(bundle: Path, host: str, port: int):
    """URLs under which the devices can reach the bundle (file name and the stable alias)."""
    ips = local_ips() if host == "0.0.0.0" else [host]
    urls = []
    for ip in ips:
        urls.append(f"http://{ip}:{port}/{quote(bundle.name)}")
        if bundle.name != ALIAS_NAME:
            urls.append(f"http://{ip}:{port}/{ALIAS_NAME}")
    return urls


# --------------------------------------------------------------------------- licenses

# Top-level runtime dependencies of each front end; their own dependencies are found automatically.
GUI_LICENSE_PACKAGES = ("aiohttp", "ttkbootstrap", "psutil")
CLI_LICENSE_PACKAGES = ("aiohttp", "psutil")
_LICENSE_FILE_RE = re.compile(r"^(LICEN[CS]E|COPYING|NOTICE)", re.I)


def _license_section(title: str, text: str) -> str:
    line = "=" * 78
    return f"{line}\n{title}\n{line}\n\n{text.strip()}\n\n"


def collect_third_party_licenses(root_packages=GUI_LICENSE_PACKAGES, include_tcl=True) -> str:
    """License texts of all third-party software that ends up in the executable.

    Read from the metadata of the installed packages, so it always matches the
    versions that are actually bundled. Used by build.py and when run from source.
    """
    from importlib import metadata
    import ssl
    import sysconfig

    out = [
        f"{APP_NAME} includes the following third-party software.\n"
        "Each component is distributed under the license reproduced below.\n\n"
    ]

    # Python itself (incl. the additional conditions for its bundled C libraries)
    stdlib = Path(sysconfig.get_paths()["stdlib"])
    for p in (Path(sys.base_prefix) / "LICENSE.txt", stdlib / "LICENSE.txt"):
        if p.is_file():
            out.append(_license_section(f"Python {sys.version.split()[0]}  -  PSF License",
                                        p.read_text(encoding="utf-8", errors="replace")))
            break

    # Python packages: the root packages plus everything they require
    dists, todo = {}, list(root_packages)
    while todo:
        name = todo.pop(0)
        key = re.sub(r"[-_.]+", "-", name).lower()
        if key in dists:
            continue
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            continue  # optional dependency that is not installed -> not bundled either
        dists[key] = dist
        for req in dist.requires or []:
            if "extra ==" not in req:
                todo.append(re.match(r"[A-Za-z0-9_.\-]+", req).group(0))

    for key in sorted(dists):
        dist = dists[key]
        meta = dist.metadata
        license_id = (meta.get("License-Expression") or meta.get("License") or "").strip().splitlines()
        texts = []
        for f in dist.files or []:
            if _LICENSE_FILE_RE.match(f.name):
                try:
                    body = Path(f.locate()).read_text(encoding="utf-8", errors="replace").strip()
                except OSError:
                    continue
                texts.append(f"--- {f} ---\n\n{body}")
        title = f"{meta['Name']} {dist.version}" + (f"  -  {license_id[0]}" if license_id else "")
        out.append(_license_section(title, "\n\n".join(texts) or "(no license file shipped with this package)"))

    # Tcl/Tk (tkinter) - only part of the GUI executable
    if include_tcl:
        for p in sorted(Path(sys.base_prefix).glob("tcl/*/license.terms"))[:1]:
            out.append(_license_section("Tcl/Tk  -  Tcl/Tk License (BSD-style)",
                                        p.read_text(encoding="utf-8", errors="replace")))

    # OpenSSL (used by Python's ssl module, bundled with Python)
    out.append(_license_section(
        f"{ssl.OPENSSL_VERSION}  -  Apache-2.0",
        "OpenSSL 3.x is licensed under the Apache License, Version 2.0.\n"
        "The full text of the Apache License 2.0 is reproduced above (see e.g. aiohttp).\n"
        "https://www.openssl.org/source/license.html",
    ))
    return "".join(out)


def license_texts(root_packages=GUI_LICENSE_PACKAGES, include_tcl=True):
    """(own license, third-party licenses) - bundled texts in an executable, collected from source otherwise."""
    def read(p: Path) -> str:
        try:
            return p.read_text(encoding="utf-8")
        except OSError:
            return f"License file not found: {p.name}"

    if getattr(sys, "frozen", False):  # running as executable: texts were bundled by build.py
        base = BASE_DIR / "licenses"
        return read(base / "LICENSE"), read(base / "THIRD_PARTY_LICENSES.txt")
    return read(BASE_DIR / "LICENSE"), collect_third_party_licenses(root_packages, include_tcl)


# --------------------------------------------------------------------------- errors


def check_bundle_file(path: Path):
    """None if the bundle can be served, otherwise (title, message)."""
    if not path.is_file():
        return "Bundle not found", f"The file\n\n{path}\n\ndoes not exist or is not a file."
    try:
        with path.open("rb"):
            pass
    except OSError as e:
        return "Cannot read bundle", f"The file\n\n{path}\n\ncannot be opened:\n{e.strerror or e}"
    return None


def explain_server_error(e: Exception, host: str, port: int):
    """Turn a bind/start exception into a (title, message) pair a user can act on."""
    if isinstance(e, socket.gaierror):
        return "Invalid host", (
            f"\"{host}\" is not a valid IP address or host name.\n\n"
            "Use 0.0.0.0 to listen on all network interfaces."
        )
    code = getattr(e, "winerror", None) or getattr(e, "errno", None)
    if code is None and isinstance(e, OSError):
        # Newer asyncio versions hide the real cause ("could not bind on any address"),
        # so reproduce the bind with a plain socket to find out why it failed.
        try:
            family = socket.AF_INET6 if ":" in host else socket.AF_INET
            with socket.socket(family, socket.SOCK_STREAM) as probe:
                probe.bind((host, port))
        except OSError as probe_err:
            code = getattr(probe_err, "winerror", None) or probe_err.errno
    if code in (errno.EADDRINUSE, 10048, 98):
        alt = port + 1 if port < 65535 else 8080
        return "Port already in use", (
            f"Port {port} is already used by another program.\n\n"
            f"Choose a different port (for example {alt}) or close the program that is using it."
        )
    if code in (errno.EACCES, 10013, 13):
        if sys.platform == "win32":
            return "Port not allowed", (
                f"Windows did not allow the use of port {port}.\n\n"
                "The port may be reserved by the system (Hyper-V, WSL, Docker) or blocked by "
                "security software. Try a different port, for example 8080 or 8888."
            )
        return "Port not allowed", (
            f"The system did not allow the use of port {port}.\n\n"
            "Ports below 1024 require root rights. Use a port like 8080 instead."
        )
    if code in (errno.EADDRNOTAVAIL, 10049, 99):
        return "Network adapter not available", (
            f"The address {host} is not available on this computer - "
            "the network adapter was probably disconnected or got a new address.\n\n"
            "Choose another host, for example all interfaces (0.0.0.0)."
        )
    return "Server could not start", f"The server could not be started on {host}:{port}.\n\nDetails: {e}"


# --------------------------------------------------------------------------- bundle inspection


def inspect_bundle(path: Path) -> dict:
    """Best-effort check of the RAUC bundle layout.

    A RAUC bundle is: payload (SquashFS image, for 'verity' followed by a dm-verity
    hash tree) + CMS signature (DER) + 8-byte big-endian signature size.
    """
    st = path.stat()
    size = st.st_size
    info = {"size": size, "mtime": st.st_mtime, "format": "", "level": "ok", "note": ""}

    with path.open("rb") as f:
        head = f.read(64)
        sig_size = 0
        sig_start = b""
        if size > 16:
            f.seek(size - 8)
            sig_size = int.from_bytes(f.read(8), "big")
            if 0 < sig_size < size - 8:
                f.seek(size - 8 - sig_size)
                sig_start = f.read(1)

    if not (0 < sig_size < size - 8) or sig_start != b"\x30":
        info.update(format="Not a RAUC bundle", level="error",
                    note="No RAUC signature found at the end of the file.")
        return info

    payload = size - 8 - sig_size
    sig = f"signature {fmt_size(sig_size)}"
    if head[:4] != b"hsqs":
        info.update(format="crypt (encrypted)", level="ok",
                    note=f"Encrypted payload, {sig}. Streaming supported.")
        return info

    bytes_used = int.from_bytes(head[40:48], "little")
    if bytes_used > payload:
        info.update(format="Damaged bundle", level="error",
                    note="The SquashFS image is larger than the payload - the file is probably truncated.")
    elif payload - bytes_used >= 4096:
        info.update(format="verity", level="ok", note=f"SquashFS + dm-verity hash tree, {sig}. Streaming supported.")
    else:
        info.update(format="plain", level="info",
                    note=f"SquashFS, {sig}. Not streamable - served as a normal full download.")
    return info


# --------------------------------------------------------------------------- per-client statistics


def _merge_interval(intervals: list, s: int, e: int) -> int:
    """Insert [s, e) into a sorted list of disjoint intervals; return newly covered bytes."""
    i = bisect.bisect_right(intervals, (s, math.inf)) - 1
    lo = i if i >= 0 and intervals[i][1] >= s else i + 1
    hi = lo
    while hi < len(intervals) and intervals[hi][0] <= e:
        hi += 1
    old = 0
    if hi > lo:
        s = min(s, intervals[lo][0])
        e = max(e, intervals[hi - 1][1])
        old = sum(b - a for a, b in intervals[lo:hi])
    intervals[lo:hi] = [(s, e)]
    return (e - s) - old


class ClientStats:
    """Thread-safe byte accounting per client IP (written by server thread, read by the front end)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._clients = {}
        self.total = 0

    def reset(self, total: int):
        with self._lock:
            self._clients = {}
            self.total = total

    def _client(self, ip):
        c = self._clients.get(ip)
        if c is None:
            c = self._clients[ip] = {"intervals": [], "covered": 0, "sent": 0, "active": 0,
                                     "range_requests": 0, "full_requests": 0, "last": time.time()}
        return c

    def begin(self, ip, ranged: bool):
        with self._lock:
            c = self._client(ip)
            c["range_requests" if ranged else "full_requests"] += 1
            c["active"] += 1
            c["last"] = time.time()

    def end(self, ip):
        with self._lock:
            c = self._client(ip)
            c["active"] = max(0, c["active"] - 1)
            c["last"] = time.time()

    def add(self, ip, offset, length):
        with self._lock:
            c = self._client(ip)
            c["covered"] += _merge_interval(c["intervals"], offset, offset + length)
            c["sent"] += length
            c["last"] = time.time()

    def snapshot(self):
        with self._lock:
            return self.total, {ip: {k: v for k, v in c.items() if k != "intervals"} for ip, c in self._clients.items()}


@dataclass
class ClientRow:
    """What the front ends show for one client."""
    ip: str
    status: str      # Downloading / Idle / Complete
    mode: str        # Streaming / Full download / Mixed
    pct: float       # share of the bundle that has been sent at least once
    covered: int     # bytes of the bundle sent at least once
    total: int       # bundle size
    sent: int        # all bytes sent (re-sent ranges included)
    speed: float     # bytes per second, smoothed
    last: float      # time.time() of the last activity


class ClientMonitor:
    """Turns ClientStats snapshots into ClientRows (status, mode, smoothed speed).

    Call rows() periodically (every 0.5 - 1 s); the speed is measured between two calls.
    """

    def __init__(self, stats: ClientStats):
        self.stats = stats
        self._speeds = {}

    def reset(self):
        self._speeds.clear()

    def rows(self):
        total, clients = self.stats.snapshot()
        now = time.monotonic()
        rows = []
        for ip, c in clients.items():
            prev = self._speeds.get(ip)
            speed = 0.0
            if prev:
                dt = now - prev[1]
                inst = (c["sent"] - prev[0]) / dt if dt > 0 else 0
                speed = 0.5 * prev[2] + 0.5 * inst
            self._speeds[ip] = (c["sent"], now, speed)

            pct = 100 * c["covered"] / total if total else 0
            # RAUC streaming sends many short Range requests with pauses in between
            # (it fetches data as it writes), so "a request is open right now" is not
            # a useful signal. A client counts as downloading until it has been quiet
            # for IDLE_AFTER_S seconds.
            recently_active = time.time() - c["last"] < IDLE_AFTER_S
            if total and c["covered"] >= total and not c["active"]:
                status = "Complete"
            elif c["active"] or recently_active:
                status = "Downloading"
            else:
                status = "Idle"
            # Streaming = RAUC fetching parts via Range requests, Full download = plain GET of the whole file
            if c["range_requests"] and c["full_requests"]:
                mode = "Mixed"
            elif c["range_requests"]:
                mode = "Streaming"
            else:
                mode = "Full download"
            rows.append(ClientRow(ip, status, mode, pct, c["covered"], total, c["sent"], speed, c["last"]))
        return rows


# --------------------------------------------------------------------------- HTTP server


class RangeNotSatisfiable(Exception):
    pass


def parse_range(header: str, size: int):
    """Parse a single 'bytes=a-b' range. Returns (start, end_exclusive) or None to ignore it."""
    m = re.fullmatch(r"\s*bytes\s*=\s*(\d*)\s*-\s*(\d*)\s*", header)
    if not m:
        return None  # multi-range or unknown unit: RFC allows answering with the full file
    a, b = m.groups()
    if not a and not b:
        return None
    if not a:  # suffix range: last N bytes
        n = int(b)
        if n == 0:
            raise RangeNotSatisfiable
        return max(0, size - n), size
    start = int(a)
    if start >= size:
        raise RangeNotSatisfiable
    end = size if not b else min(int(b) + 1, size)
    if end <= start:
        return None
    return start, end


class BundleServer:
    """Runs an aiohttp server in its own thread with its own event loop.

    log(msg)         errors and important events (thread-safe callback)
    request_log(msg) one line per request; defaults to log. RAUC streaming makes
                     many requests, so the CLI only shows them with --verbose.
    """

    def __init__(self, log, stats: ClientStats, request_log=None):
        self.log = log
        self.request_log = request_log or log
        self.stats = stats
        self.loop = None
        self.thread = None
        self.runner = None
        self.bundle = None

    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self, bundle: Path, host: str, port: int):
        self.bundle = bundle
        ready = threading.Event()
        error = []

        def run():
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            try:
                self.loop.run_until_complete(self._setup(host, port))
            except Exception as e:  # e.g. port already in use
                error.append(e)
                if self.runner:
                    self.loop.run_until_complete(self.runner.cleanup())
                self.loop.close()
                ready.set()
                return
            ready.set()
            self.loop.run_forever()
            self.loop.run_until_complete(self.runner.cleanup())
            self.loop.close()

        self.runner = None
        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()
        ready.wait()
        if error:
            self.thread = None
            raise error[0]

    def stop(self):
        if self.running:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(timeout=5)
        self.thread = None

    async def _setup(self, host: str, port: int):
        app = web.Application()
        app.on_response_prepare.append(self._access_log)
        app.router.add_get("/", self._index)
        app.router.add_get("/" + self.bundle.name, self._serve_bundle)  # also handles HEAD
        if self.bundle.name != ALIAS_NAME:
            app.router.add_get("/" + ALIAS_NAME, self._serve_bundle)  # stable alias

        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        await web.TCPSite(self.runner, host, port).start()

    async def _access_log(self, request, response):
        # Runs when headers are sent, so the real status (e.g. 206) is known.
        rng = request.headers.get("Range", "-")
        self.request_log(f"{request.remote}  {request.method} {request.path}  Range: {rng}  -> {response.status}")

    async def _index(self, request):
        name = quote(self.bundle.name)
        return web.Response(
            text=f'<html><body><a href="/{name}">{self.bundle.name}</a></body></html>',
            content_type="text/html",
        )

    async def _serve_bundle(self, request):
        ip = request.remote or "?"
        path = self.bundle
        try:
            st = path.stat()
        except OSError:
            self.log(f"ERROR: bundle file is no longer available: {path}")
            raise web.HTTPNotFound(text="Bundle file is no longer available.\n")

        size = st.st_size
        last_modified = email.utils.formatdate(st.st_mtime, usegmt=True)
        etag = f'"{st.st_mtime_ns:x}-{size:x}"'
        headers = {
            "Accept-Ranges": "bytes",
            "Content-Type": "application/octet-stream",
            "Last-Modified": last_modified,
            "ETag": etag,
        }

        start, end, status = 0, size, 200
        range_header = request.headers.get("Range")
        if_range = request.headers.get("If-Range")
        if range_header and (if_range is None or if_range in (etag, last_modified)):
            try:
                rng = parse_range(range_header, size)
            except RangeNotSatisfiable:
                return web.Response(status=416, headers={"Content-Range": f"bytes */{size}", "Accept-Ranges": "bytes"})
            if rng:
                start, end = rng
                status = 206
                headers["Content-Range"] = f"bytes {start}-{end - 1}/{size}"

        resp = web.StreamResponse(status=status, headers=headers)
        resp.content_length = end - start
        await resp.prepare(request)
        if request.method == "HEAD":
            return resp

        loop = asyncio.get_running_loop()
        self.stats.begin(ip, ranged=status == 206)
        try:
            with path.open("rb") as f:
                f.seek(start)
                pos = start
                while pos < end:
                    chunk = await loop.run_in_executor(None, f.read, min(CHUNK_SIZE, end - pos))
                    if not chunk:
                        break
                    await resp.write(chunk)
                    self.stats.add(ip, pos, len(chunk))
                    pos += len(chunk)
            await resp.write_eof()
        except ConnectionError:
            self.request_log(f"{ip}  connection closed by client")
        except OSError as e:
            self.log(f"ERROR: could not read bundle: {e}")
        finally:
            self.stats.end(ip)
        return resp
