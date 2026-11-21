"""Builds one-file executables with PyInstaller.

    python build.py          GUI and CLI
    python build.py gui      only the GUI
    python build.py cli      only the CLI

Windows: dist/RAUC-Bundle-Exposer.exe and dist/RAUC-Bundle-Exposer-CLI.exe (icon + version info)
Linux:   dist/RAUC-Bundle-Exposer    and dist/RAUC-Bundle-Exposer-CLI

The GUI is a windowed app with ttkbootstrap; the CLI is a console program that contains
neither tkinter nor ttkbootstrap nor Pillow, so it also runs on headless machines.
The version is taken from __version__ in rauc_exposer/__init__.py (single source of truth).
"""

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
IS_WINDOWS = sys.platform == "win32"

source = (ROOT / "rauc_exposer" / "__init__.py").read_text(encoding="utf-8")
version = re.search(r'^__version__ = "([^"]+)"', source, re.M).group(1)
app_name = re.search(r'^APP_NAME = "([^"]+)"', source, re.M).group(1)
copyright_ = re.search(r'^COPYRIGHT = "([^"]+)"', source, re.M).group(1)
nums = tuple((list(map(int, re.findall(r"\d+", version))) + [0, 0, 0, 0])[:4])

sys.path.insert(0, str(ROOT))
from rauc_exposer.core import (  # noqa: E402  (core has no GUI imports, nothing is started)
    CLI_LICENSE_PACKAGES,
    GUI_LICENSE_PACKAGES,
    collect_third_party_licenses,
)

# Per target: executable name, entry script, description, license set and PyInstaller options
TARGETS = {
    "gui": {
        "exe": "RAUC-Bundle-Exposer",
        "script": "rauc_exposer_gui.py",
        "description": app_name,
        "licenses": (GUI_LICENSE_PACKAGES, True),
        "options": [
            "--windowed",
            "--collect-all", "ttkbootstrap",
            # Pillow loads this dynamically for ImageTk; PyInstaller misses it on Linux
            "--hidden-import", "PIL._tkinter_finder",
            "--hidden-import", "PIL.ImageTk",
        ],
        "window_icons": True,
    },
    "cli": {
        "exe": "RAUC-Bundle-Exposer-CLI",
        "script": "rauc_exposer_cli.py",
        "description": f"{app_name} (command line)",
        "licenses": (CLI_LICENSE_PACKAGES, False),
        "options": [
            "--console",
            # keep the CLI small and headless-safe
            "--exclude-module", "tkinter",
            "--exclude-module", "_tkinter",
            "--exclude-module", "ttkbootstrap",
            "--exclude-module", "PIL",
            "--exclude-module", "rauc_exposer.gui",
        ],
        "window_icons": False,
    },
}


def version_info(exe: str, description: str) -> str:
    return f"""\
VSVersionInfo(
  ffi=FixedFileInfo(filevers={nums}, prodvers={nums}, mask=0x3f, flags=0x0,
                    OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('FileDescription', {description!r}),
      StringStruct('FileVersion', {version!r}),
      StringStruct('InternalName', {exe!r}),
      StringStruct('OriginalFilename', {exe + '.exe'!r}),
      StringStruct('ProductName', {app_name!r}),
      StringStruct('ProductVersion', {version!r}),
      StringStruct('LegalCopyright', {copyright_!r}),
    ])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])]),
  ]
)
"""


def build(name: str, t: dict):
    build_dir = ROOT / "build" / name
    build_dir.mkdir(parents=True, exist_ok=True)

    # License texts (GUI: Help > About > Licenses, CLI: --licenses), bundled into the executable
    licenses_dir = build_dir / "licenses"
    licenses_dir.mkdir(exist_ok=True)
    (licenses_dir / "LICENSE").write_text((ROOT / "LICENSE").read_text(encoding="utf-8"), encoding="utf-8")
    packages, include_tcl = t["licenses"]
    (licenses_dir / "THIRD_PARTY_LICENSES.txt").write_text(
        collect_third_party_licenses(packages, include_tcl), encoding="utf-8")

    assets = ROOT / "assets"
    pngs = [assets / f"app_{n}.png" for n in (16, 32, 48, 96, 256)]
    windows_only = []
    if IS_WINDOWS:  # .exe file icon and version info (Properties > Details) exist only on Windows
        version_file = build_dir / "version_info.txt"
        version_file.write_text(version_info(t["exe"], t["description"]), encoding="utf-8")
        windows_only = ["--icon", str(assets / "app.ico"), "--version-file", str(version_file)]
    # app_<n>.png = window icons + About image, only needed by the GUI
    window_icons = [arg for p in pngs for arg in ("--add-data", f"{p}{os.pathsep}assets")] if t["window_icons"] else []

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onefile",
        "--name", t["exe"],
        *t["options"],
        *windows_only,
        *window_icons,
        "--add-data", f"{licenses_dir}{os.pathsep}licenses",
        "--paths", str(ROOT),
        "--specpath", str(build_dir),
        "--workpath", str(build_dir / "work"),
        "--distpath", str(ROOT / "dist"),
        str(ROOT / t["script"]),
    ]
    print(f"\n=== Building {t['description']} {version} ===")
    subprocess.run(cmd, check=True, cwd=ROOT)
    return ROOT / "dist" / (t["exe"] + (".exe" if IS_WINDOWS else ""))


def main():
    wanted = sys.argv[1:] or ["gui", "cli"]
    unknown = [w for w in wanted if w not in TARGETS]
    if unknown:
        sys.exit(f"Unknown target(s): {', '.join(unknown)}. Use: gui, cli (default: both)")

    # app.ico = icon of the .exe files; app_<n>.png = window icons (generated if missing)
    assets = ROOT / "assets"
    needed = [assets / "app.ico", *(assets / f"app_{n}.png" for n in (16, 32, 48, 96, 256))]
    if not all(p.exists() for p in needed):
        subprocess.run([sys.executable, str(assets / "make_icon.py")], check=True)

    results = [build(name, TARGETS[name]) for name in wanted]
    print("\nDone:")
    for r in results:
        print(f"  {r}")


if __name__ == "__main__":
    main()
