# DBDMap

[![CI](https://github.com/erktiky/DBDMap/actions/workflows/ci.yml/badge.svg)](https://github.com/erktiky/DBDMap/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

Automatic map overlay and ReShade preset switcher for **Dead by Daylight**.

DBDMap watches the loading screen, reads the map name with OCR, and the moment it
recognises one it:

- shows that map's callout image as a click-through overlay on top of the game,
- switches ReShade to the preset you've assigned to that realm,
- optionally flips OBS to your "Playing" scene.

Press the reset hotkey (`F7` by default) to clear the overlay and go back to your
default preset.

Works on **Windows** and on **Linux** — Hyprland, Sway, COSMIC, KDE Plasma, GNOME
and anything else X11-based.

---

## Contents

- [Requirements](#requirements)
- [Install](#install)
- [First run](#first-run)
- [Adding maps and presets](#adding-maps-and-presets)
- [Configuration](#configuration)
- [Platform support](#platform-support)
- [Troubleshooting](#troubleshooting)
- [Credits](#credits)

---

## Requirements

- Python 3.9+
- Tesseract OCR (a native program — the installers below handle it)
- ReShade, if you want automatic preset switching (optional)

---

## Install

### Windows

```powershell
git clone https://github.com/erktiky/DBDMap.git
cd DBDMap
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

No Git? [Download the ZIP](https://github.com/erktiky/DBDMap/archive/refs/heads/main.zip),
extract it, then right-click `install.ps1` → **Run with PowerShell**.

The installer pulls in Python and Tesseract via `winget` if they're missing,
creates a virtualenv, and writes a `DBDMap.bat` you can double-click.

> **Set Dead by Daylight to Borderless Window.** Exclusive fullscreen draws over
> every overlay on Windows, DBDMap included.

### Linux

```bash
git clone https://github.com/erktiky/DBDMap.git
cd DBDMap
./install.sh
```

The installer detects your distro and desktop, installs Tesseract plus the right
screenshot tool, creates a virtualenv, and writes a `./dbdmap` launcher.

Then:

```bash
./dbdmap
```

> **Why sudo?** The `keyboard` library reads `/dev/input` directly to catch your
> hotkey while the game has focus, and writes `/dev/uinput` to send ReShade's
> preset shortcuts. Both need root on Linux. The launcher uses `sudo -E` so your
> display environment survives — plain `sudo` will break the overlay.

### Manual install

If you'd rather not use the scripts:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Install Tesseract separately:

| Platform | Command |
| --- | --- |
| Arch | `sudo pacman -S tesseract tesseract-data-eng` |
| Debian/Ubuntu | `sudo apt install tesseract-ocr` |
| Fedora | `sudo dnf install tesseract tesseract-langpack-eng` |
| Windows | `winget install UB-Mannheim.TesseractOCR` |

---

## First run

The first launch asks a few questions and writes `config.ini`:

1. **Which monitor** the game runs on.
2. **Whether to automate ReShade.** If yes, you'll be asked for the full path to
   your `ReShade.ini` (it sits next to the game executable, in
   `Dead by Daylight/DeadByDaylight/Binaries/Win64/`).
3. **Your reset hotkey.**

Your original `ReShade.ini` is backed up to `ReShade.bak` next to it before
anything is rewritten.

After that, launch the game. The console shows what OCR is reading:

```
🔎 MAP DETECTED: BLOOD_LODGE (97%)
⏳ Pausing OCR for 5 seconds...
```

Not sure whether your setup is right? Run the built-in diagnostics:

```bash
./dbdmap --doctor
```

It reports the detected session, OCR engine, monitor layout, which capture
backend was selected, and whether the game window was found.

---

## Adding maps and presets

The `maps/` folder drives everything:

```
maps/
├── AUTOHAVEN_WRECKERS/
│   ├── Autohaven.ini          <- ReShade preset for this realm (optional)
│   ├── BLOOD_LODGE.jpg
│   ├── GAS_HEAVEN.jpg
│   └── WRECKERS'_YARD.jpg
└── COLDWIND_FARM/
    ├── Coldwind.ini
    ├── FRACTURED_COWSHED.jpg
    └── ...
```

- **Sub-folder name** = the realm. Every realm gets its own ReShade shortcut.
- **Image file name** = the map name OCR matches against, so it has to match
  what the loading screen shows (underscores instead of spaces). `.png`, `.jpg`,
  `.jpeg` and `.webp` all work.
- **One `.ini` per folder** = the ReShade preset applied for that realm. Leave it
  out and DBDMap just shows the overlay without touching ReShade.
- `default-preset.ini` in the root is what the reset hotkey returns to.

Add a folder, add images, restart DBDMap. The generated `binds.txt` lists which
key combination ended up assigned to each realm.

### How the ReShade switching works

DBDMap rewrites `PresetShortcutKeys`/`PresetShortcutPaths` in your `ReShade.ini`
so every realm gets a `CTRL/SHIFT/ALT + F-key` combination, then presses that
combination for you when a map is detected. Your reset hotkey is deliberately
excluded from the assignable keys so DBDMap can never trigger its own reset.

---

## Configuration

`config.ini` is created on first run. See
[`config.ini.example`](config.ini.example) for every option with comments.

The ones worth knowing about:

| Setting | Default | What it does |
| --- | --- | --- |
| `reset_hotkey` | `F7` | Clears the overlay, restores the default preset |
| `detection_threshold` | `85` | How close OCR has to be to a known map name (%) |
| `ocr_interval` | `1.0` | Seconds between OCR attempts |
| `minimap_position` | `left` | `left` or `right` edge of the screen |
| `minimap_width` / `_height` | `300` | Overlay size in pixels |
| `minimap_opacity` | `80` | 0–100 |
| `capture_backend` | `auto` | Override screen-capture method (see below) |
| `tesseract_path` | *(empty)* | Point at Tesseract if it's in an unusual place |

### OBS scene switching

Set `enabled = True` under `[OBS]`, put your obs-websocket URL in `websocket`,
and name your scenes. Requires [`obs-cmd`](https://github.com/grigio/obs-cmd) on
`PATH`. DBDMap switches to `scene_playing` on a detected map and to
`scene_waiting` when you hit reset.

---

## Platform support

DBDMap picks a screen-capture method automatically at startup and prints which
one it chose. `--doctor` shows all of them and which are available.

| Environment | Capture | Overlay |
| --- | --- | --- |
| Hyprland / Sway / COSMIC / Wayfire / river | `grim` | Native Wayland |
| KDE Plasma (Wayland) | `spectacle`, then portal | XWayland override-redirect |
| GNOME (Wayland) | GNOME Shell D-Bus → `gnome-screenshot` → portal | XWayland override-redirect |
| Other Wayland compositors | `xdg-desktop-portal` | XWayland override-redirect |
| Any X11 desktop | `mss` | X11 override-redirect |
| Windows 10/11 | `mss` | Layered click-through window |

On Hyprland, DBDMap additionally reads the game's exact window geometry from
`hyprctl`, so the OCR region follows the window even when it isn't fullscreen.
Everywhere else it uses a region derived from your monitor resolution, which is
what a fullscreen game occupies anyway.

**Two Wayland caveats worth knowing about**, both handled automatically:

*Capture.* Direct screen grabbing (`mss`, `pyautogui`) does not work on Wayland —
not even through XWayland, because Xwayland is rootless and the X root window
contains no window pixels, so a grab of it returns solid black. DBDMap asks the
compositor instead, and refuses any backend that hands back a blank image.

*Stacking.* GNOME and KDE have no protocol letting a normal Wayland window stay
above a fullscreen game. DBDMap runs its overlay through XWayland as an
override-redirect window, which those compositors do stack on top. Dead by
Daylight itself already runs under XWayland via Proton, so this costs nothing.

*Privileges.* DBDMap needs root for `/dev/input`, but D-Bus authenticates by
peer UID and rejects a root client on your session bus — which would break every
capture route that goes through it (portal, GNOME Shell, Spectacle). DBDMap
drops back to the invoking user with `runuser` for exactly those calls. `grim`
is unaffected: it talks to the Wayland socket directly, which is why Hyprland
never needed any of this.

---

## Troubleshooting

**`tesseract executable not found`**
Install it (see the table above), or set `tesseract_path` under `[Advanced]` in
`config.ini`.

**Nothing is ever detected / `❌ No text detected`**
The capture region is derived from your monitor resolution, so check that
`resolution` in `config.ini` is the *physical* resolution of the screen the game
is on. On a scaled display that's the full pixel count, not the scaled-down
number. Run `--doctor` to see what DBDMap thinks your monitors are.

**Wrong map detected**
Raise `detection_threshold`. If a map is *never* detected, lower it — OCR on
stylised fonts sits in the high 80s more often than you'd think.

**Overlay is invisible on Windows**
Switch the game to Borderless Window. Exclusive fullscreen bypasses the
compositor entirely.

**Overlay is behind the game on GNOME/KDE Wayland**
Make sure XWayland is installed and running (`echo $DISPLAY` should print
something). Without it there's no way to stay on top.

**Hotkey stops working mid-session**
It shouldn't — DBDMap watches its own keyboard listener and rebuilds it if a
device disappears (which is what Steam Input re-enumeration used to cause). If
it still happens, the console will say so; please open an issue.

**`Display variables are missing`**
You used `sudo` without `-E`. Use the `./dbdmap` launcher, or
`sudo -E python dbdmap.py`.

---

## Credits

- Map callout images are from [Hens's website](https://hens333.com/callouts).
- OCR by [Tesseract](https://github.com/tesseract-ocr/tesseract).

The code in this repository is MIT licensed. The map images are **not** — they
belong to their original author and are included here for convenience only.
