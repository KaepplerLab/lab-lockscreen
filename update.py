#!/usr/bin/env python3
"""Fetch bounded, validated products and publish a local GNOME dashboard.

No network dependency at display time. Cache entries bundle bytes and metadata
in one atomic JSON write. Sources fail independently; only a complete PNG may
replace the visible dashboard. Requires Ubuntu's python3-pil, no pip packages.
"""
import argparse
import base64
import fcntl
import hashlib
import io
import json
import logging
import math
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import tomllib
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit, unquote
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw, ImageFont, ImageOps

LOG = logging.getLogger("lab-lockscreen")
UTC = timezone.utc
Image.MAX_IMAGE_PIXELS = 24_000_000


def timestamp(value):
    """Parse SWPC UTC strings and HTTP dates, rejecting implausible futures."""
    if not value:
        return None
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        result = result.replace(tzinfo=UTC) if result.tzinfo is None else result
    except ValueError:
        try:
            result = parsedate_to_datetime(str(value))
        except (ValueError, TypeError):
            return None
    seconds = result.timestamp()
    return seconds if seconds <= time.time() + 300 else None


def utc_label(seconds):
    if seconds is None:
        return "unknown"
    return datetime.fromtimestamp(seconds, UTC).strftime("%d %b %Y %H:%M UTC")


def atomic_write(path, data):
    """Temp file and destination share a filesystem; fsync before rename."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".new-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def decode_image(data):
    with Image.open(io.BytesIO(data)) as image:
        if image.format not in ("PNG", "JPEG"):
            raise ValueError("Expected PNG or JPEG")
        if min(image.size) < 100 or image.width * image.height > 24_000_000:
            raise ValueError("Implausible image dimensions")
        image.verify()
    with Image.open(io.BytesIO(data)) as image:
        return image.convert("RGB")


def read_cache(path):
    try:
        entry = json.loads(path.read_text())
        data = base64.b64decode(entry["data"], validate=True)
        if hashlib.sha256(data).hexdigest() != entry["hash"]:
            raise ValueError("Cache checksum mismatch")
        for key in ("checked_at", "changed_at"):
            if not isinstance(entry[key], (int, float)) or not math.isfinite(entry[key]):
                raise ValueError("Invalid cache timestamp")
        observed = entry["observed_at"]
        if observed is not None and (not isinstance(observed, (int, float)) or not math.isfinite(observed)):
            raise ValueError("Invalid source timestamp")
        return entry, data
    except (OSError, ValueError, KeyError, TypeError):
        return None, None


class PageLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.images = []
        self.links = []
        self.anchor = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "img" and attrs.get("src"):
            self.images.append((attrs["src"], attrs.get("alt", "")))
        if tag == "a" and attrs.get("href"):
            self.anchor = [attrs["href"], ""]

    def handle_data(self, data):
        if self.anchor is not None:
            self.anchor[1] += data

    def handle_endtag(self, tag):
        if tag == "a" and self.anchor is not None:
            self.links.append(tuple(self.anchor))
            self.anchor = None


class Fetcher:
    def __init__(self, settings):
        self.timeout = settings["timeout_seconds"]
        self.attempts = settings["attempts"]
        self.limit = settings["max_download_mb"] * 1024 * 1024

    def get(self, url, allow_http=False):
        scheme = urlsplit(url).scheme
        if scheme != "https" and not (allow_http and scheme == "http"):
            raise ValueError("Sources must use HTTPS (UAF HTTP requires sources.uaf.allow_http=true)")
        for attempt in range(self.attempts):
            try:
                request = Request(url, headers={"User-Agent": "LabLockscreen/1.0", "Cache-Control": "no-cache"})
                with urlopen(request, timeout=self.timeout) as response:
                    data = response.read(self.limit + 1)
                    if len(data) > self.limit:
                        raise ValueError("Download exceeds configured limit")
                    return data, timestamp(response.headers.get("Last-Modified"))
            except (OSError, ValueError) as error:
                if attempt + 1 == self.attempts:
                    raise
                LOG.warning("Download retry for %s: %s", url, error)
                time.sleep(1)

    def uaf(self, config):
        allow_http = config.get("allow_http", False)

        def source_url(base, href):
            url = urljoin(base, href)
            # Many older UAF pages embed absolute HTTP image URLs. Upgrade
            # these to HTTPS unless the user explicitly selects HTTP support.
            parts = urlsplit(url)
            if parts.scheme == "http" and not allow_http:
                url = urlunsplit(parts._replace(scheme="https"))
            return url

        if config["url"]:
            url = source_url(config["page_url"], config["url"])
            data, modified = self.get(url, allow_http=allow_http)
            decode_image(data)
            return data, modified, url
        # Bounded discovery: landing page plus at most two relevant subpages.
        page = config["page_url"]
        terms = config["match_terms"]

        def score(item):
            text = " ".join(item).lower()
            return sum(term.lower() in text for term in terms)

        data, _ = self.get(page, allow_http=allow_http)
        parser = PageLinks()
        parser.feed(data.decode("utf-8", errors="replace"))
        candidates = [(source_url(page, src), alt) for src, alt in parser.images if score((src, alt))]
        links = sorted((link for link in parser.links if score(link)), key=score, reverse=True)
        for href, _ in links[:2]:
            target = source_url(page, href)
            if urlsplit(target).netloc != urlsplit(page).netloc:
                continue
            if urlsplit(target).path.lower().endswith((".png", ".jpg", ".jpeg")):
                candidates.append((target, href))
                continue
            try:
                html, _ = self.get(target, allow_http=allow_http)
                child = PageLinks()
                child.feed(html.decode("utf-8", errors="replace"))
                candidates.extend((source_url(target, src), alt + " " + href) for src, alt in child.images)
            except (OSError, ValueError):
                LOG.warning("UAF subpage unavailable")
        for url, _ in sorted(candidates, key=score, reverse=True)[:3]:
            if urlsplit(url).netloc != urlsplit(page).netloc:
                continue
            try:
                data, modified = self.get(url, allow_http=allow_http)
                decode_image(data)
                return data, modified, url
            except (OSError, ValueError):
                LOG.warning("UAF image candidate unavailable")
        raise ValueError("No valid UAF image found; configure sources.uaf.url")


def grid_images(html, page_url, allow_http=False):
    """Resolve exactly four camera images in document order, no link crawling."""
    parser = PageLinks()
    parser.feed(html.decode("utf-8", errors="replace"))
    images = []
    seen = set()
    for src, alt in parser.images:
        text = (src + " " + alt).lower()
        if any(word in text for word in ("logo", "favicon", "spacer", "button", "icon")):
            continue
        url = urljoin(page_url, src)
        parts = urlsplit(url)
        if parts.scheme == "http" and not allow_http:
            url = urlunsplit(parts._replace(scheme="https"))
        if url in seen:
            continue
        seen.add(url)
        label = alt.strip() or unquote(Path(parts.path).name)
        images.append({"url": url, "label": label or f"Camera {len(images) + 1}"})
    if len(images) != 4:
        raise ValueError(f"Expected four camera images on asc_grid page; found {len(images)}. Set sources.uaf.image_urls to four direct image URLs.")
    return images


def update_grid(config, fetcher, cache, now, offline):
    """One page lookup, four independent source downloads and caches."""
    manifest_path = cache / "uaf-grid-links.json"
    manifest = None
    try:
        saved = json.loads(manifest_path.read_text())
        if saved["page_url"] == config["page_url"] and len(saved["images"]) == 4:
            if all(isinstance(item["url"], str) and isinstance(item["label"], str) for item in saved["images"]):
                manifest = saved["images"]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    discovery_error = None
    overrides = config.get("image_urls", [])
    labels = config.get("image_labels", [])
    if overrides:
        manifest = [{"url": url, "label": labels[i] if labels else f"Camera {i + 1}"} for i, url in enumerate(overrides)]
    elif not offline:
        try:
            html, _ = fetcher.get(config["page_url"], allow_http=config.get("allow_http", False))
            manifest = grid_images(html, config["page_url"], config.get("allow_http", False))
            atomic_write(manifest_path, json.dumps({"page_url": config["page_url"], "images": manifest}).encode())
        except Exception as exc:
            discovery_error = str(exc)
            LOG.warning("uaf grid page: %s", exc)

    def camera(index):
        settings = dict(config)
        selected = manifest[index] if manifest else None
        # No discovery guess when the grid page is unavailable. Render cache.
        settings["url"] = selected["url"] if selected else ""
        result = update_source(f"uaf_{index + 1}", settings, fetcher, cache, now,
                               offline or selected is None)
        result["label"] = labels[index] if labels else (selected["label"] if selected else f"Camera {index + 1}")
        if discovery_error:
            result["discovery_error"] = discovery_error
            if not selected:
                result["error"] = discovery_error
        return f"uaf_{index + 1}", result

    with ThreadPoolExecutor(max_workers=4) as pool:
        return dict(pool.map(camera, range(4)))


def is_image_source(name):
    return name == "ovation" or name == "uaf" or name.startswith("uaf_")


def metric(data, name):
    """Accept both pre-2026 header tables and current object-list JSON."""
    rows = json.loads(data)
    if isinstance(rows, dict):
        rows = [rows]
    if not isinstance(rows, list) or not rows:
        raise ValueError("Empty or invalid product")
    if isinstance(rows[0], list):
        header = rows[0]
        rows = [dict(zip(header, row)) for row in rows[1:] if isinstance(row, list)]
    fields = {"kp": ("kp",), "speed": ("proton_speed", "speed"), "bz": ("bz_gsm", "bz")}[name]
    valid = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        row = {key.lower(): value for key, value in row.items()}
        observed = timestamp(row.get("time_tag"))
        for field in fields:
            try:
                value = float(row[field])
                bounds = {"kp": (0, 9), "speed": (0, 3000), "bz": (-500, 500)}[name]
                if observed is not None and math.isfinite(value) and bounds[0] <= value <= bounds[1]:
                    valid.append((observed, value))
                    break
            except (KeyError, ValueError, TypeError):
                continue
    if not valid:
        raise ValueError("No valid timestamped measurements")
    observed, value = max(valid)
    return value, observed


def update_source(name, config, fetcher, cache, now, offline):
    entry, data = read_cache(cache / (name + ".json"))
    error = None
    if not offline:
        try:
            url = config["url"]
            if name == "uaf" or name.startswith("uaf_"):
                fresh, modified, url = fetcher.uaf(config)
            else:
                fresh, modified = fetcher.get(url)
            if is_image_source(name):
                decode_image(fresh)
                observed = modified
            else:
                _, observed = metric(fresh, name)
            digest = hashlib.sha256(fresh).hexdigest()
            changed_at = entry["changed_at"] if entry and entry["hash"] == digest else now
            new_entry = {"data": base64.b64encode(fresh).decode(), "hash": digest,
                         "checked_at": now, "changed_at": changed_at,
                         "observed_at": observed, "url": url}
            atomic_write(cache / (name + ".json"), json.dumps(new_entry).encode())
            entry, data = new_entry, fresh
        except Exception as exc:
            # Isolate provider failures, including malformed/corrupt images.
            error = str(exc)
            LOG.warning("%s: %s; retaining cache", name, error)
    else:
        error = "Network skipped (--offline)"
    image = None
    value = None
    try:
        if data is not None:
            if is_image_source(name):
                image = decode_image(data)
            else:
                value, _ = metric(data, name)
    except Exception:
        entry = None
        error = "Invalid cache"
    if entry:
        # For imagery, changed_at detects a frozen feed even if HTTP dates reset.
        age_from = entry["observed_at"] or entry["changed_at"]
        if is_image_source(name):
            age_from = min(age_from, entry["changed_at"])
        stale = now - age_from > config["stale_after_minutes"] * 60
        state = "STALE" if stale else ("CACHED" if error else "AVAILABLE")
    else:
        state = "UNAVAILABLE"
    return {"entry": entry, "image": image, "value": value, "state": state, "error": error}


def font(size, bold=False):
    suffix = "-Bold" if bold else ""
    return ImageFont.truetype(f"/usr/share/fonts/truetype/dejavu/DejaVuSans{suffix}.ttf", size)


def fit_text(draw, text, x, y, size, width, color, bold=False):
    text = str(text)
    chosen = font(size, bold)
    while draw.textlength(text, font=chosen) > width and size > 12:
        size -= 1
        chosen = font(size, bold)
    while draw.textlength(text, font=chosen) > width and len(text) > 2:
        text = text[:-2] + "…"
    draw.text((x, y), text, font=chosen, fill=color)


def compose(config, sources, now):
    # Compose at 1920x1080 and scale once. Panels flank the reserved clock area.
    canvas = Image.new("RGB", (1920, 1080), "#09121f")
    draw = ImageDraw.Draw(canvas)
    pale, muted, accent = "#e7f0fa", "#9baec4", "#72d6d0"
    identity = config["identity"]
    fit_text(draw, identity["laboratory_name"], 64, 35, 32, 1280, pale, True)
    fit_text(draw, identity["institution_name"], 64, 80, 23, 1280, muted)
    computer = identity["computer_name"] or socket.gethostname()
    fit_text(draw, computer, 1400, 45, 22, 456, accent, True)
    fit_text(draw, config["display"]["title"], 64, 150, 20, 1200, accent, True)
    fit_text(draw, "Generated: " + utc_label(now), 1330, 150, 17, 525, muted)
    for name, x in (("uaf", 64), ("ovation", 1200)):
        width, y = 656, 238
        draw.rounded_rectangle((x, y, x + width, 878), radius=18, fill="#111f30", outline="#29435b", width=2)
        cfg = config["sources"][name]
        fit_text(draw, cfg["label"], x + 20, y + 19, 19, width - 40, pale, True)
        if name == "uaf" and cfg.get("mode") == "grid":
            draw_grid(canvas, draw, sources, x, y, pale, muted, accent)
            continue
        source = sources.get(name)
        if source and source["image"]:
            image = ImageOps.contain(source["image"], (width - 32, 488))
            canvas.paste(image, (x + (width - image.width) // 2, y + 72 + (488 - image.height) // 2))
        else:
            fit_text(draw, "No image available" if cfg["enabled"] else "Source disabled", x + 50, y + 240, 27, width - 100, muted)
        if source:
            state = source["state"]
            color = accent if state == "AVAILABLE" else "#f7bd76"
            fit_text(draw, state, x + 20, y + 563, 18, width - 40, color, True)
            entry = source["entry"]
            if entry:
                # Do not mislabel fetch time as camera acquisition time.
                detail = "Source file: " + utc_label(entry["observed_at"]) if entry["observed_at"] else "First seen unchanged: " + utc_label(entry["changed_at"])
                fit_text(draw, detail, x + 20, y + 591, 16, width - 40, muted)
                fit_text(draw, "Last fetch: " + utc_label(entry["checked_at"]), x + 20, y + 614, 15, width - 40, muted)
    draw.line((64, 920, 1856, 920), fill="#29435b", width=2)
    specs = (("kp", "Planetary Kp", "", 64), ("speed", "Solar wind", " km/s", 675), ("bz", "IMF Bz (GSM)", " nT", 1286))
    for name, label, units, x in specs:
        source = sources.get(name)
        value = "—" if not source or source["value"] is None else f"{source['value']:.1f}{units}"
        fit_text(draw, label + ": " + value, x, 942, 26, 560, pale, True)
        if source:
            entry = source["entry"]
            observed = utc_label(entry["observed_at"]) if entry else "no data"
            fit_text(draw, source["state"] + " • " + observed, x, 984, 16, 560, muted)
    failed = sum(bool(s["error"]) for s in sources.values())
    status = "Source fetch failures: using cached data where available" if failed else "Local dashboard • data courtesy NOAA SWPC and UAF Geophysical Institute"
    fit_text(draw, status, 64, 1035, 16, 1792, muted)
    size = (config["display"]["width"], config["display"]["height"])
    return canvas.resize(size, Image.Resampling.LANCZOS)


def draw_grid(canvas, draw, sources, x, y, pale, muted, accent):
    """Four intact camera frames, matching the web page's reading order."""
    for index in range(4):
        source = sources.get(f"uaf_{index + 1}")
        left = x + 16 + (index % 2) * 316
        top = y + 62 + (index // 2) * 282
        draw.rounded_rectangle((left, top, left + 308, top + 272), radius=8, fill="#09121f")
        label = source.get("label", f"Camera {index + 1}") if source else f"Camera {index + 1}"
        fit_text(draw, label, left + 8, top + 6, 15, 292, pale, True)
        if source and source["image"]:
            image = ImageOps.contain(source["image"], (292, 190))
            canvas.paste(image, (left + (308 - image.width) // 2, top + 32 + (190 - image.height) // 2))
        else:
            fit_text(draw, "No image available", left + 18, top + 105, 17, 272, muted)
        state = source["state"] if source else "DISABLED"
        color = accent if state == "AVAILABLE" else "#f7bd76"
        fit_text(draw, state, left + 8, top + 224, 14, 292, color, True)
        entry = source["entry"] if source else None
        if entry:
            seconds = entry["observed_at"] or entry["changed_at"]
            prefix = "File: " if entry["observed_at"] else "Seen: "
            fit_text(draw, prefix + utc_label(seconds), left + 8, top + 248, 12, 292, muted)


def publish(image, state, apply):
    output = io.BytesIO()
    image.save(output, format="PNG")
    data = output.getvalue()
    decode_image(data)
    # A unique URI avoids stale textures in GNOME's background/CSS caches.
    target = state / (f"dashboard-{time.time_ns()}.png")
    atomic_write(target, data)
    atomic_write(state / "current.png", data)
    if apply:
        uri = target.as_uri()

        subprocess.run([
            "gsettings", "set",
            "org.gnome.desktop.screensaver",
            "picture-uri", uri
        ], check=True)

        for key in ("picture-uri", "picture-uri-dark"):
            subprocess.run([
                "gsettings", "set",
                "org.gnome.desktop.background",
                key, uri
            ], check=True)
    # Keep the newest two generations, plus the stable current.png inspection copy.
    generations = sorted(state.glob("dashboard-*.png"), key=lambda path: path.stat().st_mtime_ns, reverse=True)
    for old in generations[2:]:
        old.unlink(missing_ok=True)


def load_config(path):
    with open(path, "rb") as handle:
        config = tomllib.load(handle)
    display, update = config["display"], config["update"]
    if not 800 <= display["width"] <= 7680 or not 600 <= display["height"] <= 4320:
        raise ValueError("Unsupported output size")
    if display["width"] * display["height"] > 24_000_000:
        raise ValueError("Output must contain at most 24 million pixels")
    if not 1 <= update["attempts"] <= 3 or not 1 <= update["timeout_seconds"] <= 30:
        raise ValueError("Use 1–3 attempts and a 1–30 second timeout")
    if not 1 <= update["max_download_mb"] <= 24 or not 60 <= update["interval_seconds"] <= 86400:
        raise ValueError("Invalid download limit or update interval")
    for name in ("ovation", "uaf", "kp", "speed", "bz"):
        if config["sources"][name]["stale_after_minutes"] <= 0:
            raise ValueError("Stale age must be positive")
    uaf = config["sources"]["uaf"]
    if uaf.get("mode", "single") not in ("single", "grid"):
        raise ValueError("UAF mode must be single or grid")
    for key in ("image_urls", "image_labels"):
        values = uaf.get(key, [])
        if not isinstance(values, list) or len(values) not in (0, 4) or any(not isinstance(value, str) or not value for value in values):
            raise ValueError(f"sources.uaf.{key} must be empty or contain exactly four nonempty strings")
    return config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path.home() / ".config/lab-lockscreen/config.toml")
    parser.add_argument("--state-dir", type=Path, default=Path.home() / ".local/share/lab-lockscreen")
    parser.add_argument("--offline", action="store_true", help="Render cache without network calls")
    parser.add_argument("--no-apply", action="store_true", help="Write images without changing GNOME")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    config = load_config(args.config)
    state = args.state_dir.expanduser().resolve()
    state.mkdir(parents=True, exist_ok=True)
    with (state / "update.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            LOG.info("Another updater is running")
            return
        now = time.time()
        cache = state / "cache"
        cache.mkdir(exist_ok=True)
        fetcher = Fetcher(config["update"])
        sources = {}
        for name, settings in config["sources"].items():
            if settings["enabled"]:
                if name == "uaf" and settings.get("mode") == "grid":
                    sources.update(update_grid(settings, fetcher, cache, now, args.offline))
                else:
                    sources[name] = update_source(name, settings, fetcher, cache, now, args.offline)
        image = compose(config, sources, now)
        publish(image, state, not args.no_apply)
        report = {name: {"state": value["state"], "error": value["error"],
                         "label": value.get("label"), "grid_page_error": value.get("discovery_error"),
                         "last_fetch": value["entry"]["checked_at"] if value["entry"] else None}
                  for name, value in sources.items()}
        atomic_write(state / "status.json", json.dumps(report, indent=2).encode())
        LOG.info("Published %s", state / "current.png")


if __name__ == "__main__":
    main()
