"""Phone-width check: screenshot pages at 390x844 in headless Edge/Chrome.

    python tools/phone_check.py http://localhost:5173/apps/campus-alert/go https://<tunnel>/apps/campus-alert/go

Headless browsers clamp the window to ~480 px, so a plain --window-size=390 screenshot only crops a
wider layout. This puts each page in a 390x844 iframe instead, which gives a true phone-width layout.
Screenshots go to data/phone-check/ (gitignored). Look at them: no sideways scroll, readable text,
buttons reachable with a thumb.
"""

from __future__ import annotations

import argparse
import html
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "phone-check"
CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
]


def browser() -> str:
    for c in CANDIDATES:
        if Path(c).exists():
            return c
    for name in ("msedge", "google-chrome", "chromium", "chromium-browser", "chrome"):
        if found := shutil.which(name):
            return found
    sys.exit("phone_check: no Edge/Chrome found")


def shoot(exe: str, url: str, out: Path, width: int, height: int, wait_ms: int) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        frame = Path(tmp) / "frame.html"
        frame.write_text(
            '<!doctype html><meta charset="utf-8"><body style="margin:0;background:#888">'
            f'<iframe src="{html.escape(url, quote=True)}" allow="geolocation" '
            f'style="width:{width}px;height:{height}px;border:0;background:#fff">'
            "</iframe>",
            encoding="utf-8",
        )
        subprocess.run(
            [
                exe,
                "--headless=new",
                "--disable-gpu",
                "--hide-scrollbars",
                f"--window-size={width + 20},{height + 20}",
                "--force-device-scale-factor=1",
                f"--virtual-time-budget={wait_ms}",
                f"--user-data-dir={Path(tmp) / 'profile'}",
                f"--screenshot={out}",
                frame.as_uri(),
            ],
            check=True,
            capture_output=True,
            timeout=120,
        )
        # On Windows the launcher can return before the headless child writes the file: wait for it
        # (the frame page and profile in `tmp` must outlive the browser).
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and not (out.exists() and out.stat().st_size > 0):
            time.sleep(0.5)
        time.sleep(1.0)
    if not out.exists():
        sys.exit(f"phone_check: no screenshot for {url}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("urls", nargs="+")
    ap.add_argument("--width", type=int, default=390)
    ap.add_argument("--height", type=int, default=844)
    ap.add_argument("--wait-ms", type=int, default=20000, help="time for the page to load, retry and fetch data")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    exe = browser()
    for i, url in enumerate(args.urls, 1):
        out = OUT / f"phone-{i}.png"
        out.unlink(missing_ok=True)
        shoot(exe, url, out, args.width, args.height, args.wait_ms)
        print(f"{url} -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
