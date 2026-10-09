# Lab Lockscreen 1.1.0

An Ubuntu 24.04 desktop package for GNOME Shell 46. It combines the four UAF images from `asc_grid.html`
in a 2×2 grid, NOAA SWPC northern OVATION, planetary Kp, solar-wind speed and
GSM Bz into a local PNG. A small bundled extension shows the image clearly on
the user's lock screen. It retains GNOME's clock, password prompt and normal
authentication. No continuously running browser or video player is required.

## Install on the lab computer

Run in a terminal inside the **lab user's logged-in GNOME desktop session**.
Do not run the installer with sudo or from a root account. This installs for
that desktop user, not for every account and not for the GDM login screen.

```bash
sudo apt update
sudo apt install python3-pil fonts-dejavu-core
tar -xzf lab-lockscreen-ubuntu24-v1.1.0.tar.gz
cd lab-lockscreen
nano config.toml
bash install.sh
```

Log out and back in once so GNOME discovers the extension, then run:

```bash
gnome-extensions enable lab-lockscreen@local
```

Lock with Super+L. On Ubuntu's default Wayland session, logging out/in is
required; Alt+F2 `r` does not restart GNOME there. Disable other lock-screen
background extensions before enabling this one to avoid conflicting overrides.

## Your lab, institution and computer

Edit these clearly marked fields at the top of `config.toml` before installation:

```toml
[identity]
laboratory_name = "Space Physics Laboratory"
institution_name = "Your Institution"
computer_name = "Lab Workstation 01"
```

These are examples, not assigned identities. An empty computer name uses the
hostname. After installation, edit the working configuration here:

```bash
nano ~/.config/lab-lockscreen/config.toml
systemctl --user start lab-lockscreen.service
```

Re-running `bash install.sh` preserves the installed config and the original
lock-screen setting. To change the update interval, edit
`update.interval_seconds` in the installed config and re-run the installer;
this regenerates the timer. Other edits apply on the next update.

### Upgrade from 1.0 / 1.0.1

Extract the v1.1.0 archive and run `bash install.sh` from the extracted folder.
The installer keeps your laboratory, institution and computer names. It switches
an older single-camera UAF configuration to the requested four-camera grid at
`https://optics.gi.alaska.edu/asc_grid.html`, backing it up as
`config.toml.before-v1.1.0`. Already configured grid settings are retained on
later installations. Any still-obsolete NOAA endpoints are also migrated.
The extension is unchanged; an enabled installation needs no logout.

The NOAA fix from v1.0.1 remains: current summary endpoints, including
`proton_speed`, replace the removed two-hour solar-wind products.

## Display

The default output is 1920×1080. Set `display.width` and `display.height` to
3840 and 2160 for a 4K display. The layout is designed for 16:9: two image
panels sit to either side of an intentionally empty center for GNOME's clock
and unlock prompt. Other aspect ratios rescale the same layout. The extension
uses `contain` so products remain intact; each monitor shows the same dashboard
and may have margins. GNOME notifications or differently sized prompts can
cover part of the image. This is a lock-screen dashboard, not a kiosk mode.

## Four UAF camera images

The updater fetches `https://optics.gi.alaska.edu/asc_grid.html` once per run,
resolves its four image elements in document order, and arranges them as
upper-left, upper-right, lower-left and lower-right. Obvious logo/icon elements
are excluded. It requires exactly four remaining images instead of choosing
arbitrary unrelated products. No Poker Flat-only link search is used in grid
mode. Each camera gets an independent last-good cache, freshness label and
source file/first-seen timestamp. Successful cameras continue updating when
another fails. Last resolved image URLs are retained for page outages.

The build environment still cannot access this page because its upstream
certificate chain produces a gateway error. Therefore **the live HTML and four
camera downloads have not been verified here**. The parser and independent
cache handling were tested against representative HTML and simulated failures.

If the page structure does not use four ordinary image elements, you can set
four direct current image addresses and optional labels in the installed config:

```toml
[sources.uaf]
mode = "grid"
page_url = "https://optics.gi.alaska.edu/asc_grid.html"
image_urls = ["https://IMAGE-1", "https://IMAGE-2", "https://IMAGE-3", "https://IMAGE-4"]
image_labels = ["Station 1", "Station 2", "Station 3", "Station 4"]
```

Replace the existing values inside the section; do not add a duplicate section.
Keep `image_urls = []` for automatic selection. Empty `image_labels` uses each
image's alt text or filename. The generic labels above are examples, not station
identifications. `url` and `match_terms` apply only to optional `mode = "single"`.

For camera errors:

```bash
cat ~/.local/share/lab-lockscreen/status.json
journalctl --user -u lab-lockscreen.service -n 50 --no-pager
```

Status entries `uaf_1` through `uaf_4` identify individual failures, including any
page-discovery error. Older embedded HTTP image URLs are upgraded to HTTPS by
default. If the lab computer can access UAF only over HTTP, explicitly set
`allow_http = true` and use an HTTP `page_url` or direct image addresses. This
setting applies to UAF fetches only; certificate verification is never disabled.

## What the status labels mean

| Label | Meaning |
|---|---|
| AVAILABLE | Latest fetch passed validation and the product has not exceeded its age limit. |
| CACHED | Fetch failed or was skipped; a recent cached product remains available. |
| STALE | Source timestamp or unchanged-image age exceeds its configured limit. |
| UNAVAILABLE | There is no valid cached product yet. |

The source **file timestamp** comes from HTTP Last-Modified where available;
it is not claimed to be the camera's exposure time. If absent, the panel shows
when the same content was first seen. Last fetch is displayed separately.
Numeric metrics use their own NOAA observation timestamps. Kp has a six-hour
default age limit because it is a three-hour index. Camera/OVATION and solar
wind limits are separately configurable. A static frame can legitimately be
old during daylight or camera downtime; STALE does not diagnose the cause.

The dashboard never claims that a source failure proves the whole computer is
offline. A provider outage, daytime camera pause and lost connectivity can all
produce similar symptoms. If the user timer or computer itself stops, the PNG
cannot age its labels dynamically: compare its visible Generated time.

## Failure behavior and bounded storage

- Download timeouts, up to three configured attempts, and bounded file sizes.
- Independent source handling: one provider cannot prevent the other updates.
- PNG/JPEG decoding and dimension checks reject HTML error pages and damaged files.
- Validated JSON metrics support NOAA's 2026 object-list format and older header tables.
- Each cache bundles bytes, checksum and timestamps in one atomic write.
- Complete output PNGs use a same-directory atomic replace. A render failure
  leaves the existing screen in place. A first offline run produces labelled
  placeholders, which later fill in when sources become available.
- Unique output names trigger GNOME's image-cache refresh; only two generations
  and `current.png` are kept. Old frames do not accumulate.
- An exclusive lock prevents concurrent manual and timer runs.
- systemd stops an update exceeding four minutes. Interrupting a write cannot
  replace an output with partial bytes; temporary files may remain after a kill.

Cache and output are under `~/.local/share/lab-lockscreen/`. Logs go to the
user journal, whose retention is managed by the lab computer's journal policy;
this package does not change global log retention.

The timer starts after login, runs every five minutes by default, and resumes
after suspend. No requirement for continuous internet and no root service.
It does not update at the GDM login screen before the lab user logs in.
`OnUnitInactiveSec` schedules after the previous run ends and avoids overlap.
No `Persistent=true` claim is made: persistence applies to calendar timers,
not this elapsed-time timer.

## Check operation

```bash
systemctl --user status lab-lockscreen.timer
journalctl --user -u lab-lockscreen.service -n 50 --no-pager
cat ~/.local/share/lab-lockscreen/status.json
gnome-extensions info lab-lockscreen@local
```

Open `~/.local/share/lab-lockscreen/current.png` to check the dashboard independently
of the lock screen. For an intentional offline check without touching GNOME:

```bash
python3 ~/.local/share/lab-lockscreen/update.py --offline --no-apply
```

`--offline` suppresses all network calls; `--no-apply` only suppresses the GNOME
setting change. They still render and publish the local output. Do not use
`--offline` in the timer if you want automatic downloads.

## Remove or recover

```bash
bash uninstall.sh
```

This stops the timer, disables the extension, restores the previous screensaver
URI and retains config/cache. Log out and back in after removal. If a shell
extension causes trouble, disabling `lab-lockscreen@local` and logging back in
restores normal GNOME presentation. No authentication code is modified.

## Validation and limitations

Run included checks with:

```bash
python3 -m unittest discover -s tests -v
```

See `VALIDATION.md` for the build checks. The image pipeline can be tested
without GNOME, but this package has **not been exercised in a live Ubuntu GNOME
46 session** in the build environment. Verify one lock/unlock cycle, one
timer update while locked, suspend/resume, and one offline update on the lab
computer before leaving it unattended. The small local extension uses GNOME
46 private background methods, is restricted to Shell 46, and will need review
before a GNOME major-version upgrade. It is not an extensions.gnome.org release.

Primary source references:

- NOAA imagery: https://services.swpc.noaa.gov/images/animations/ovation/north/
- NOAA products: https://services.swpc.noaa.gov/products/
- NOAA 2026 JSON-format notice:
  https://www.weather.gov/media/notification/pdf_2026/scn26-21_Data_Format_Changes_Impacting_SWPC_Products.pdf
- UAF four-camera page: https://optics.gi.alaska.edu/asc_grid.html
- GNOME background customization reference:
  https://extensions.gnome.org/review/71274

Data courtesy NOAA SWPC and UAF Geophysical Institute. Provider availability,
camera operating hours and timestamps remain outside the updater's control.
