#!/usr/bin/env python3
"""
DBDMap - automatic map overlay and ReShade preset switcher for Dead by Daylight.

Reads the map name from the loading screen with OCR, then shows the matching
callout image as an overlay and switches ReShade to that realm's preset.

Platform-specific behaviour lives in platform_support.py.
"""

import os
import sys

from platform_support import (
    IS_LINUX, IS_WINDOWS, IS_WAYLAND, IS_WLROOTS, HAS_X11,
    HAS_HYPRCTL, HAS_OBSCMD,
    app_dir, init_console, choose_qt_platform, session_summary,
    linux_input_access, setup_tesseract,
    run_cmd, fire_and_forget,
    ScreenCapture, CaptureError, is_blank, probe_all_backends,
    get_monitors as enumerate_monitors, pick_monitor, synthetic_monitor,
    find_dbd_window, apply_overlay_platform_tweaks, raise_overlay, overlay_notes,
)

init_console()

# --- Always run against our own directory (PyInstaller aware) ---
os.chdir(app_dir())

DOCTOR = '--doctor' in sys.argv or '--check' in sys.argv

# --- Verify we can actually see the display and read the keyboard ---
# Only when run as the program: importing this module (tests, tooling) must not
# exit the interpreter.
if IS_LINUX and __name__ == '__main__' and not DOCTOR:
    if not os.environ.get('DISPLAY') and not os.environ.get('WAYLAND_DISPLAY'):
        print("\n❌ CRITICAL: Display variables are missing!")
        print("If you are running as root (sudo), Linux stripped your display access.")
        print("Please run the script using the -E flag to preserve your environment:")
        print("\n    sudo -E python dbdmap.py\n")
        sys.exit(1)

    # The `keyboard` library needs root on Linux: it reads every /dev/input/event*,
    # writes /dev/uinput, and calls `dumpkeys` for the console keymap. Group
    # permissions alone are not enough, so there is no rootless path to offer.
    if os.geteuid() != 0:
        print("\n❌ CRITICAL: The 'keyboard' library needs root to read/write /dev/input.")
        print("Please run:\n\n    sudo -E python dbdmap.py\n")
        print("The -E is required: it keeps WAYLAND_DISPLAY/DISPLAY so the overlay")
        print("and screen capture still work.\n")
        sys.exit(1)

# Suppress annoying PyQt font warnings
os.environ["QT_LOGGING_RULES"] = "qt.qpa.fonts.warning=false"

# Decide the Qt platform plugin before QApplication exists.
_qt_platform = choose_qt_platform()
if _qt_platform:
    os.environ["QT_QPA_PLATFORM"] = _qt_platform

import time
import re
import threading
import traceback
import json
import configparser
import shutil
import unicodedata
from difflib import SequenceMatcher

import psutil
import keyboard
import cv2
import numpy as np
import pytesseract
from PIL import Image, ImageOps

# --- PyQt6 Imports ---
from PyQt6.QtWidgets import QApplication, QLabel, QWidget
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap

# ---------------------------------------------------------
# Windows Ctypes Setup for Keystrokes
# ---------------------------------------------------------
if IS_WINDOWS:
    import ctypes
    user32 = ctypes.windll.user32
    VK_CONTROL, VK_SHIFT, VK_MENU = 0x11, 0x10, 0x12
    F_KEYS = {f"f{i}": 0x6F + i for i in range(1, 13)}

# Nothing external is allowed to block the main loop forever
OCR_TIMEOUT = 15

# Where the map name sits on the loading screen, as a fraction of the game window
MAP_NAME_REGION = (0.042708, 0.814814, 0.46875, 0.038888)


# ---------------------------------------------------------
# Console Manager
# ---------------------------------------------------------
class ConsoleOutput:
    def __init__(self):
        self.last_msg = ""
        self.count = 1

    def log(self, msg):
        if msg == self.last_msg:
            self.count += 1
            sys.stdout.write(f"\r{msg} ({self.count}){' ' * 10}")
        else:
            if self.last_msg: print()
            self.last_msg = msg
            self.count = 1
            sys.stdout.write(f"\r{msg}{' ' * 10}")
        sys.stdout.flush()

    def warn(self, msg):
        """Prints on its own line without breaking the repeat-counter of log()."""
        if self.last_msg: print()
        self.last_msg = ""
        print(msg)


def hyprctl_json(what):
    if not HAS_HYPRCTL: return None
    result = run_cmd(['hyprctl', '-j', what], text=True)
    if not result or result.returncode != 0: return None
    try:
        return json.loads(result.stdout)
    except Exception:
        return None


# ---------------------------------------------------------
# Persistent PyQt6 Overlay
# ---------------------------------------------------------
class PersistentOverlay(QWidget):
    def __init__(self, config):
        super().__init__()
        self.setWindowTitle("Minimap Overlay")

        self.setWindowFlags(
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.Tool |
            # Click-through and never-focused, on every platform Qt supports.
            # Hyprland already got this from its windowrules; the other desktops
            # have no equivalent and would otherwise swallow mouse input.
            Qt.WindowType.WindowTransparentForInput |
            Qt.WindowType.WindowDoesNotAcceptFocus
        )
        if IS_LINUX and not IS_WLROOTS and HAS_X11:
            # Override-redirect: the only way to sit above a fullscreen game on
            # GNOME/KDE, whether the session is X11 or XWayland-under-Wayland.
            self.setWindowFlag(Qt.WindowType.X11BypassWindowManagerHint, True)

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        self.label = QLabel(self)
        self.label.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        self.is_visible = False
        self._pixmap_cache = {}

        if HAS_HYPRCTL:
            op_raw = config['Minimap'].get('minimap_opacity', '80').replace('%', '')
            op_val = float(op_raw) / 100.0
            rules = [
                f"opacity {op_val} override {op_val} override, class:^(minimap_overlay)$",
                "float, class:^(minimap_overlay)$",
                "noborder, class:^(minimap_overlay)$",
                "pin, class:^(minimap_overlay)$",
                "noblur, class:^(minimap_overlay)$",
                "nofocus, class:^(minimap_overlay)$"
            ]
            for rule in rules:
                run_cmd(['hyprctl', 'keyword', 'windowrulev2', rule])

        self.setGeometry(0, 0, 1, 1)
        self.setWindowOpacity(0.0)
        self.show()
        apply_overlay_platform_tweaks(self)

    def show_map(self, image_path, w, h, x, y, opacity):
        try:
            key = (image_path, w, h)
            pixmap = self._pixmap_cache.get(key)
            if pixmap is None:
                pixmap = QPixmap(image_path)
                if pixmap.isNull():
                    print(f"\n[DEBUG] ❌ Could not load image: {image_path}")
                    return
                pixmap = pixmap.scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
                # A handful of maps at a fixed size - cheap to keep, avoids re-decoding
                if len(self._pixmap_cache) > 30:
                    self._pixmap_cache.clear()
                self._pixmap_cache[key] = pixmap

            self.label.setPixmap(pixmap)
            self.label.resize(w, h)

            self.setGeometry(x, y, w, h)
            self.setWindowOpacity(float(opacity))
            self.is_visible = True
            raise_overlay(self)
        except Exception as e:
            print(f"\n[DEBUG] ❌ Overlay error: {e}")

    def hide_overlay(self):
        self.setWindowOpacity(0.0)
        self.setGeometry(0, 0, 1, 1)
        self.label.clear()
        self.is_visible = False

    def update_gui(self):
        QApplication.processEvents()


# --- GLOBAL THREAD-SAFE FLAGS ---
pending_reset = False
_last_reset_ts = 0.0
_injecting_until = 0.0      # keystrokes we send ourselves must not trigger our own hotkey
_last_event_ts = 0.0        # updated by the keyboard listener - used as a health check
_last_listener_restart = 0.0


def smart_sleep(duration, overlay, interrupt_func=None):
    end_time = time.time() + duration
    while time.time() < end_time:
        overlay.update_gui()
        # Always react to a reset request immediately instead of up to 5s later
        if pending_reset:
            break
        if interrupt_func and interrupt_func():
            break
        time.sleep(0.05)


# ---------------------------------------------------------
# 1-4. Setup Functions
# ---------------------------------------------------------
def check_directory():
    if not os.path.isdir('maps'):
        print("ERROR: 'maps' folder not found next to dbdmap.py.")
        print(f"Looked in: {os.getcwd()}")
        sys.exit(1)
    if not any(os.path.isdir(os.path.join('maps', d)) for d in os.listdir('maps')):
        print("ERROR: 'maps' contains no realm sub-folders.")
        sys.exit(1)


def calculate_screenshot_region(monitor):
    rx, ry, rw, rh = MAP_NAME_REGION
    w, h = monitor['width'], monitor['height']
    return (int(w * rx), int(h * ry), int(w * rw), int(h * rh))


def setup_config(app):
    config_file = 'config.ini'
    config = configparser.ConfigParser()
    if not os.path.exists(config_file):
        print("Generating config.ini...")
        monitors = enumerate_monitors(app, hyprctl_json)
        selected_monitor, selected_index = None, 0
        if not monitors:
            res_str = input("Enter resolution manually (e.g., 2560x1440): ").strip()
            w, h = map(int, res_str.split('x'))
            selected_monitor = {'name': 'Manual', 'width': w, 'height': h, 'scale': 1.0, 'x': 0, 'y': 0}
        elif len(monitors) == 1:
            m = monitors[0]
            if input(f"Detected {m['width']}x{m['height']} (Scale: {m['scale']}). Correct? (y/n): ").strip().lower() == 'y': selected_monitor = m
        else:
            print("Detected multiple monitors:")
            for i, m in enumerate(monitors): print(f"{i+1}. {m['width']}x{m['height']} | {m['name']}")
            choice = input(f"Select monitor (1-{len(monitors)}): ").strip()
            selected_index = int(choice) - 1 if choice.isdigit() and 0 < int(choice) <= len(monitors) else 0
            selected_monitor = monitors[selected_index]

        if not selected_monitor:
            w, h = map(int, input("Resolution (2560x1440): ").strip().split('x'))
            scale = float(input("Display scale factor (1.0): ").strip() or "1.0")
            selected_monitor = {'name': 'Manual', 'width': w, 'height': h, 'scale': scale, 'x': 0, 'y': 0}
        else:
            selected_index = monitors.index(selected_monitor) if selected_monitor in monitors else 0

        auto_resp = input("Automate ReShade switching? (y/n): ").strip().lower()
        reset_key = input("Enter reset hotkey (Default: F7): ").strip() or "F7"

        config['General'] = {
            'monitor_name': selected_monitor['name'],
            'monitor_index': str(selected_index),
            'resolution': f"{selected_monitor['width']}x{selected_monitor['height']}",
            'monitor_scale': str(selected_monitor['scale']),
            'monitor_x': str(selected_monitor['x']),
            'monitor_y': str(selected_monitor['y']),
            'reset_hotkey': reset_key,
        }
        config['ReShade'] = {'automate_reshade': "True" if auto_resp == 'y' else "False", 'reshade_path': '""'}
        config['Minimap'] = {'minimap_position': 'left', 'minimap_width': '300', 'minimap_height': '300', 'minimap_offset_x': '25', 'minimap_offset_y': '25', 'minimap_opacity': '80'}
        with open(config_file, 'w') as f: config.write(f)
    config.read(config_file)

    # Fill in options added after the config was first generated
    defaults = {
        'General': {'detection_threshold': '85', 'ocr_interval': '1.0', 'pause_after_match': '5.0',
                    'monitor_index': '0'},
        'OBS': {'enabled': 'False', 'websocket': '', 'scene_playing': 'Playing', 'scene_waiting': 'Waiting'},
        'Advanced': {'capture_backend': 'auto', 'tesseract_path': ''},
    }
    dirty = False
    for section, options in defaults.items():
        if not config.has_section(section):
            config.add_section(section)
            dirty = True
        for key, value in options.items():
            if not config.has_option(section, key):
                config.set(section, key, value)
                dirty = True
    if dirty:
        with open(config_file, 'w') as f: config.write(f)
    return config


def setup_reshade(config):
    if config['ReShade'].get('automate_reshade') != 'True': return None
    if not os.path.isfile("default-preset.ini"):
        print("ERROR: 'default-preset.ini' not found, but ReShade automation is enabled.")
        sys.exit(1)
    reshade_path = config['ReShade'].get('reshade_path', '""').strip('"')
    if not reshade_path or not os.path.isfile(reshade_path):
        if reshade_path:
            print(f"WARNING: configured ReShade.ini not found: {reshade_path}")
        reshade_path = input("Enter full path to ReShade.ini: ").strip().strip('"')
        if not os.path.isfile(reshade_path):
            print("ERROR: that file does not exist.")
            sys.exit(1)
        config.set('ReShade', 'reshade_path', f'"{reshade_path}"')
        with open('config.ini', 'w') as f: config.write(f)

    # Only back up once - re-copying on every launch would overwrite the pristine
    # backup with a file this script has already rewritten.
    backup_path = os.path.join(os.path.dirname(reshade_path), "ReShade.bak")
    if not os.path.isfile(backup_path):
        shutil.copy2(reshade_path, backup_path)
        restore_ownership(backup_path, reshade_path)
        print(f"Backed up original ReShade.ini -> {backup_path}")
    return reshade_path


def analyze_maps():
    maps_dir = "maps"
    realms = sorted([d for d in os.listdir(maps_dir) if os.path.isdir(os.path.join(maps_dir, d))])
    maps_data, realm_presets, map_to_realm = {}, [], {}
    for realm in realms:
        realm_path = os.path.join(maps_dir, realm)
        map_names, ini_file = [], None
        for f in sorted(os.listdir(realm_path)):
            ext, name = os.path.splitext(f)[1].lower(), os.path.splitext(f)[0]
            if ext in {'.png', '.jpg', '.jpeg', '.webp'}:
                map_names.append(name)
                map_to_realm[name] = realm
            elif ext == '.ini' and not ini_file: ini_file = os.path.join(realm_path, f)
        maps_data[realm] = sorted(map_names)
        if ini_file:
            realm_presets.append((realm, os.path.abspath(ini_file)))
        else:
            print(f"Note: realm '{realm}' has no .ini preset - ReShade won't be switched for it.")
    realm_presets.append(("DEFAULT", os.path.abspath("default-preset.ini")))
    try:
        with open('maps.json', 'w') as f: json.dump(maps_data, f, indent=4)
    except OSError as e:
        print(f"Note: could not write maps.json ({e}) - continuing anyway.")
    return maps_data, realm_presets, map_to_realm


def remap_reshade_keybinds(reshade_path, realm_presets, reserved_key=None):
    """Rewrites ReShade's preset shortcuts. `reserved_key` (e.g. 'f7') is skipped so
    the reset hotkey is never also a preset shortcut - injecting it used to make the
    script trigger its own reset and snap straight back to the default preset."""
    if not reshade_path or not realm_presets: return {}

    modifier_sets = [("1", "1", "1", "CTRL + SHIFT + ALT"), ("0", "1", "1", "SHIFT + ALT"), ("1", "0", "1", "CTRL + ALT"), ("1", "1", "0", "CTRL + SHIFT")]
    f_keys = [(112 + i, f"f{i + 1}") for i in range(12) if f"f{i + 1}" != reserved_key]

    slots = [(mods, key) for mods in modifier_sets for key in f_keys]
    if len(realm_presets) > len(slots):
        print(f"WARNING: {len(realm_presets)} presets but only {len(slots)} shortcut slots - the rest will be skipped.")

    shortcut_keys, shortcut_paths, binds_text, realm_to_keys = [], [], [], {}
    for (realm, ini_path), ((ctrl, shift, alt, mod_str), (keycode, f_key)) in zip(realm_presets, slots):
        shortcut_keys.append(f"{keycode},{ctrl},{shift},{alt}")
        shortcut_paths.append(ini_path)
        binds_text.append(f"{realm} = {mod_str} + {f_key.upper()}")
        realm_to_keys[realm] = {'ctrl': ctrl == '1', 'shift': shift == '1', 'alt': alt == '1', 'key': f_key}

    try:
        with open('binds.txt', 'w') as f: f.write('\n'.join(binds_text) + '\n')
    except OSError:
        pass

    default_preset = os.path.abspath("default-preset.ini")
    new_lines = {
        'PresetShortcutKeys=': 'PresetShortcutKeys=' + ','.join(shortcut_keys) + '\n',
        'PresetShortcutPaths=': 'PresetShortcutPaths=' + ','.join(shortcut_paths) + '\n',
    }

    with open(reshade_path, 'r', encoding='utf-8') as f: lines = f.readlines()

    out, written = [], set()
    for line in lines:
        prefix = next((p for p in new_lines if line.startswith(p)), None)
        if prefix:
            out.append(new_lines[prefix])
            written.add(prefix)
        elif line.startswith('PresetPath=') and 'DBDMap' in line:
            # Stale path (e.g. after moving this folder) - point it back at our default
            out.append(f'PresetPath={default_preset}\n')
        else:
            out.append(line)

    missing = [new_lines[p] for p in new_lines if p not in written]
    if missing:
        try:
            idx = next(i for i, l in enumerate(out) if l.strip().upper() == '[GENERAL]')
            out[idx + 1:idx + 1] = missing
        except StopIteration:
            out.append('\n[GENERAL]\n')
            out.extend(missing)

    # Atomic write so a crash mid-write can't leave a truncated ReShade.ini
    tmp_path = reshade_path + '.dbdmap.tmp'
    with open(tmp_path, 'w', encoding='utf-8') as f: f.writelines(out)
    restore_ownership(tmp_path, reshade_path)
    os.replace(tmp_path, reshade_path)
    return realm_to_keys


def restore_ownership(new_path, original_path):
    """We may run as root, the game does not. A replaced file would end up owned by
    root and ReShade could no longer save its own config ("Unable to save
    configuration and/or current preset")."""
    if IS_WINDOWS: return
    try:
        st = os.stat(original_path)
        uid, gid, mode = st.st_uid, st.st_gid, st.st_mode & 0o777
    except OSError:
        uid, gid, mode = -1, -1, 0o644

    # If the file is already root-owned (e.g. an older version of this script
    # clobbered it), hand it back to the user who ran sudo.
    if uid == 0 and os.environ.get('SUDO_UID'):
        uid = int(os.environ['SUDO_UID'])
        gid = int(os.environ.get('SUDO_GID', -1))

    try:
        os.chmod(new_path, mode)
        if uid != -1 and os.geteuid() == 0: os.chown(new_path, uid, gid)
    except OSError as e:
        print(f"Warning: could not preserve ownership of {original_path}: {e}")


# ---------------------------------------------------------
# Keyboard injection + listener health
# ---------------------------------------------------------
def _keyboard_probe(event):
    """Every real keyboard event bumps this timestamp - see check_listener_health()."""
    global _last_event_ts
    _last_event_ts = time.time()


def send_keybind(key_dict):
    """Synchronous key injection. Everything we press is announced through
    _injecting_until so our own reset hotkey handler ignores it."""
    global _injecting_until
    if not key_dict: return

    seen_before = _last_event_ts
    _injecting_until = time.time() + 2.0

    try:
        if IS_WINDOWS:
            if key_dict['ctrl']: user32.keybd_event(VK_CONTROL, 0, 0, 0)
            if key_dict['shift']: user32.keybd_event(VK_SHIFT, 0, 0, 0)
            if key_dict['alt']: user32.keybd_event(VK_MENU, 0, 0, 0)
            vk_key = F_KEYS[key_dict['key']]
            user32.keybd_event(vk_key, 0, 0, 0)
            time.sleep(0.1)
            user32.keybd_event(vk_key, 0, 2, 0)
            if key_dict['alt']: user32.keybd_event(VK_MENU, 0, 2, 0)
            if key_dict['shift']: user32.keybd_event(VK_SHIFT, 0, 2, 0)
            if key_dict['ctrl']: user32.keybd_event(VK_CONTROL, 0, 2, 0)
        else:
            held = [m for m in ('ctrl', 'shift', 'alt') if key_dict[m]]
            try:
                for m in held: keyboard.press(m)
                time.sleep(0.05)
                keyboard.press(key_dict['key'])
                time.sleep(0.1)
            finally:
                for k in [key_dict['key']] + list(reversed(held)):
                    try: keyboard.release(k)
                    except Exception: pass
    finally:
        # Small tail so the listener thread has drained our own key events
        _injecting_until = time.time() + 0.5

    if not IS_WINDOWS:
        time.sleep(0.05)
        if _last_event_ts == seen_before:
            check_listener_health()


def restart_listener(reason):
    """Rebuilds the keyboard hook. Rate limited, because it re-opens every input
    device - but it turns 'F7 stopped working, restart the script' into a hiccup."""
    global _last_listener_restart
    if time.time() - _last_listener_restart < 60:
        return
    _last_listener_restart = time.time()

    print(f"\n⚠️  {reason} - restarting the keyboard listener...")
    try:
        keyboard.unhook_all()
    except Exception: pass
    try:
        keyboard._listener.listening = False
    except Exception: pass
    try:
        register_hotkey()
        print("✅ Keyboard listener restarted.")
    except Exception as e:
        print(f"❌ Could not restart the keyboard listener: {e}")


def check_listener_health():
    """Our injected keys are read back by the keyboard listener. If they never show
    up, the listener died (this is what made F7 silently stop working mid-session)."""
    try:
        listener = keyboard._listener
        alive = (getattr(listener, 'listening', False)
                 and listener.listening_thread.is_alive()
                 and listener.processing_thread.is_alive())
    except Exception:
        return
    if not alive:
        restart_listener("Keyboard listener died")


def check_input_devices():
    """`keyboard` opens every input device once, at startup, and reads each in its
    own thread. If a keyboard is unplugged (or re-enumerated by Steam Input) that
    thread dies for good and keystrokes from it are never seen again."""
    if IS_WINDOWS: return
    try:
        device = getattr(keyboard._os_keyboard, 'device', None)
        paths = [d.path for d in getattr(device, 'devices', []) if d.path.startswith('/dev/input/')]
    except Exception:
        return
    if paths and not all(os.path.exists(p) for p in paths):
        restart_listener("An input device disappeared")


def reset_preset(event=None):
    """Ultra-dumb callback: just flips a flag and escapes instantly."""
    global pending_reset, _last_reset_ts
    now = time.time()
    if now < _injecting_until:
        return  # this is a keystroke we sent ourselves, not the user
    if now - _last_reset_ts < 0.5:
        return  # key auto-repeat
    _last_reset_ts = now
    pending_reset = True


def register_hotkey():
    keyboard.hook(_keyboard_probe)
    if '+' in RESET_HOTKEY:
        keyboard.add_hotkey(RESET_HOTKEY, reset_preset)
    else:
        # on_press_key fires per key event instead of depending on the library's
        # internal "currently pressed" table, which can go stale and silently
        # stop matching the hotkey forever.
        keyboard.on_press_key(RESET_HOTKEY, reset_preset)


# ---------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------
_obs_check = {'ts': 0.0, 'running': False}
_last_obs_scene = None


def is_obs_running():
    now = time.time()
    if now - _obs_check['ts'] < 30:
        return _obs_check['running']
    _obs_check['ts'] = now
    clients = hyprctl_json('clients')
    if clients is None:
        names = {'obs', 'obs.exe', 'obs64.exe', 'obs32.exe'}
        _obs_check['running'] = any(
            (p.info.get('name') or '').lower() in names for p in psutil.process_iter(['name'])
        )
    else:
        _obs_check['running'] = any('com.obsproject.Studio' in c.get('class', '') for c in clients)
    return _obs_check['running']


def obs_switch_scene(scene):
    global _last_obs_scene
    if not (OBS_ENABLED and OBS_WEBSOCKET and HAS_OBSCMD): return
    if scene == _last_obs_scene: return
    if not is_obs_running(): return
    _last_obs_scene = scene
    fire_and_forget(["obs-cmd", "--websocket", OBS_WEBSOCKET, "scene", "switch", scene], timeout=5)


def get_dbd_window_info():
    return find_dbd_window(hyprctl_json)


def preprocess_for_ocr(pil_image):
    cv_image = cv2.cvtColor(np.array(pil_image.convert("RGB")), cv2.COLOR_RGB2BGR)
    gray = cv2.cvtColor(cv_image, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY)
    thresh = cv2.dilate(thresh, None, iterations=1)
    thresh = cv2.bitwise_not(thresh)
    thresh = cv2.resize(thresh, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    thresh = cv2.GaussianBlur(thresh, (11, 11), 0)
    toCrop = Image.fromarray(thresh).convert("L")
    bbox = ImageOps.invert(toCrop).getbbox()
    if bbox:
        cropped = toCrop.crop(bbox)
        thresh = np.array(cropped)
    return thresh


def clean_ocr_text(text):
    if not text: return ""
    text = ''.join(ch for ch in unicodedata.normalize('NFKD', text) if not unicodedata.category(ch).startswith('M'))
    text = text.upper()
    replacements = {" ": "_", "|": "I", "0": "O", "’": "'", "“": "'", "”": "'", "`": "'", "´": "'", "VV": "W", "1": "I", "5": "S", "8": "B", "6": "G", "2": "Z", "—": "-"}
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


def _norm(s):
    return ''.join(ch for ch in s.upper() if ch.isalnum())


def best_map_match(raw_text, all_known_maps):
    """Scores the whole capture and each individual line - a stray second line of
    noise used to drag the score of a perfectly readable map name below threshold."""
    candidates = [clean_ocr_text(raw_text)]
    candidates += [clean_ocr_text(line) for line in raw_text.splitlines() if len(line.strip()) >= 4]

    best, best_score = None, 0.0
    for candidate in candidates:
        parsed = _norm(candidate)
        if len(parsed) < 4: continue
        for m in all_known_maps:
            score = SequenceMatcher(None, parsed, _norm(m)).ratio() * 100
            if score > best_score: best_score, best = score, m
    return best, best_score


# ---------------------------------------------------------
# Overlay placement
# ---------------------------------------------------------
def compute_overlay_rect(config, app, monitor):
    """Qt positions windows in logical coordinates, so anchoring to the right edge
    has to use the logical width - not the physical resolution from config.ini."""
    width = int(config['Minimap'].get('minimap_width', '300'))
    height = int(config['Minimap'].get('minimap_height', '300'))
    off_x = int(config['Minimap'].get('minimap_offset_x', '25'))
    off_y = int(config['Minimap'].get('minimap_offset_y', '25'))
    position = config['Minimap'].get('minimap_position', 'left').lower()

    screen = None
    for candidate in app.screens():
        if candidate.name() == monitor['name']:
            screen = candidate
            break
    if screen is None:
        index = int(config['General'].get('monitor_index', '0') or 0)
        screens = app.screens()
        screen = screens[index] if 0 <= index < len(screens) else app.primaryScreen()

    if screen is not None:
        geom = screen.geometry()
        origin_x, origin_y, logical_w = geom.x(), geom.y(), geom.width()
    else:
        origin_x, origin_y = monitor['x'], monitor['y']
        logical_w = int(monitor['width'] / monitor['scale'])

    x = origin_x + (logical_w - width - off_x if position == 'right' else off_x)
    return x, origin_y + off_y, width, height


# ---------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------
def run_doctor(app):
    print("DBDMap environment report")
    print("-" * 35)
    print(f"Session       : {session_summary()}")
    print(f"Python        : {sys.version.split()[0]}")
    print(f"Qt platform   : {os.environ.get('QT_QPA_PLATFORM', 'default')}")
    print(f"Directory     : {os.getcwd()}")

    if IS_LINUX:
        ok, missing = linux_input_access()
        print(f"Input access  : {'OK' if ok else 'MISSING -> ' + ', '.join(missing)}")

    path, info = setup_tesseract()
    print(f"Tesseract     : {path or 'NOT FOUND'}  ({info})")

    monitors = enumerate_monitors(app, hyprctl_json)
    for i, m in enumerate(monitors):
        print(f"Monitor {i}     : {m['name']} {m['width']}x{m['height']} "
              f"scale={m['scale']} layout=({m['x']},{m['y']}) physical=({m['phys_x']},{m['phys_y']})")

    if monitors:
        print("Capture       :")
        for name, verdict in probe_all_backends(monitors[0]):
            print(f"  {name:<17} {verdict}")
        try:
            ScreenCapture(monitors[0], 'auto', log=lambda m: print(f"  selected -> {m}"))
        except CaptureError as e:
            print(f"  selected -> NONE\n{e}")

    found, rect, space = find_dbd_window(hyprctl_json)
    print(f"Dead by Daylight: {'running' if found else 'not running'}"
          + (f"  rect={rect} ({space})" if rect else ""))

    for note in overlay_notes():
        print(f"Note          : {note}")


# ---------------------------------------------------------
# Main Execution & Loop
# ---------------------------------------------------------
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName("minimap_overlay")

    if DOCTOR:
        run_doctor(app)
        sys.exit(0)

    print("Starting dbdmap initialization...\n" + "-" * 35)
    print(f"Session: {session_summary()}")

    check_directory()
    config = setup_config(app)

    tesseract_path, tesseract_info = setup_tesseract(config['Advanced'].get('tesseract_path', ''))
    if not tesseract_path:
        print(f"\n❌ CRITICAL: {tesseract_info}.")
        if IS_WINDOWS:
            print("Install it from https://github.com/UB-Mannheim/tesseract/wiki")
            print("(or run: winget install UB-Mannheim.TesseractOCR)")
            print("then re-run DBDMap, or set tesseract_path under [Advanced] in config.ini.")
        else:
            print("Install it with your package manager, e.g.:")
            print("  sudo pacman -S tesseract tesseract-data-eng")
            print("  sudo apt install tesseract-ocr")
            print("  sudo dnf install tesseract")
        sys.exit(1)
    print(f"OCR engine: {tesseract_info}")

    monitors = enumerate_monitors(app, hyprctl_json)
    monitor = pick_monitor(monitors, config) or synthetic_monitor(config)
    print(f"Monitor: {monitor['name']} {monitor['width']}x{monitor['height']} (scale {monitor['scale']})")

    try:
        capture = ScreenCapture(monitor, config['Advanced'].get('capture_backend', 'auto'))
    except CaptureError as e:
        print(f"\n❌ CRITICAL: {e}")
        sys.exit(1)

    RESET_HOTKEY = config['General'].get('reset_hotkey', 'F7').strip().lower()
    RESERVED_KEY = RESET_HOTKEY if re.fullmatch(r'f([1-9]|1[0-2])', RESET_HOTKEY) else None
    OBS_ENABLED = config['OBS'].getboolean('enabled', fallback=False)
    OBS_WEBSOCKET = config['OBS'].get('websocket', '').strip()
    OBS_SCENE_PLAYING = config['OBS'].get('scene_playing', 'Playing')
    OBS_SCENE_WAITING = config['OBS'].get('scene_waiting', 'Waiting')
    THRESHOLD = float(config['General'].get('detection_threshold', '85'))
    OCR_INTERVAL = float(config['General'].get('ocr_interval', '1.0'))
    PAUSE_AFTER_MATCH = float(config['General'].get('pause_after_match', '5.0'))

    reshade_path = setup_reshade(config)
    maps_data, realm_presets, map_to_realm = analyze_maps()
    realm_to_keys = remap_reshade_keybinds(reshade_path, realm_presets, reserved_key=RESERVED_KEY)

    static_resolution_region = calculate_screenshot_region(monitor)
    all_known_maps = [m for maps in maps_data.values() for m in maps]
    console = ConsoleOutput()
    overlay = PersistentOverlay(config)

    try:
        register_hotkey()
        print(f"Bound '{RESET_HOTKEY.upper()}' to reset preset and close overlay.")
        if RESERVED_KEY:
            print(f"'{RESERVED_KEY.upper()}' is excluded from the ReShade preset shortcuts.")
    except Exception as e:
        print(f"Warning: Could not bind global hotkey. Error: {e}")

    for note in overlay_notes():
        print(f"Note: {note}")

    print("\nInitialization complete! Entering main OCR loop.\n" + "-" * 35)

    current_map = None
    next_device_check = time.time() + 30
    blank_frames, blank_warned = 0, False

    try:
        while True:
            try:
                overlay.update_gui()

                if time.time() >= next_device_check:
                    next_device_check = time.time() + 30
                    check_input_devices()

                # Handle the reset request from the Main Thread to protect the keyboard listener
                if pending_reset:
                    pending_reset = False
                    current_map = None
                    overlay.hide_overlay()
                    smart_sleep(0.1, overlay)

                    if "DEFAULT" in realm_to_keys:
                        send_keybind(realm_to_keys["DEFAULT"])

                    obs_switch_scene(OBS_SCENE_WAITING)
                    console.warn("🔄 Reset to default preset.")
                    continue  # Skip the OCR this loop cycle so the UI stays snappy

                is_running, win_rect, win_space = get_dbd_window_info()

                if not is_running:
                    console.log("⏸️  DeadByDaylight not running. Waiting...")
                    smart_sleep(5.0, overlay)
                    continue

                if win_rect is not None:
                    win_x, win_y, win_w, win_h = win_rect
                    rx, ry, rw, rh = MAP_NAME_REGION
                    current_region = (int(win_x + win_w * rx), int(win_y + win_h * ry),
                                      int(win_w * rw), int(win_h * rh))
                    screenshot = capture.grab(current_region, space=win_space)
                else:
                    screenshot = capture.grab(static_resolution_region, space='monitor')

                if screenshot is None:
                    console.log("📷 Screen capture failed. Retrying...")
                    smart_sleep(1.0, overlay)
                    continue

                # A backend that keeps handing back a uniform image is capturing
                # nothing; without this the only symptom is "No text detected"
                # forever, which looks like an OCR problem rather than a capture one.
                if is_blank(screenshot):
                    blank_frames += 1
                    if blank_frames == 30 and not blank_warned:
                        blank_warned = True
                        console.warn(
                            f"⚠️  The '{capture.name}' capture backend has returned a blank image "
                            f"30 times in a row.\n"
                            f"    DBDMap is not seeing the game. Try setting capture_backend under "
                            f"[Advanced] in config.ini,\n"
                            f"    or run './dbdmap --doctor' to see what else is available.")
                else:
                    blank_frames = 0

                processed = preprocess_for_ocr(screenshot)

                # =================================================================
                # DEBUG ONLY: SHOW RAW AND PROCESSED SCREENSHOTS
                # Uncomment the 4 lines below to visually debug the OCR region.
                # This will open two windows showing what the script is reading.
                # =================================================================
                # raw_cv_img = cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2BGR)
                # cv2.imshow("DEBUG: Raw Capture", raw_cv_img)
                # cv2.imshow("DEBUG: Processed for OCR", processed)
                # cv2.waitKey(1)
                # =================================================================

                try:
                    raw_text = pytesseract.image_to_string(processed, config='--psm 6', timeout=OCR_TIMEOUT).strip()
                except Exception:
                    raw_text = ""

                match_found = False

                if not raw_text:
                    console.log("❌ No text detected.")
                else:
                    best, best_score = best_map_match(raw_text, all_known_maps)

                    if best and best_score >= THRESHOLD:
                        match_found = True
                        if best == current_map:
                            console.log(f"✅ {best} still active ({best_score:.0f}%)")
                        else:
                            console.log(f"🔎 MAP DETECTED: {best} ({best_score:.0f}%)")
                            current_map = best

                            realm = map_to_realm.get(best)
                            if realm and realm in realm_to_keys:
                                send_keybind(realm_to_keys[realm])

                            map_image_path = None
                            for ext in ['.png', '.jpg', '.jpeg', '.webp']:
                                test_path = os.path.join("maps", realm or "", best + ext)
                                if os.path.exists(test_path):
                                    map_image_path = test_path
                                    break

                            if map_image_path:
                                pos_x, pos_y, target_w, target_h = compute_overlay_rect(config, app, monitor)

                                opacity_raw = config['Minimap'].get('minimap_opacity', '80').replace('%', '')
                                opacity_val = float(opacity_raw) / 100.0

                                overlay.show_map(map_image_path, target_w, target_h, pos_x, pos_y, opacity_val)
                            else:
                                console.warn(f"⚠️  No image file found for {best}")

                            obs_switch_scene(OBS_SCENE_PLAYING)
                    elif best:
                        console.log(f"❔ Unclear text (closest: {best} {best_score:.0f}%)")
                    else:
                        console.log("❌ No text detected.")

                if match_found:
                    console.log(f"⏳ Pausing OCR for {PAUSE_AFTER_MATCH:.0f} seconds...")
                    smart_sleep(PAUSE_AFTER_MATCH, overlay, interrupt_func=lambda: not overlay.is_visible)
                else:
                    smart_sleep(OCR_INTERVAL, overlay)

            except Exception:
                # One bad frame must never take the whole script down
                console.warn("⚠️  Recovered from an error in the main loop:")
                traceback.print_exc()
                smart_sleep(2.0, overlay)

    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        try: overlay.hide_overlay()
        except Exception: pass
        try: keyboard.unhook_all()
        except Exception: pass
