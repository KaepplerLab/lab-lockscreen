#!/usr/bin/env python3
"""Migrate only the two removed v1.0 NOAA URLs; preserve user settings."""
from pathlib import Path
import sys
import tomllib
import re


def migrate(path):
    text = path.read_text()
    replacements = {
        "https://services.swpc.noaa.gov/products/solar-wind/plasma-2-hour.json":
        "https://services.swpc.noaa.gov/products/summary/solar-wind-speed.json",
        "https://services.swpc.noaa.gov/products/solar-wind/mag-2-hour.json":
        "https://services.swpc.noaa.gov/products/summary/solar-wind-mag-field.json",
    }
    revised = text
    for old, new in replacements.items():
        revised = revised.replace(old, new)
    tomllib.loads(revised)
    if revised != text:
        # Keep the user's original labels, identity, comments and custom URLs.
        backup = path.with_name(path.name + ".before-v1.0.1")
        if not backup.exists():
            backup.write_text(text)
        from update import atomic_write
        atomic_write(path, revised.encode())
        print("Migrated removed NOAA endpoints; previous configuration backed up.")


def migrate_grid(path):
    text = path.read_text()
    parsed = tomllib.loads(text)
    # Already-migrated grid or an explicit single-mode preference is retained.
    if "mode" in parsed.get("sources", {}).get("uaf", {}):
        return
    match = re.search(r"(?m)^\[sources\.uaf\][^\n]*\n", text)
    if not match:
        raise ValueError("Missing sources.uaf configuration section")
    start = match.end()
    following = re.search(r"(?m)^\[", text[start:])
    end = start + following.start() if following else len(text)
    section = text[start:end]
    replacements = {"label": '"UAF / GI — FOUR ALL-SKY CAMERAS"',
                    "url": '""', "page_url": '"https://optics.gi.alaska.edu/asc_grid.html"'}
    for key, value in replacements.items():
        section, count = re.subn(rf"(?m)^{key}\s*=.*$", f"{key} = {value}", section)
        if not count:
            section += f"\n{key} = {value}\n"
    section += '\n# Four cameras in asc_grid.html page order. Optional direct overrides.\nmode = "grid"\nimage_urls = []\nimage_labels = []\n\n'
    revised = text[:start] + section + text[end:]
    tomllib.loads(revised)
    backup = path.with_name(path.name + ".before-v1.1.0")
    if not backup.exists():
        backup.write_text(text)
    from update import atomic_write
    atomic_write(path, revised.encode())
    print("Enabled the four-camera UAF grid; previous configuration backed up.")


if __name__ == "__main__":
    migrate(Path(sys.argv[1]))
    migrate_grid(Path(sys.argv[1]))
