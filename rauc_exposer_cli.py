"""Entry point of the CLI (used by PyInstaller and for `python rauc_exposer_cli.py`)."""

import sys

from rauc_exposer.cli import main

if __name__ == "__main__":
    sys.exit(main())
