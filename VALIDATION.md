# Build validation — v1.1.0, 9 October 2026

Passed:

- 18 automated checks for offline initial rendering, failed downloads retaining
  valid caches, HTTP-200 HTML rejection, frozen-image staleness, stale-cache
  labelling, damaged cache content and missing metadata, bounded output
  generations, download limits, old/current NOAA Kp JSON compatibility, and
  null-value fallback for solar-wind observations, current NOAA summary field
  names, configuration migration preserving user identity/settings, and UAF
  embedded HTTP image links being upgraded to HTTPS, four-image page ordering,
  ambiguous-page rejection, an individual camera failure preserving its cache
  while the other cameras update, and idempotent grid configuration migration.
- Installer and uninstaller Bash syntax checks.
- Extension JavaScript module syntax check with Node (syntax only, not GJS).
- 1920×1080 offline dashboard rendered, decoded and visually inspected.

- Current NOAA speed/Bz summary products retrieved through source lookup and
  their returned object formats exercised in parser tests.

Not verified here:

- Direct downloads from the updater runtime and UAF discovery. The UAF response
  here reports a certificate-chain error at the gateway. NOAA endpoints were
  verified by source lookup; the old two-hour endpoints return 404.
- Installing/running the extension in this build environment. The user reports
  successful lockscreen operation, OVATION and Kp on their Ubuntu computer.
- Timer execution, image refresh while locked, and suspend/resume on real hardware.

Before unattended use on the lab computer, follow README setup and verify:

1. Each provider's image/measurement matches its intended product.
2. Super+L shows the dashboard clearly; password unlock still works normally.
3. A manual `systemctl --user start lab-lockscreen.service` updates the screen.
4. The next timer run refreshes the image while locked.
5. `--offline` retains imagery and shows cached/stale status as appropriate.
6. Suspend/resume preserves the display and the timer subsequently runs again.

GNOME integration is provided as a local extension restricted to Shell 46;
it uses private presentation methods and needs review for future GNOME versions.

The requested asc_grid.html page also returns a gateway certificate-chain error
here. Its live image URLs and station labels are intentionally not guessed.
