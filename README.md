# RAUC Bundle Exposer

Serves a [RAUC](https://rauc.io/) update bundle over HTTP with **Range request** support, so a
device can install it directly from the network with RAUC streaming:

```
rauc install http://<pc-ip>:8080/bundle.raucb
```

Available as a GUI (shows which devices are downloading and how far they are) and as a
command line program for headless machines and scripts. Runs on Windows 10/11 and Linux.

Streaming needs a bundle in `verity` or `crypt` format. A `plain` bundle has to be
downloaded completely first (`wget -O /tmp/u.raucb <URL> && rauc install /tmp/u.raucb`).

## Usage

**GUI:** choose the `.raucb` file with **Browse…**, click **Start server**, copy the URL with
**Copy URL** and run `rauc install <URL>` on the device.

**CLI:**

```
RAUC-Bundle-Exposer-CLI update.raucb                  # all interfaces, port 8080
RAUC-Bundle-Exposer-CLI update.raucb -p 8888 -H 192.168.1.10
```

It prints the URLs and the SHA-256 and shows the progress of every client. Ctrl+C stops it.
Useful options: `-v` (log every request), `--exit-when-complete`, `--list-interfaces`.
All options: `--help`.

**Firewall:** the device must reach the PC on the chosen port. On Windows allow the program
when the firewall asks; on Linux e.g. `sudo ufw allow 8080/tcp`. Ports below 1024 need root
on Linux.

## Run from source

Python 3.10+ is required; the GUI also needs tkinter (Linux: `python3-tk` on Debian/Ubuntu,
`python3-tkinter` on Fedora, `tk` on Arch).

```
python3 -m venv .venv
source .venv/bin/activate              # Windows: .venv\Scripts\activate
pip install -r requirements-gui.txt    # CLI only: requirements.txt
python -m rauc_exposer                 # GUI
python -m rauc_exposer.cli --help      # CLI
```

## Build executables

```
pip install -r requirements-build.txt
python build.py            # GUI and CLI (or: python build.py gui / cli)
```

Results in `dist/`: `RAUC-Bundle-Exposer` (GUI) and `RAUC-Bundle-Exposer-CLI` (with `.exe` on
Windows). The CLI contains no tkinter/ttkbootstrap/Pillow and runs on servers without a desktop.

- PyInstaller cannot cross-compile: build Windows executables on Windows, Linux ones on Linux.
- Linux needs `binutils`; the executable runs on the same or a newer glibc than the build
  machine, so build on the oldest distribution you want to support.
- The `.exe` is not code-signed, so SmartScreen may warn (*More info → Run anyway*).
- All PyInstaller options are in the `TARGETS` table in `build.py`.

## Project structure

```
rauc_exposer/
  __init__.py   version, name, copyright
  core.py       HTTP server, client statistics, bundle check, networking, licenses
  gui.py        Tk desktop application
  cli.py        command line interface
```

`core.py` is shared by both front ends and never imports GUI libraries, so server fixes go
there. New version: change `__version__` in `rauc_exposer/__init__.py`. New icon: edit and run
`assets/make_icon.py`.

## License

[MIT](LICENSE). The executables bundle third-party components under their own licenses
(Python, aiohttp, psutil, OpenSSL, and in the GUI also ttkbootstrap, Pillow and Tcl/Tk). Their
full texts are shown under *Help → About → Licenses* in the GUI and printed by `--licenses` in
the CLI.
