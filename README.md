# RAUC Bundle Exposer

A small tool that serves a RAUC update bundle (.raucb) over HTTP, so a device can
install it directly from the network:

    rauc install http://<pc-ip>:8080/bundle.raucb

It comes with a GUI and a CLI and runs on Windows and Linux.

Streaming installation requires a bundle in verity or crypt format. A plain bundle
must be downloaded to the device first and installed from there.

## Usage

GUI: select the bundle, click "Start server" and run the shown URL with
`rauc install` on the device.

CLI:

    RAUC-Bundle-Exposer-CLI update.raucb
    RAUC-Bundle-Exposer-CLI update.raucb -p 8888 -H 192.168.1.10

Run with `--help` to see all options.

Make sure the firewall allows incoming connections on the chosen port.

## Running from source

Requires Python 3.10 or newer (and tkinter for the GUI).

Linux:

    python3 -m venv .venv
    source .venv/bin/activate
    pip install -r requirements-gui.txt

Windows:

    python -m venv .venv
    .venv\Scripts\activate
    pip install -r requirements-gui.txt

Then start the application:

    python -m rauc_exposer          # GUI
    python -m rauc_exposer.cli      # CLI

If you only need the CLI, install `requirements.txt` instead.

## Building

    pip install -r requirements-build.txt
    python build.py

The executables are written to `dist/`. Build on the platform you are targeting.

## License

MIT, see LICENSE.
