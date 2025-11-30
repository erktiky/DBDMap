import os
import threading
import tkinter as tk
from PIL import Image, ImageTk, ImageOps
import pytesseract
import pyautogui
import keyboard
from screeninfo import get_monitors
import cv2
import numpy as np
import time
import configparser
import psutil
import sys
import unicodedata
import random
from difflib import SequenceMatcher
import random

clean_terminal = True


def randomize_time(center: float, spread: float) -> float:
    """
    Returns a random float between (center - spread) and (center + spread).
    """
    return random.uniform(center - spread, center + spread)

def reset_reshade_func():
    try:
        import ctypes
        from ctypes import wintypes

        # Try to bring DeadByDaylight window to foreground so it receives input
        def _enum_windows_for_pid(pid):
            hwnds = []
            EnumWindows = ctypes.windll.user32.EnumWindows
            EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
            GetWindowThreadProcessId = ctypes.windll.user32.GetWindowThreadProcessId

            def _cb(hwnd, lParam):
                pid_ref = wintypes.DWORD()
                GetWindowThreadProcessId(hwnd, ctypes.byref(pid_ref))
                if pid_ref.value == lParam and ctypes.windll.user32.IsWindowVisible(hwnd):
                    hwnds.append(hwnd)
                return True

            EnumWindows(EnumWindowsProc(_cb), pid)
            return hwnds

        target_pid = None
        for p in psutil.process_iter(['name', 'pid']):
            try:
                name = p.info.get('name') or ''
                if name.lower() in ('deadbydaylight.exe', 'deadbydaylight-egs-shipping.exe'):
                    target_pid = p.info['pid']
                    break
            except Exception:
                continue

        if target_pid:
            hwnds = _enum_windows_for_pid(target_pid)
            if hwnds:
                try:
                    ctypes.windll.user32.SetForegroundWindow(hwnds[0])
                    time.sleep(0.05)
                except Exception:
                    pass

        # Use low-level Win32 input so games/overlays that ignore higher-level events accept them
        VK_HOME = 0x24
        KEYEVENTF_KEYUP = 0x0002

        # Use configured refresh area coordinates early so we can check menu state
        x, y = RESHADE_PIXEL_CHECK

        # Define pixel-checking helper and constants early so we can test menu state
        TARGET_RGB1 = (71, 99, 152)
        TARGET_RGB2 = (30, 30, 30)
        TOLERANCE = 5

        def _pixel_matches(x, y, target1, target2, tol=TOLERANCE):
            # get device context for the entire screen
            time.sleep(0.1)
            dc = ctypes.windll.user32.GetDC(0)
            try:
                color = ctypes.windll.gdi32.GetPixel(dc, int(x), int(y))
            finally:
                ctypes.windll.user32.ReleaseDC(0, dc)

            if color == -1 or color is None:
                return False

            r = color & 0xFF
            g = (color >> 8) & 0xFF
            b = (color >> 16) & 0xFF

            def match(target):
                return (
                    abs(r - target[0]) <= tol and
                    abs(g - target[1]) <= tol and
                    abs(b - target[2]) <= tol
                )

            return match(target1) or match(target2)


        # Ensure the reshade menu is open: if the pixel at the refresh area does not
        # match the expected "open" color, press Home until it does (or give up).
        checks = 0
        while not _pixel_matches(x, y, TARGET_RGB1, TARGET_RGB2, TOLERANCE):
            ctypes.windll.user32.keybd_event(VK_HOME, 0, 0, 0)
            time.sleep(randomize_time(0.01, 0.002))
            ctypes.windll.user32.keybd_event(VK_HOME, 0, KEYEVENTF_KEYUP, 0)
            checks += 1
            if checks >= 15:
                break
            time.sleep(randomize_time(0.1, 0.02))
        time.sleep(randomize_time(0.1, 0.02))
        if not _pixel_matches(x + random.randint(-2,2), y + random.randint(-2,2), TARGET_RGB1, TARGET_RGB2, TOLERANCE):
            ctypes.windll.user32.keybd_event(VK_HOME, 0, 0, 0)

        mouse_x, mouse_y = RESHADE_REFRESH_AREA

        # move mouse and click using native calls (use configured refresh area)
        ctypes.windll.user32.SetCursorPos(int(mouse_x + random.randint(-2,2)), int(mouse_y + random.randint(-2,2)))
        time.sleep(randomize_time(0.01, 0.002))
        MOUSEEVENTF_LEFTDOWN = 0x0002
        MOUSEEVENTF_LEFTUP = 0x0004
        ctypes.windll.user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        time.sleep(randomize_time(0.03, 0.005))
        ctypes.windll.user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        # short wait before checking menu state
        time.sleep(randomize_time(0.03, 0.005))

        # press Home once to finalize, then poll a pixel to ensure the menu closed.
        ctypes.windll.user32.keybd_event(VK_HOME, 0, 0, 0)
        time.sleep(randomize_time(0.01, 0.002))
        ctypes.windll.user32.keybd_event(VK_HOME, 0, KEYEVENTF_KEYUP, 0)
        time.sleep(randomize_time(0.1, 0.02))

        # Poll and re-send Home while the pixel at the same refresh coordinates indicates the menu is still open
        checks = 0
        while _pixel_matches(x, y, TARGET_RGB1, TARGET_RGB2, TOLERANCE):
            ctypes.windll.user32.keybd_event(VK_HOME, 0, 0, 0)
            time.sleep(randomize_time(0.01, 0.002))
            ctypes.windll.user32.keybd_event(VK_HOME, 0, KEYEVENTF_KEYUP, 0)
            checks += 1
            if checks >= 15:
                break  # avoid infinite loop
            time.sleep(randomize_time(0.1, 0.02))

        ctypes.windll.user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        time.sleep(randomize_time(0.03, 0.005))
        ctypes.windll.user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    except Exception as e:
        print(f"Error in reset_reshade_func: {e}")

REALMS = {
    "THE_MACMILLAN_ESTATE": [
        "COAL_TOWER",
        "GROANING_STOREHOUSE",
        "IRONWORKS_OF_MISERY",
        "SHELTER_WOODS",
        "SUFFOCATION_PIT"
    ],
    "AUTOHAVEN_WRECKERS": [
        "AZAROV'S_RESTING_PLACE",
        "BLOOD_LODGE",
        "GAS_HEAVEN",
        "WRECKERS'_YARD",
        "WRETCHED_SHOP"
    ],
    "COLDWIND_FARM": [
        "FRACTURED_COWSHED",
        "RANCID_ABATTOIR",
        "ROTTEN_FIELDS",
        "THE_THOMPSON_HOUSE",
        "TORMENT_CREEK"
    ],
    "CROTUS_PRENN_ASYLUM": [
        "DISTURBED_WARD",
        "FATHER_CAMPBELL'S_CHAPEL"
    ],
    "HADDONFIELD": [
        "LAMPKIN_LANE"
    ],
    "BACKWATER_SWAMP": [
        "THE_PALE_ROSE",
        "GRIM_PANTRY"
    ],
    "LERY'S_MEMORIAL_INSTITUTE": [
        "TREATMENT_THEATRE"
    ],
    "RED_FOREST": [
        "MOTHER'S_DWELLING",
        "THE_TEMPLE_OF_PURGATION"
    ],
    "SPRINGWOOD": [
        "BADHAM_PRESCHOOL_I"
    ],
    "GIDEON_MEAT_PLANT": [
        "THE_GAME"
    ],
    "YAMAOKA_ESTATE": [
        "FAMILY_RESIDENCE",
        "SANCTUM_OF_WRATH"
    ],
    "ORMOND": [
        "MOUNT_ORMOND_RESORT",
        "ORMOND_LAKE_MINE"
    ],
    "HAWKINS_NATIONAL_LABORATORY": [
        "THE_UNDERGROUND_COMPLEX"
    ],
    "GRAVE_OF_GLENVALE": [
        "DEAD_DAWG_SALOON"
    ],
    "SILENT_HILL": [
        "MIDWICH_ELEMENTARY_SCHOOL"
    ],
    "RACCOON_CITY": [
        "RACCOON_CITY_POLICE_STATION_EAST_WING",
        "RACCOON_CITY_POLICE_STATION_WEST_WING"
    ],
    "FORSAKEN_BONEYARD": [
        "EYRIE_OF_CROWS",
        "DEAD_SANDS"
    ],
    "WITHERED_ISLE": [
        "GARDEN_OF_JOY",
        "GREENVILLE_SQUARE",
        "FREDDY_FAZBEAR'S_PIZZA",
        "FALLEN_REFUGE"
    ],
    "THE_DECIMATED_BORGO": [
        "THE_SHATTERED_SQUARE",
        "FORGOTTEN_RUINS"
    ],
    "DVARKA_DEEPWOOD": [
        "TOBA_LANDING",
        "NOSTROMO_WRECKAGE"
    ]
}

MAP_TO_REALM = {}

for realm, maps in REALMS.items():
    for m in maps:
        MAP_TO_REALM[m] = realm

def get_realm(map_name: str) -> str | None:
    return MAP_TO_REALM.get(map_name)


def get_all_maps() -> list:
    """Return a flat list of all map names (sub-realms) from the REALMS dict."""
    all_maps = []
    for maps in REALMS.values():
        all_maps.extend(maps)
    return all_maps



def get_base_path():
    if getattr(sys, 'frozen', False):
        # Running as compiled .exe
        return os.path.dirname(sys.executable)
    else:
        # Running from .py
        return os.path.dirname(os.path.abspath(__file__))

config = configparser.ConfigParser()
config_path = os.path.join(get_base_path(), 'config.ini')
config.read(config_path)

os.environ['TESSDATA_PREFIX'] = os.path.join(get_base_path(), 'Tesseract-OCR', 'share', 'tessdata')
pytesseract.pytesseract.tesseract_cmd = os.path.join(get_base_path(), 'Tesseract-OCR', 'tools', 'tesseract', 'tesseract.exe')

# --- SETTINGS ---
IMAGE_FOLDER = os.path.join(get_base_path(), "maps")  # Folder containing your images (relative to script)
RESHADE_FOLDER = os.path.join(get_base_path(), "reshades")  # Folder containing your reshade images (relative to script)
UPDATE_RESHADE = config.getboolean('DEFAULT', 'UPDATE_RESHADE')  # If True, apply reshade preset when changing map
SCREENSHOT_REGION = tuple(map(int, config.get('DEFAULT', 'SCREENSHOT_REGION').split(',')))  # (left, top, width, height)
UPDATE_KEYBIND = config.get('DEFAULT', 'UPDATE_KEYBIND')  # Key to capture screenshot and update image
RESET_KEYBIND = config.get('DEFAULT', 'RESET_KEYBIND')  # Key to reset image
RESET_RESHADE_KEYBIND = config.get('DEFAULT', 'RESET_RESHADE_KEYBIND')  # Key to reset reshade to default
CROSSHAIR_KEYBIND = config.get('DEFAULT', 'CROSSHAIR_KEYBIND')  # Key to toggle Magical Rectangle in active preset
CENTER_UP_KEYBIND = config.get('DEFAULT', 'CENTER_UP_KEYBIND', fallback='PAGE_UP')  # Key to set center to (0.5, 0.5)
CENTER_DOWN_KEYBIND = config.get('DEFAULT', 'CENTER_DOWN_KEYBIND', fallback='PAGE_DOWN')  # Key to set center to (0.5, 0.5 + offset)
CENTER_OFFSET = config.getfloat('DEFAULT', 'CROSSHAIR_OFFSET', fallback=0.35)  # Offset value where 0.35 -> +0.035 on Y
AUTO_UPDATE = config.getboolean('DEFAULT', 'AUTO_UPDATE')  # If True, automatically update image every second
SECONDARY_MONITOR_MODE = config.getboolean('DEFAULT', 'SECONDARY_MONITOR_MODE') # If True, use 2nd monitor fullscreen if available
MINIMAP_SCALE = config.getfloat('DEFAULT', 'MINIMAP_SCALE')  # Scale factor for minimap display
MINIMAP_POSITION = config.get('DEFAULT', 'MINIMAP_POSITION').lower()  # Position of minimap: 'left' or 'right'
MINIMAP_OFFSET_X = config.getint('DEFAULT', 'MINIMAP_OFFSET_X')  # X offset for minimap position
MINIMAP_OFFSET_Y = config.getint('DEFAULT', 'MINIMAP_OFFSET_Y')  # Y offset for minimap position
MINIMAP_ALPHA = config.getfloat('DEFAULT', 'MINIMAP_ALPHA')  # Transparency for minimap window (0.0 to 1.0)
RESHADE_PATH = None
RESHADE_PIXEL_CHECK = tuple(map(int, config.get('DEFAULT', 'RESHADE_PIXEL_CHECK').split(',')))  # Coordinates to look at when checking pixel color while refresh reshade (read from config)
RESHADE_REFRESH_AREA = tuple(map(int, config.get('DEFAULT', 'RESHADE_REFRESH_AREA').split(',')))  # Coordinates to click to refresh reshade (read from config)
# ----------------

monitors = get_monitors()
if SECONDARY_MONITOR_MODE:
    monitor = monitors[1] if len(monitors) > 1 else monitors[0]
else:
    monitor = monitors[0]

minimap_size_x = round((monitor.width / 6) * MINIMAP_SCALE)
minimap_size_y = round((monitor.width / 6) * MINIMAP_SCALE)

if UPDATE_RESHADE:
    RESHADE_PATH = config.get('DEFAULT', 'RESHADE_PATH', fallback='').strip()
    if not RESHADE_PATH:
        RESHADE_PATH = None
        UPDATE_RESHADE = False
    else:
        RESHADE_PATH = os.path.abspath(RESHADE_PATH)
        if not RESHADE_PATH:
            RESHADE_PATH = None
            UPDATE_RESHADE = False

class ImageWindow:
    def make_clickthrough(self, hwnd):
        # Windows-specific code to make window click-through
        import ctypes
        hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
        styles = ctypes.windll.user32.GetWindowLongW(hwnd, -20)
        styles = styles | 0x80000 | 0x20  # WS_EX_LAYERED | WS_EX_TRANSPARENT
        ctypes.windll.user32.SetWindowLongW(hwnd, -20, styles)

    def __init__(self):
        if len(monitors) > 1 and SECONDARY_MONITOR_MODE:
        # Create always-on-top, borderless window
            self.root = tk.Tk()
            self.root.title("Image Display")
            self.root.attributes('-topmost', True)
            self.root.overrideredirect(True)
            self.root.geometry(f"{round(0.5 * monitor.width)}x{monitor.height}+{monitor.x}+{monitor.y}")
            self.root.attributes('-alpha', 1)
            self.root.wm_attributes('-transparentcolor', "#574dad")
            self.root.configure(bg='#574dad')
            # Canvas for the image
            self.label = tk.Label(self.root, bg='#574dad')
            self.label.pack(fill='both', expand=True)
        else:
            self.root = tk.Tk()
            self.root.title("Image Display")
            self.root.attributes('-topmost', True)
            self.root.overrideredirect(True)
            if MINIMAP_POSITION == 'left':
                minimap_pos_x = MINIMAP_OFFSET_X
            else:
                minimap_pos_x = monitor.width - (minimap_size_x + MINIMAP_OFFSET_X)
            self.root.geometry(f"{minimap_size_x}x{minimap_size_y}+{minimap_pos_x}+{MINIMAP_OFFSET_Y}")
            self.root.attributes('-alpha', MINIMAP_ALPHA)
            self.root.wm_attributes('-transparentcolor', '#574dad')
            self.root.configure(bg='#574dad', bd=0, highlightthickness=0)
            # Canvas for the image
            self.label = tk.Label(self.root, bg='#574dad', borderwidth=0, highlightthickness=0)
            self.label.pack(fill='both', expand=True)

            self.root.after(500, self.make_clickthrough, self.root.winfo_id())

        # Mode flags
        self.preview_mode = False
        if AUTO_UPDATE:
            print("🔹 Auto-update mode enabled. Cannot use Preview Mode.")
        else:
            print("🔹 Starting in Game Mode. Press F10 to toggle Preview Mode.")

        # Start listeners
        threading.Thread(target=self.listen_for_key, daemon=True).start()
        threading.Thread(target=self.listen_for_clear, daemon=True).start()
        threading.Thread(target=self.listen_for_reset_reshade, daemon=True).start()
        threading.Thread(target=self.listen_for_crosshair, daemon=True).start()
        threading.Thread(target=self.listen_for_center_keys, daemon=True).start()
        threading.Thread(target=self.listen_for_f10, daemon=True).start()

        self.root.mainloop()

    def listen_for_key(self):
        if not AUTO_UPDATE:
            print(f"Press {UPDATE_KEYBIND} to capture text and load image...")
            while True:
                keyboard.wait(UPDATE_KEYBIND)
                attempt1 = self.update_image()
                if not attempt1:
                    time.sleep(0.5)
                    attempt2 = self.update_image()
                    if not attempt2:
                        time.sleep(0.5)
                        self.update_image()
        else:
            print("Auto-update mode enabled. Updating image every second...")
            while True: 
                if "DeadByDaylight.exe" in (i.name() for i in psutil.process_iter()) or "DeadByDaylight-EGS-Shipping.exe" in (i.name() for i in psutil.process_iter()):
                    attempt = self.update_image()
                    if attempt:
                        time.sleep(5)
                else:
                    print("❌ Dead by Daylight is not running. Waiting...")
                time.sleep(1)

    def listen_for_clear(self):
        print(f"Press {RESET_KEYBIND} to clear the image...")
        while True:
            keyboard.wait(RESET_KEYBIND)
            self.label.config(image=None)
            self.label.image = None
            print("🧹 Image cleared.")

    def listen_for_reset_reshade(self):
        print(f"Press {RESET_RESHADE_KEYBIND} to reset Reshade to default...")
        while True:
            keyboard.wait(RESET_RESHADE_KEYBIND)
            if RESHADE_PATH:
                cfg = configparser.ConfigParser()
                cfg.optionxform = str  # preserve option name case (so it stays 'PresetPath')
                cfg.read(RESHADE_PATH, encoding="utf-8")

                if not cfg.has_section("GENERAL"):
                    cfg.add_section("GENERAL")

                # Use absolute path to ensure ReShade loads the correct Backup.ini from DBDMap/reshades
                backup_reshade = os.path.abspath(os.path.join(get_base_path(), "reshades", "Backup.ini"))
                cfg.set("GENERAL", "PresetPath", backup_reshade)

                with open(RESHADE_PATH, "w", encoding="utf-8", newline="\n") as f:
                    cfg.write(f)

                reset_reshade_func()
                print("🧹 Reshade preset reset to default.")
            else:
                print("❌ Reshade path not configured; cannot reset.")

    def _resolve_active_preset_path(self) -> str | None:
        """
        Read `PresetPath` from ReShade.ini at `RESHADE_PATH` and resolve it to an absolute file path.
        If the value is a bare filename or relative (e.g. '.\\Backup.ini'), resolve relative to the parent
        folder of `RESHADE_PATH`.
        """
        if not RESHADE_PATH or not os.path.isfile(RESHADE_PATH):
            return None
        cfg = configparser.ConfigParser()
        cfg.optionxform = str
        cfg.read(RESHADE_PATH, encoding="utf-8")
        if not cfg.has_section("GENERAL") or not cfg.has_option("GENERAL", "PresetPath"):
            return None
        preset_path_raw = cfg.get("GENERAL", "PresetPath").strip()
        base_dir = os.path.dirname(RESHADE_PATH)
        # Handle relative forms like '.\\Backup.ini' or just 'Backup.ini'
        if preset_path_raw.startswith('.'):
            abs_path = os.path.abspath(os.path.join(base_dir, preset_path_raw))
        elif os.path.isabs(preset_path_raw):
            abs_path = preset_path_raw
        else:
            abs_path = os.path.abspath(os.path.join(base_dir, preset_path_raw))
        return abs_path

    def _get_magical_center(self, preset_file: str) -> tuple[float, float] | None:
        """
        Get the current `center=` value from the `[PD80_04_Magical_Rectangle.fx]` section.
        Returns (x, y) tuple if found, None otherwise.
        """
        if not preset_file or not os.path.isfile(preset_file):
            return None
        try:
            with open(preset_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()

            in_section = False
            for line in lines:
                if line.strip() == '[PD80_04_Magical_Rectangle.fx]':
                    in_section = True
                    continue
                if in_section:
                    if line.strip().startswith('[') and line.strip().endswith(']'):
                        # Next section, stop looking
                        break
                    if line.startswith('center='):
                        value = line.split('=', 1)[1].strip()
                        parts = value.split(',')
                        if len(parts) == 2:
                            return (float(parts[0]), float(parts[1]))
            return None
        except Exception:
            return None

    def _is_magical_rectangle_enabled(self, preset_file: str) -> bool:
        """
        Check if Magical Rectangle is enabled in the given preset file by reading the `Techniques` line.
        Returns True if enabled, False if disabled or not found.
        """
        if not preset_file or not os.path.isfile(preset_file):
            return False
        try:
            with open(preset_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()

            target_token = 'prod80_04_Magical_Rectangle@PD80_04_Magical_Rectangle.fx'
            for line in lines:
                if line.startswith('Techniques='):
                    techniques = [t.strip() for t in line.split('=', 1)[1].strip().split(',') if t.strip()]
                    return target_token in techniques
            return False
        except Exception:
            return False

    def _set_magical_rectangle_state(self, preset_file: str, enabled: bool) -> bool:
        """
        Set the Magical Rectangle enabled/disabled state in the given preset file.
        Returns True if a change was made, False otherwise.
        """
        if not preset_file or not os.path.isfile(preset_file):
            return False
        try:
            with open(preset_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()

            target_token = 'prod80_04_Magical_Rectangle@PD80_04_Magical_Rectangle.fx'
            changed = False
            for i, line in enumerate(lines):
                if line.startswith('Techniques='):
                    prefix, rest = line.split('=', 1)
                    techniques = [t.strip() for t in rest.strip().split(',') if t.strip()]
                    is_currently_enabled = target_token in techniques

                    if enabled and not is_currently_enabled:
                        # Enable: append token
                        techniques.append(target_token)
                        changed = True
                    elif not enabled and is_currently_enabled:
                        # Disable: remove token
                        techniques = [t for t in techniques if t != target_token]
                        changed = True

                    if changed:
                        lines[i] = f"Techniques={','.join(techniques)}\n"
                    break

            if changed:
                with open(preset_file, 'w', encoding='utf-8', newline='\n') as f:
                    f.writelines(lines)
                return True
            return False
        except Exception as e:
            print(f"❌ Failed to set Magical Rectangle state in '{preset_file}': {e}")
            return False

    def _toggle_magical_rectangle_in_preset(self, preset_file: str) -> bool:
        """
        Toggle the Magical Rectangle effect within the given preset `.ini` file by editing the `Techniques` line.
        Returns True if a change was made, False otherwise.
        """
        if not preset_file or not os.path.isfile(preset_file):
            return False
        try:
            # Read file lines preserving formatting
            with open(preset_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()

            target_token = 'prod80_04_Magical_Rectangle@PD80_04_Magical_Rectangle.fx'
            changed = False
            for i, line in enumerate(lines):
                if line.startswith('Techniques='):
                    prefix, rest = line.split('=', 1)
                    techniques = [t.strip() for t in rest.strip().split(',') if t.strip()]
                    if target_token in techniques:
                        # Disable: remove token
                        techniques = [t for t in techniques if t != target_token]
                        changed = True
                        action = 'disabled'
                    else:
                        # Enable: append token (place at end to avoid disrupting order)
                        techniques.append(target_token)
                        changed = True
                        action = 'enabled'
                    # Rebuild the line with LF endings
                    lines[i] = f"Techniques={','.join(techniques)}\n"
                    print(f"🔧 Magical Rectangle {action} in preset: {os.path.basename(preset_file)}")
                    break

            if changed:
                with open(preset_file, 'w', encoding='utf-8', newline='\n') as f:
                    f.writelines(lines)
                return True
            else:
                print("ℹ️ No Techniques line found; no changes made.")
                return False
        except Exception as e:
            print(f"❌ Failed to toggle Magical Rectangle in '{preset_file}': {e}")
            return False

    def listen_for_crosshair(self):
        """Listen for CROSSHAIR_KEYBIND and toggle Magical Rectangle in the active preset."""
        if not CROSSHAIR_KEYBIND:
            return
        print(f"Press {CROSSHAIR_KEYBIND} to toggle Magical Rectangle in the active preset...")
        while True:
            keyboard.wait(CROSSHAIR_KEYBIND)
            preset_abs = self._resolve_active_preset_path()
            if not preset_abs:
                print("❌ Could not resolve active preset from ReShade.ini.")
                continue
            if self._toggle_magical_rectangle_in_preset(preset_abs):
                # Optionally refresh reshade so the change applies immediately
                try:
                    reset_reshade_func()
                except Exception:
                    pass
            else:
                print("❌ Toggle operation failed or made no changes.")

    def _set_magical_center(self, preset_file: str, center_value: tuple[float, float]) -> bool:
        """
        Set the `center=` value inside the `[PD80_04_Magical_Rectangle.fx]` section of the given preset file.
        If the section or line does not exist, create/update appropriately. Returns True if written.
        """
        if not preset_file or not os.path.isfile(preset_file):
            return False
        try:
            with open(preset_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()

            section_header = '[PD80_04_Magical_Rectangle.fx]'
            center_str = f"center={center_value[0]:.6f},{center_value[1]:.6f}\n"
            in_section = False
            section_found = False
            center_set = False

            i = 0
            while i < len(lines):
                line = lines[i]
                if line.strip() == section_header:
                    section_found = True
                    in_section = True
                    i += 1
                    continue
                if in_section:
                    # Next section starts
                    if line.strip().startswith('[') and line.strip().endswith(']'):
                        # Insert center before leaving section if not present
                        if not center_set:
                            lines.insert(i, center_str)
                            center_set = True
                        in_section = False
                        # do not i += 1 to re-evaluate this new section header
                        continue
                    # Modify existing center
                    if line.startswith('center='):
                        lines[i] = center_str
                        center_set = True
                        # Stay in section for other lines
                i += 1

            # If section never found, append it and center
            if not section_found:
                lines.append(section_header + "\n")
                lines.append(center_str)

            # If section found but center not set and we were at end of file or end of section
            if section_found and not center_set:
                # append at end of file (common enough) or just after header if header was last
                # Find last occurrence of section header to place center below it if needed
                try:
                    idx = len(lines) - 1 - lines[::-1].index(section_header + "\n")
                    # Insert after header if immediate next line is another header or EOF
                    insert_pos = idx + 1
                    lines.insert(insert_pos, center_str)
                except Exception:
                    # Fallback: append
                    lines.append(center_str)

            with open(preset_file, 'w', encoding='utf-8', newline='\n') as f:
                f.writelines(lines)
            print(f"🎯 Set Magical Rectangle center to {center_value} in {os.path.basename(preset_file)}")
            return True
        except Exception as e:
            print(f"❌ Failed to set center in '{preset_file}': {e}")
            return False

    def listen_for_center_keys(self):
        """Listen for PAGE_UP and PAGE_DOWN to set Magical Rectangle center."""
        print(f"Press {CENTER_UP_KEYBIND} to center (0.5,0.5) and {CENTER_DOWN_KEYBIND} to offset (0.5,0.5+{CENTER_OFFSET}/10)...")
        
        def handle_up():
            while True:
                keyboard.wait(CENTER_UP_KEYBIND)
                preset_abs = self._resolve_active_preset_path()
                if not preset_abs:
                    print("❌ Could not resolve active preset from ReShade.ini.")
                    continue
                if self._set_magical_center(preset_abs, (0.5, 0.5)):
                    try:
                        reset_reshade_func()
                    except Exception:
                        pass
        
        def handle_down():
            while True:
                keyboard.wait(CENTER_DOWN_KEYBIND)
                preset_abs = self._resolve_active_preset_path()
                if not preset_abs:
                    print("❌ Could not resolve active preset from ReShade.ini.")
                    continue
                offset_y = 0.5 + (CENTER_OFFSET / 10.0)
                if self._set_magical_center(preset_abs, (0.5, offset_y)):
                    try:
                        reset_reshade_func()
                    except Exception:
                        pass
        
        # Run both handlers in separate threads so they can listen concurrently
        threading.Thread(target=handle_up, daemon=True).start()
        threading.Thread(target=handle_down, daemon=True).start()
        
        # Keep this thread alive
        while True:
            time.sleep(1)

    def listen_for_f10(self):
        while True:
            if AUTO_UPDATE:
                return  # Disable in auto-update mode
            keyboard.wait('F10')
            self.preview_mode = not self.preview_mode
            mode = "Preview Mode" if self.preview_mode else "Game Mode"
            print(f"🔹 Switched to {mode}")

    def preprocess_for_ocr(self, pil_image):
        """Convert PIL image to OpenCV and preprocess for better OCR accuracy"""
        cv_image = cv2.cvtColor(np.array(pil_image), cv2.COLOR_RGB2BGR)
        gray = cv2.cvtColor(cv_image, cv2.COLOR_BGR2GRAY)

        # Threshold: everything slightly darker than white becomes black
        _, thresh = cv2.threshold(gray, 247, 255, cv2.THRESH_BINARY)

        # Modify image to enhance text
        thresh = cv2.dilate(thresh, None, iterations=1)
        thresh = cv2.bitwise_not(thresh)
        thresh = cv2.resize(thresh, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        thresh = cv2.GaussianBlur(thresh, (11, 11), 0)

        # Crop to text area
        toCrop = Image.fromarray(thresh).convert("L")
        bbox = ImageOps.invert(toCrop).getbbox()
        cropped = toCrop.crop(bbox)
        thresh = np.array(cropped)

        # Show debug windows only in preview mode
        if self.preview_mode:
            cv2.imshow("Original Screenshot", cv_image)
            cv2.imshow("Processed for OCR", thresh)
            print("🧪 Press any key in a preview window to continue...")
            cv2.waitKey(0)
            cv2.destroyAllWindows()

        return thresh

    update_counter = 0
    def update_image(self):
        self.root.attributes('-topmost', True)

        self.update_counter += 1
        if self.update_counter >= 20 and clean_terminal:
            os.system('cls' if os.name == 'nt' else 'clear')
            self.update_counter = 0
            if AUTO_UPDATE:
                print("🔹 Auto-update mode enabled. Cannot use Preview Mode.")
                print("Auto-update mode enabled. Updating image every second...")
            else:
                print("🔹 Running in Game Mode. Press F10 to toggle Preview Mode.")
                print(f"Press {UPDATE_KEYBIND} to capture text and load image...")
            print(f"Press {RESET_KEYBIND} to clear the image...")
            print(f"Press {RESET_RESHADE_KEYBIND} to reset Reshade to default...")
            if CROSSHAIR_KEYBIND:
                print(f"Press {CROSSHAIR_KEYBIND} to toggle Magical Rectangle in the active preset...")
            print(f"Press {CENTER_UP_KEYBIND} to center (0.5,0.5) and {CENTER_DOWN_KEYBIND} to offset (0.5,0.5+{CENTER_OFFSET}/10)...")
            


        screenshot = pyautogui.screenshot(region=SCREENSHOT_REGION)
        processed = self.preprocess_for_ocr(screenshot)

        # OCR
        text = pytesseract.image_to_string(processed, config='--psm 6').strip()
        text = ''.join(ch for ch in unicodedata.normalize('NFKD', text) if not unicodedata.category(ch).startswith('M'))
        if not text:
            print("❌ No text detected.")
            return

        formatted = text.upper().replace(" ", "_").replace("|", "I").replace("0", "O").replace("�",r"'").replace("’",r"'").replace("“",r"'").replace("”",r"'").replace("`",r"'").replace("´",r"'").replace("VV","w").replace("1","I").replace("5","S").replace("8","B").replace("6","G").replace("2","Z").replace("—","-").replace("5","S").replace("l", "I")
        print(f"🧠 Parsed text: {formatted}")

        # Fuzzy match against known maps to tolerate OCR mistakes
        try:
            maps_list = get_all_maps()
            def _norm(s: str) -> str:
                return ''.join(ch for ch in s.upper() if ch.isalnum())

            parsed_norm = _norm(formatted)
            best = None
            best_score = 0.0
            for m in maps_list:
                score = SequenceMatcher(None, parsed_norm, _norm(m)).ratio() * 100
                if score > best_score:
                    best_score = score
                    best = m

            if best and best_score >= 85.0:
                print(f"🔎 Fuzzy matched to {best} ({best_score:.0f}%) — using that map instead.")
                formatted = best
        except Exception:
            # If fuzzy matching fails for any reason, continue with original formatted
            pass

        # Look for image file
        image_path = None
        for ext in ['.png', '.jpg', '.jpeg']:
            candidate = os.path.join(IMAGE_FOLDER, f"{formatted}{ext}")
            if os.path.exists(candidate):
                realm = get_realm(formatted)
                if realm and UPDATE_RESHADE:
                    candidate_reshade = os.path.join(get_base_path(), "reshades", f"{realm}.ini")

                    # Check current preset's Magical Rectangle state and center before switching
                    old_preset = self._resolve_active_preset_path()
                    magical_rect_enabled = False
                    magical_rect_center = None
                    if old_preset and os.path.isfile(old_preset):
                        magical_rect_enabled = self._is_magical_rectangle_enabled(old_preset)
                        magical_rect_center = self._get_magical_center(old_preset)

                    cfg = configparser.ConfigParser()
                    cfg.optionxform = str  # preserve option name case (so it stays 'PresetPath')
                    cfg.read(RESHADE_PATH, encoding="utf-8")

                    if not cfg.has_section("GENERAL"):
                        cfg.add_section("GENERAL")

                    cfg.set("GENERAL", "PresetPath", candidate_reshade)

                    with open(RESHADE_PATH, "w", encoding="utf-8", newline="\n") as f:
                        cfg.write(f)

                    print(magical_rect_enabled)
                    print(magical_rect_center)
                    # Ensure new preset has the same Magical Rectangle state and center as the old one
                    if os.path.isfile(candidate_reshade):
                        self._set_magical_rectangle_state(candidate_reshade, magical_rect_enabled)
                        if magical_rect_center is not None:
                            self._set_magical_center(candidate_reshade, magical_rect_center)

                    reset_reshade_func()

                image_path = candidate
                break

        if not image_path:
            print(f"❌ Image not found for '{formatted}'")
            return

        print(f"✅ Found image: {image_path}")
        img = Image.open(image_path)

        # Resize with aspect ratio
        if SECONDARY_MONITOR_MODE:
            img.thumbnail((round(0.7 * monitor.width), monitor.height), Image.LANCZOS)
        else:
            img.thumbnail((minimap_size_x, minimap_size_y), Image.LANCZOS)

        photo = ImageTk.PhotoImage(img)

        # Update window
        self.label.config(image=photo)
        self.label.image = photo  # prevent garbage collection

        return True

if __name__ == "__main__":
    ImageWindow()
