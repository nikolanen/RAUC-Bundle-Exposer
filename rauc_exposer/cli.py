"""Command line interface: serves a RAUC bundle without a GUI (headless machines, scripts, CI).

    python -m rauc_exposer.cli BUNDLE [--host HOST] [--port PORT] [options]

On a terminal a live status line shows every client's progress; when the output is
redirected (log file, systemd) a progress line per active client is printed instead.
Press Ctrl+C (or send SIGTERM) to stop the server.
"""

import argparse
import shutil
import signal
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from . import APP_NAME, COPYRIGHT, __version__
from .core import (
    DEFAULT_PORT,
    CLI_LICENSE_PACKAGES,
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
    sha256_file,
    text_bar,
)

REFRESH_S = 1.0        # how often the client statistics are read
PLAIN_REPORT_S = 5.0   # redirected output: at most one progress line per client this often

EXIT_OK, EXIT_ERROR, EXIT_USAGE = 0, 1, 2


def one_line(text: str) -> str:
    """GUI messages contain blank lines; on the console they read better as one line."""
    return " ".join(text.split())


class Console:
    """Serialises output of the server thread, the hash thread and the main loop.

    In live mode (stdout is a terminal) the last line is a status line that is
    redrawn in place; normal lines are printed above it.
    """

    def __init__(self, live: bool):
        self.live = live
        self._lock = threading.Lock()
        self._status = ""

    def _clear_status(self):
        if self.live and self._status:
            sys.stdout.write("\r\033[K")

    def _draw_status(self):
        if self.live and self._status:
            sys.stdout.write(self._status)
        sys.stdout.flush()

    def print(self, text: str = "", error=False):
        with self._lock:
            self._clear_status()
            sys.stdout.flush()
            print(text, file=sys.stderr if error else sys.stdout, flush=True)
            self._draw_status()

    def log(self, msg: str, error=False):
        """Thread-safe: may be called from the server thread."""
        self.print(f"{datetime.now():%H:%M:%S}  {msg}", error=error)

    def status(self, text: str):
        if not self.live:
            return
        with self._lock:
            self._clear_status()
            width = max(20, shutil.get_terminal_size().columns - 1)
            self._status = text if len(text) <= width else text[: width - 1] + "…"
            self._draw_status()

    def end_status(self):
        with self._lock:
            self._clear_status()
            self._status = ""
            sys.stdout.flush()


def enable_ansi() -> bool:
    """Lets the Windows console interpret the escape codes of the status line."""
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except Exception:
        return False


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="RAUC-Bundle-Exposer-CLI",
        description=f"{APP_NAME} {__version__} - serves a RAUC bundle over HTTP with Range "
                    "support, so a device can install it with RAUC streaming:  "
                    "rauc install http://<pc-ip>:<port>/bundle.raucb",
    )
    p.add_argument("bundle", nargs="?", type=Path, help="the RAUC bundle (.raucb) to serve")
    p.add_argument("-H", "--host", default="0.0.0.0",
                   help="address to listen on (default: 0.0.0.0 = all interfaces)")
    p.add_argument("-p", "--port", type=int, default=DEFAULT_PORT, help=f"TCP port (default: {DEFAULT_PORT})")
    p.add_argument("-v", "--verbose", action="store_true", help="log every HTTP request")
    p.add_argument("--no-sha", action="store_true", help="do not calculate the SHA-256 of the bundle")
    p.add_argument("--plain", action="store_true",
                   help="no live status line, print progress lines instead (default when output is redirected)")
    p.add_argument("-f", "--force", action="store_true",
                   help="serve the file even if it does not look like a valid RAUC bundle")
    p.add_argument("--exit-when-complete", action="store_true",
                   help="stop the server as soon as one client has received the whole bundle")
    p.add_argument("--list-interfaces", action="store_true", help="list the network interfaces and exit")
    p.add_argument("--licenses", action="store_true", help="print the license texts and exit")
    p.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    return p


def list_interfaces():
    print("Address           Interface")
    for ip, name in network_interfaces():
        print(f"{ip:<17} {name}")
    print(f"{'0.0.0.0':<17} all interfaces")
    print(f"{'127.0.0.1':<17} localhost")


def print_licenses():
    own, third_party = license_texts(CLI_LICENSE_PACKAGES, include_tcl=False)
    print(f"{APP_NAME} {__version__}\n{COPYRIGHT}\n\n{own.strip()}\n\n{third_party}")


def describe(rows) -> str:
    """Live status line for all clients."""
    if not rows:
        return "Waiting for a client to connect…   (Ctrl+C to stop)"
    parts = [f"{r.ip} {r.status} {text_bar(r.pct)} {r.pct:5.1f} % {fmt_speed(r.speed)}"
             for r in sorted(rows, key=lambda r: r.ip)]
    return "  |  ".join(parts)


def progress_line(r) -> str:
    return (f"{r.ip}  {r.status}  {r.mode}  {r.pct:5.1f} %  "
            f"({fmt_size(r.covered)} / {fmt_size(r.total)})  {fmt_speed(r.speed)}")


def serve(args, console: Console) -> int:
    path = args.bundle.expanduser().resolve()
    problem = check_bundle_file(path)
    if problem:
        console.print(f"Error: {problem[0]}: {one_line(problem[1])}", error=True)
        return EXIT_ERROR
    if not 1 <= args.port <= 65535:
        console.print("Error: the port must be a whole number between 1 and 65535 (for example 8080).", error=True)
        return EXIT_USAGE

    info = inspect_bundle(path)
    console.print(f"{APP_NAME} {__version__}")
    console.print(f"Bundle    {path}")
    console.print(f"Size      {fmt_size(info['size'])}  ({info['size']:,} bytes)")
    console.print(f"Modified  {datetime.fromtimestamp(info['mtime']):%Y-%m-%d  %H:%M:%S}")
    console.print(f"Format    {info['format']}" + (f"  -  {info['note']}" if info["note"] else ""))
    if info["level"] == "error" and not args.force:
        console.print(f"Error: {info['format']}: {info['note']}  Use --force to serve it anyway.", error=True)
        return EXIT_ERROR

    stats = ClientStats()
    monitor = ClientMonitor(stats)
    server = BundleServer(
        log=lambda m: console.log(m, error=m.startswith("ERROR")),
        stats=stats,
        request_log=console.log if args.verbose else (lambda m: None),
    )
    try:
        server.start(path, args.host, args.port)
    except Exception as e:
        title, msg = explain_server_error(e, args.host, args.port)
        console.print(f"Error: {title}: {one_line(msg)}", error=True)
        return EXIT_ERROR
    stats.reset(info["size"])

    urls = bundle_urls(path, args.host, args.port)
    console.print(f"Serving on {args.host}:{args.port}")
    for url in urls:
        console.print(f"  {url}")
    console.print(f"On the device:  rauc install {urls[0]}")
    console.print("Press Ctrl+C to stop.")
    console.print()

    if not args.no_sha:
        def hash_worker():
            try:
                console.log(f"SHA-256  {sha256_file(path)}")
            except OSError as e:
                console.log(f"Could not calculate SHA-256: {e.strerror or e}", error=True)
        threading.Thread(target=hash_worker, daemon=True).start()

    known = {}          # ip -> last status
    reported = {}       # ip -> (covered at the last progress line, time)
    try:
        while True:
            time.sleep(REFRESH_S)
            rows = monitor.rows()
            finished = False
            for r in rows:
                before = known.get(r.ip)
                if before is None:
                    console.log(f"{r.ip}  connected")
                if r.status == "Complete" and before != "Complete":
                    console.log(f"{r.ip}  download complete ({fmt_size(r.total)}, {r.mode.lower()})")
                    finished = True
                known[r.ip] = r.status

                if not console.live:
                    covered, at = reported.get(r.ip, (-1, 0.0))
                    if r.covered != covered and time.monotonic() - at >= PLAIN_REPORT_S:
                        console.log(progress_line(r))
                        reported[r.ip] = (r.covered, time.monotonic())
            console.status(describe(rows))
            if finished and args.exit_when_complete:
                break
    except KeyboardInterrupt:
        pass
    finally:
        console.end_status()
        server.stop()
        console.log("Server stopped")
    return EXIT_OK


def main(argv=None) -> int:
    # Windows consoles with a legacy code page cannot print the progress bar characters
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass

    args = build_parser().parse_args(argv)
    if args.list_interfaces:
        list_interfaces()
        return EXIT_OK
    if args.licenses:
        print_licenses()
        return EXIT_OK
    if args.bundle is None:
        build_parser().error("the bundle file is required (or use --list-interfaces / --licenses)")

    # SIGTERM (systemd, docker stop, kill) stops the server cleanly like Ctrl+C
    def on_sigterm(_signum, _frame):
        raise KeyboardInterrupt

    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, on_sigterm)

    live = sys.stdout.isatty() and not args.plain and enable_ansi()
    return serve(args, Console(live))


if __name__ == "__main__":
    sys.exit(main())
