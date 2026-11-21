"""RAUC Bundle Exposer - serves a RAUC bundle over HTTP (with Range support).

Package layout:
  core.py   server, statistics, bundle inspection, networking, licenses (no GUI imports)
  gui.py    Tk desktop application          ->  python -m rauc_exposer
  cli.py    command line interface          ->  python -m rauc_exposer.cli

gui.py and cli.py import from core.py; core.py never imports either of them (or tkinter),
so the CLI also runs on headless machines without Tk.
"""

__version__ = "1.0.0"
APP_NAME = "RAUC Bundle Exposer"
COPYRIGHT = "© 2026 Nikola Nenadic"
