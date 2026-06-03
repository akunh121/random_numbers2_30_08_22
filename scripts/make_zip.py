#!/usr/bin/env python3
"""Bundle the Windows local-updater scripts into a distributable zip.

Run from the repo root:

    python3 scripts/make_zip.py

Writes lotto-updater-windows.zip with CRLF line endings on text files so
they open cleanly in Notepad / Task Scheduler on Windows. The zip itself
is intentionally gitignored — regenerate it whenever the scripts change.
"""
from __future__ import annotations

import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / 'scripts'
OUT = REPO / 'lotto-updater-windows.zip'

FILES = [
    'setup.bat',
    'uninstall.bat',
    'update_local.bat',
    'update_test.bat',
    'update_local.py',
    'README_LOCAL_UPDATER.md',
    'QUICKSTART.txt',
]
WINDOWS_TEXT_SUFFIXES = {'.bat', '.txt', '.md', '.json', '.py'}


def main() -> None:
    with zipfile.ZipFile(OUT, 'w', zipfile.ZIP_DEFLATED) as zf:
        for name in FILES:
            path = SRC / name
            data = path.read_bytes()
            if path.suffix in WINDOWS_TEXT_SUFFIXES:
                data = data.replace(b'\r\n', b'\n').replace(b'\n', b'\r\n')
            zf.writestr(f'lotto-updater/{name}', data)
    print(f'Wrote {OUT} ({OUT.stat().st_size:,} bytes)')


if __name__ == '__main__':
    main()
