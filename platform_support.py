"""
Platform abstraction for DBDMap.

Everything in here exists so dbdmap.py can stay a single readable OCR loop while
still working on Hyprland, other wlroots compositors, GNOME/KDE/COSMIC (Wayland
and X11) and Windows.

Three things differ per platform:

  * capture   - how to grab a rectangle of the screen
  * window    - how to find the Dead by Daylight window (optional; we fall back
                to a static monitor region, which is correct for fullscreen)
  * overlay   - how to make a window always-on-top and click-through

The Hyprland behaviour (grim + hyprctl + Qt/Wayland) is preserved exactly; the
other backends are additions that only ever run when Hyprland's isn't available.
"""

import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading

CMD_TIMEOUT = 3

IS_WINDOWS = sys.platform == 'win32'
IS_LINUX = sys.platform.startswith('linux')
IS_MAC = sys.platform == 'darwin'


# ---------------------------------------------------------
# Paths (PyInstaller aware)
# ---------------------------------------------------------
def app_dir():
    """Directory holding maps/, config.ini, ... - next to the exe when frozen."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def bundle_dir():
    """Directory holding read-only data baked into a onefile build."""
    return getattr(sys, '_MEIPASS', app_dir())


# ---------------------------------------------------------
# Console: Windows consoles default to a legacy codepage and blow up on the
# emoji this script logs with.
# ---------------------------------------------------------
def init_console():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass
    if IS_WINDOWS:
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            # Enable ANSI so the \r-based progress lines behave.
            handle = ctypes.windll.kernel32.GetStdHandle(-11)
            mode = ctypes.c_uint32()
            if ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                ctypes.windll.kernel32.SetConsoleMode(handle, mode.value | 0x0004)
        except Exception:
            pass


# ---------------------------------------------------------
# Subprocess helpers - every external call is time-boxed.
# ---------------------------------------------------------
def run_cmd(args, timeout=CMD_TIMEOUT, text=False):
    try:
        return subprocess.run(args, capture_output=True, text=text, timeout=timeout)
    except Exception:
        return None


def fire_and_forget(args, timeout=CMD_TIMEOUT):
    """Runs a command on a throwaway thread so the main loop never waits on it."""
    def _run():
        try:
            subprocess.run(args, capture_output=True, timeout=timeout)
        except Exception:
            pass
    threading.Thread(target=_run, daemon=True).start()


# ---------------------------------------------------------
# Session detection
# ---------------------------------------------------------
HAS_HYPRCTL = bool(shutil.which('hyprctl'))
HAS_GRIM = bool(shutil.which('grim'))
HAS_OBSCMD = bool(shutil.which('obs-cmd'))
HAS_XDOTOOL = bool(shutil.which('xdotool'))

IS_WAYLAND = IS_LINUX and bool(os.environ.get('WAYLAND_DISPLAY'))
HAS_X11 = bool(os.environ.get('DISPLAY')) and not IS_WINDOWS


def _desktop():
    return (os.environ.get('XDG_CURRENT_DESKTOP', '') + ':' +
            os.environ.get('XDG_SESSION_DESKTOP', '') + ':' +
            os.environ.get('DESKTOP_SESSION', '')).lower()


IS_HYPRLAND = bool(os.environ.get('HYPRLAND_INSTANCE_SIGNATURE')) or 'hyprland' in _desktop()
IS_SWAY = bool(os.environ.get('SWAYSOCK')) or 'sway' in _desktop()
IS_KDE = 'kde' in _desktop() or 'plasma' in _desktop()
IS_GNOME = 'gnome' in _desktop()
IS_COSMIC = 'cosmic' in _desktop()

# wlroots-family compositors all speak the screencopy protocol that grim uses.
IS_WLROOTS = IS_WAYLAND and (IS_HYPRLAND or IS_SWAY or IS_COSMIC or
                             any(k in _desktop() for k in ('wayfire', 'river', 'niri', 'labwc')))


def session_summary():
    if IS_WINDOWS:
        return "Windows"
    if IS_MAC:
        return "macOS"
    kind = "Wayland" if IS_WAYLAND else ("X11" if HAS_X11 else "no display server")
    desktop = os.environ.get('XDG_CURRENT_DESKTOP') or 'unknown'
    return f"Linux / {desktop} / {kind}"


# ---------------------------------------------------------
# Qt platform plugin choice.
#
# On Hyprland (and other wlroots compositors) the native Wayland plugin works and
# is what the tuned setup already uses - keep it. On GNOME/KDE Wayland a normal
# Wayland toplevel cannot be always-on-top at all, so we deliberately run the
# overlay through XWayland where an override-redirect window can be. The game
# itself runs under XWayland via Proton, so this also lets us capture it.
# ---------------------------------------------------------
def choose_qt_platform():
    if IS_WINDOWS or IS_MAC:
        return None
    if os.environ.get('QT_QPA_PLATFORM'):
        return None  # user override wins
    if IS_WAYLAND and IS_WLROOTS:
        return 'wayland'
    if HAS_X11:
        return 'xcb'
    if IS_WAYLAND:
        return 'wayland'
    return None


# ---------------------------------------------------------
# Tesseract discovery. On Linux it is a normal package; on Windows it is almost
# never on PATH, which is why the old bundled-binary approach existed.
# ---------------------------------------------------------
def _tesseract_candidates():
    out = [
        os.path.join(bundle_dir(), 'Tesseract-OCR', 'tesseract.exe'),
        os.path.join(app_dir(), 'Tesseract-OCR', 'tesseract.exe'),
        # Layout used by the old DBDMap release zips
        os.path.join(app_dir(), 'Tesseract-OCR', 'tools', 'tesseract', 'tesseract.exe'),
    ]
    if IS_WINDOWS:
        out += [
            r'C:\Program Files\Tesseract-OCR\tesseract.exe',
            r'C:\Program Files (x86)\Tesseract-OCR\tesseract.exe',
            os.path.expandvars(r'%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe'),
            os.path.expandvars(r'%LOCALAPPDATA%\Tesseract-OCR\tesseract.exe'),
            os.path.expandvars(r'%USERPROFILE%\scoop\apps\tesseract\current\tesseract.exe'),
            r'C:\ProgramData\chocolatey\bin\tesseract.exe',
        ]
    else:
        out += [
            '/usr/bin/tesseract', '/usr/local/bin/tesseract',
            '/opt/homebrew/bin/tesseract', '/snap/bin/tesseract',
        ]
    return out


def setup_tesseract(configured_path=''):
    """Points pytesseract at a real binary. Returns (path, version) or (None, error)."""
    import pytesseract

    path = None
    if configured_path:
        configured_path = configured_path.strip().strip('"')
        if os.path.isfile(configured_path):
            path = configured_path
    if not path:
        path = shutil.which('tesseract')
    if not path:
        path = next((p for p in _tesseract_candidates() if p and os.path.isfile(p)), None)

    if not path:
        return None, "tesseract executable not found"

    pytesseract.pytesseract.tesseract_cmd = path

    # A relocatable/bundled copy needs to be told where its language data is.
    if not os.environ.get('TESSDATA_PREFIX'):
        base = os.path.dirname(path)
        for guess in (os.path.join(base, 'tessdata'),
                      os.path.join(os.path.dirname(base), 'tessdata'),
                      os.path.join(os.path.dirname(base), 'share', 'tessdata')):
            if os.path.isdir(guess):
                os.environ['TESSDATA_PREFIX'] = guess
                break

    result = run_cmd([path, '--version'], text=True, timeout=10)
    if not result or result.returncode != 0:
        return None, f"'{path}' is not a working tesseract binary"
    version = (result.stdout or '').splitlines()[0].strip() if result.stdout else 'unknown'
    return path, version


# ---------------------------------------------------------
# Linux input permissions.
#
# The `keyboard` library reads /dev/input/event* and writes /dev/uinput. Root is
# the usual way to get that, but a user in the right group with a udev rule can
# do it too - so probe the actual access instead of demanding euid 0.
# ---------------------------------------------------------
def linux_input_access():
    import glob
    if os.geteuid() == 0:
        return True, []
    missing = []
    if not os.access('/dev/uinput', os.W_OK):
        missing.append('/dev/uinput (write)')
    events = glob.glob('/dev/input/event*')
    if not events or not any(os.access(p, os.R_OK) for p in events):
        missing.append('/dev/input/event* (read)')
    return (not missing), missing


# Note: granting /dev/input + /dev/uinput access is NOT enough to drop the sudo
# requirement on Linux. The `keyboard` library additionally shells out to
# `dumpkeys` for the console keymap, which needs CAP_SYS_TTY_CONFIG, and it opens
# *every* event device rather than the ones the user can read. Root it is.


# ---------------------------------------------------------
# Screen capture backends.
#
# Coordinate spaces, because they are the easiest thing to get wrong:
#   'monitor'  - physical pixels, relative to the configured monitor's top-left
#   'layout'   - compositor layout coordinates (what hyprctl reports; logical)
#   'physical' - physical pixels, absolute across the whole desktop
# ---------------------------------------------------------
class CaptureError(Exception):
    pass


def _pil_open(data):
    from PIL import Image
    return Image.open(io.BytesIO(data))


# Session-bus env that a desktop screenshot tool needs to find its compositor.
_SESSION_ENV_VARS = (
    'DBUS_SESSION_BUS_ADDRESS', 'XDG_RUNTIME_DIR', 'WAYLAND_DISPLAY',
    'DISPLAY', 'XAUTHORITY', 'XDG_CURRENT_DESKTOP', 'XDG_SESSION_TYPE',
)


def _as_user_argv(argv):
    """Wraps a command so it runs as the user who invoked sudo.

    DBDMap needs root for /dev/input, but D-Bus authenticates by peer uid and
    flatly refuses a root client on a user's session bus. Every screenshot route
    that goes through the session bus - xdg-desktop-portal, GNOME Shell,
    Spectacle - therefore has to be run back as the original user.

    `grim` is exempt: it talks to the Wayland socket directly, which root can
    open regardless, which is why Hyprland never needed any of this.
    """
    if IS_WINDOWS or os.geteuid() != 0:
        return argv
    uid = os.environ.get('SUDO_UID')
    user = os.environ.get('SUDO_USER')
    if not uid or not user:
        return argv

    env_argv = ['env'] + [f'{var}={os.environ[var]}'
                          for var in _SESSION_ENV_VARS if os.environ.get(var)]

    # We are already root, so neither of these can prompt for a password.
    if shutil.which('runuser'):
        return ['runuser', '-u', user, '--'] + env_argv + argv
    if shutil.which('sudo'):
        return ['sudo', '-n', '-u', user] + env_argv + argv
    return argv


def _user_writable_temp(suffix='.png'):
    """A temp path a de-privileged child can write to. mkstemp gives us a
    root-owned 0600 file, which the child could not open."""
    fd, path = tempfile.mkstemp(suffix=suffix, prefix='dbdmap-')
    os.close(fd)
    try:
        os.unlink(path)
    except OSError:
        pass
    return path


def _grab_grim(layout_rect):
    x, y, w, h = layout_rect
    result = run_cmd(["grim", "-g", f"{x},{y} {w}x{h}", "-"], timeout=5)
    if not result or result.returncode != 0 or not result.stdout:
        return None
    try:
        return _pil_open(result.stdout)
    except Exception:
        return None


_mss_local = threading.local()


def _grab_mss(phys_rect):
    import mss
    from PIL import Image
    x, y, w, h = phys_rect
    sct = getattr(_mss_local, 'sct', None)
    if sct is None:
        sct = _mss_local.sct = mss.mss()
    try:
        raw = sct.grab({'left': x, 'top': y, 'width': w, 'height': h})
    except Exception:
        # A display reconfiguration invalidates the handle; rebuild once.
        try:
            _mss_local.sct = mss.mss()
            raw = _mss_local.sct.grab({'left': x, 'top': y, 'width': w, 'height': h})
        except Exception:
            return None
    return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")


def _grab_pyautogui(phys_rect):
    import pyautogui
    try:
        return pyautogui.screenshot(region=tuple(phys_rect))
    except Exception:
        return None


def _grab_via_fullscreen_tool(argv_builder, phys_rect):
    """KDE/GNOME Wayland have no region-grab CLI we can trust, so take the whole
    desktop and crop. Slower, but correct - and OCR only runs about once a second."""
    from PIL import Image
    path = _user_writable_temp()
    try:
        result = run_cmd(_as_user_argv(argv_builder(path)), timeout=15)
        if not result or result.returncode != 0 or not os.path.isfile(path):
            return None
        with Image.open(path) as img:
            img.load()
            x, y, w, h = phys_rect
            # Full-desktop shots on scaled or multi-monitor setups may come back
            # at a different pixel size or origin than the layout suggests.
            # Clamp instead of crashing, and never crop outside the image - PIL
            # would silently pad with black and poison the OCR.
            x, y = max(0, x), max(0, y)
            x2, y2 = min(x + w, img.width), min(y + h, img.height)
            if x >= img.width or y >= img.height or x2 <= x or y2 <= y:
                return None
            return img.crop((x, y, x2, y2)).convert("RGB")
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _grab_spectacle(phys_rect):
    return _grab_via_fullscreen_tool(
        lambda p: ['spectacle', '-b', '-n', '-f', '-o', p], phys_rect)


def _grab_gnome_screenshot(phys_rect):
    return _grab_via_fullscreen_tool(
        lambda p: ['gnome-screenshot', '-f', p], phys_rect)


def _grab_gnome_shell(layout_rect):
    """GNOME Shell's own D-Bus API. Takes logical coordinates."""
    from PIL import Image
    x, y, w, h = layout_rect
    path = _user_writable_temp()
    try:
        result = run_cmd(_as_user_argv([
            'gdbus', 'call', '--session',
            '--dest', 'org.gnome.Shell.Screenshot',
            '--object-path', '/org/gnome/Shell/Screenshot',
            '--method', 'org.gnome.Shell.Screenshot.ScreenshotArea',
            str(x), str(y), str(w), str(h), 'false', path,
        ]), text=True, timeout=15)
        if not result or result.returncode != 0:
            return None
        if 'true' not in (result.stdout or '').lower():
            return None
        with Image.open(path) as img:
            img.load()
            return img.convert("RGB")
    except Exception:
        return None
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _portal_screenshot_path():
    """Asks xdg-desktop-portal for a full-desktop screenshot and returns the file
    path it produced. Must run as the session user - see _as_user_argv()."""
    from urllib.parse import urlparse, unquote
    try:
        from jeepney import DBusAddress, new_method_call, MatchRule, message_bus
        from jeepney.io.blocking import open_dbus_connection, Proxy
    except ImportError:
        return None

    import secrets
    token = 'dbdmap' + secrets.token_hex(8)
    try:
        conn = open_dbus_connection(bus='SESSION')
    except Exception:
        return None
    try:
        sender = conn.unique_name.lstrip(':').replace('.', '_')
        request_path = f'/org/freedesktop/portal/desktop/request/{sender}/{token}'

        rule = MatchRule(type='signal', interface='org.freedesktop.portal.Request',
                         member='Response', path=request_path)
        Proxy(message_bus, conn).AddMatch(rule)

        portal = DBusAddress('/org/freedesktop/portal/desktop',
                             bus_name='org.freedesktop.portal.Desktop',
                             interface='org.freedesktop.portal.Screenshot')
        msg = new_method_call(portal, 'Screenshot', 'sa{sv}', (
            '', {'interactive': ('b', False), 'handle_token': ('s', token)},
        ))
        conn.send_and_get_reply(msg, timeout=10)

        deadline = 15
        uri = None
        while deadline > 0:
            reply = conn.receive(timeout=deadline)
            if reply.header.fields.get(1) == request_path and \
                    reply.header.fields.get(3) == 'Response':
                code, results = reply.body
                if code != 0:
                    return None
                uri = results.get('uri', (None, None))[1]
                break
            deadline -= 1
        if not uri:
            return None
        return unquote(urlparse(uri).path)
    except Exception:
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _grab_portal(phys_rect):
    """xdg-desktop-portal - the universal Wayland fallback. On GNOME/KDE the
    first call raises a one-time permission prompt."""
    from PIL import Image

    if os.geteuid() == 0 and os.environ.get('SUDO_UID'):
        # A root process cannot authenticate to the user's session bus at all,
        # so the D-Bus half runs in a de-privileged child that just prints the
        # resulting path. Re-running this module is cheaper and far safer than
        # fork()ing an interpreter that already has Qt and listener threads.
        argv = _as_user_argv([sys.executable, os.path.abspath(__file__), '--portal-grab'])
        result = run_cmd(argv, text=True, timeout=25)
        if not result or result.returncode != 0:
            return None
        path = (result.stdout or '').strip()
    else:
        path = _portal_screenshot_path()

    if not path or not os.path.isfile(path):
        return None

    try:
        with Image.open(path) as img:
            img.load()
            x, y, w, h = phys_rect
            x, y = max(0, x), max(0, y)
            x2, y2 = min(x + w, img.width), min(y + h, img.height)
            if x2 <= x or y2 <= y:
                return None
            return img.crop((x, y, x2, y2)).convert("RGB")
    except Exception:
        return None
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


# name -> (callable, coordinate space it expects, human label)
CAPTURE_BACKENDS = {
    'grim':             (_grab_grim, 'layout', 'grim (wlroots screencopy)'),
    'mss':              (_grab_mss, 'physical', 'mss (X11/Windows direct capture)'),
    'pyautogui':        (_grab_pyautogui, 'physical', 'pyautogui'),
    'spectacle':        (_grab_spectacle, 'physical', 'Spectacle (KDE Wayland)'),
    'gnome-shell':      (_grab_gnome_shell, 'layout', 'GNOME Shell D-Bus'),
    'gnome-screenshot': (_grab_gnome_screenshot, 'physical', 'gnome-screenshot'),
    'portal':           (_grab_portal, 'physical', 'xdg-desktop-portal'),
}


def _candidate_capture_order():
    if IS_WINDOWS or IS_MAC:
        return ['mss', 'pyautogui']

    order = []
    if IS_WAYLAND:
        # Direct X11 capture is NOT usable here even when XWayland is running:
        # Xwayland is rootless, so the X root window holds no window pixels and a
        # grab of it comes back solid black. Every option below asks the
        # compositor instead.
        if HAS_GRIM:
            order.append('grim')      # Hyprland, Sway, COSMIC, Wayfire, river...
        if IS_KDE:
            order.append('spectacle')
        if IS_GNOME:
            order += ['gnome-shell', 'gnome-screenshot']
        for name in ('spectacle', 'gnome-shell', 'gnome-screenshot'):
            if name not in order:
                order.append(name)
        # Generic fallback that works on any compositor with a portal. Only
        # reached when everything above failed, so the one-time permission
        # prompt it may raise on GNOME/KDE is a fair trade for working at all.
        order.append('portal')
        # Last-ditch. The blank-frame check in _probe() throws these out when the
        # X root window is empty, which under Xwayland it always is.
        order += ['mss', 'pyautogui']
    elif HAS_X11:
        order += ['mss', 'pyautogui']
    return order


def is_blank(image):
    """True for a uniform image - the signature of a capture backend that ran
    successfully but grabbed nothing (rootless Xwayland, a permission-less
    portal, a compositor that handed back an empty buffer)."""
    if image is None:
        return True
    try:
        low, high = image.convert('L').getextrema()
        return low == high
    except Exception:
        return False


def backend_available(name):
    if name == 'grim':
        return HAS_GRIM
    if name == 'spectacle':
        return bool(shutil.which('spectacle'))
    if name == 'gnome-screenshot':
        return bool(shutil.which('gnome-screenshot'))
    if name == 'gnome-shell':
        return bool(shutil.which('gdbus'))
    if name in ('mss', 'pyautogui'):
        try:
            __import__(name)
            return True
        except Exception:
            return False
    if name == 'portal':
        try:
            __import__('jeepney')
            return True
        except Exception:
            return False
    return False


class ScreenCapture:
    """Picks a working capture backend once, then converts coordinates for it."""

    def __init__(self, monitor, preferred='auto', log=print):
        self.monitor = monitor           # dict from get_monitors()
        self.log = log
        self.name = None
        self.space = None
        self._grab = None
        self._select(preferred)

    # -- coordinate conversion ------------------------------------------------
    def _to_layout(self, rect, space):
        x, y, w, h = rect
        m, scale = self.monitor, self.monitor['scale']
        if space == 'layout':
            return (int(x), int(y), int(w), int(h))
        if space == 'monitor':
            # Identical arithmetic to the original Hyprland code path.
            return (int(m['x'] + x / scale), int(m['y'] + y / scale),
                    int(w / scale), int(h / scale))
        # physical -> layout
        return (int(m['x'] + (x - m['phys_x']) / scale),
                int(m['y'] + (y - m['phys_y']) / scale),
                int(w / scale), int(h / scale))

    def _to_physical(self, rect, space):
        x, y, w, h = rect
        m, scale = self.monitor, self.monitor['scale']
        if space == 'physical':
            return (int(x), int(y), int(w), int(h))
        if space == 'monitor':
            return (int(m['phys_x'] + x), int(m['phys_y'] + y), int(w), int(h))
        # layout -> physical
        return (int(m['phys_x'] + (x - m['x']) * scale),
                int(m['phys_y'] + (y - m['y']) * scale),
                int(w * scale), int(h * scale))

    # -- selection ------------------------------------------------------------
    def _probe(self, name, strict=True):
        """Grabs a big central chunk of the monitor. `strict` additionally
        requires the result to contain more than one colour, which is what
        separates a real capture from a black rectangle."""
        grab, space, _label = CAPTURE_BACKENDS[name]
        m = self.monitor
        probe_rect = (m['width'] // 4, m['height'] // 4,
                      max(64, m['width'] // 2), max(64, m['height'] // 2))
        rect = self._to_layout(probe_rect, 'monitor') if space == 'layout' \
            else self._to_physical(probe_rect, 'monitor')
        try:
            image = grab(rect)
        except Exception:
            return False
        if image is None or image.width <= 0 or image.height <= 0:
            return False
        return not (strict and is_blank(image))

    def _select(self, preferred):
        if preferred and preferred != 'auto':
            if preferred not in CAPTURE_BACKENDS:
                self.log(f"⚠️  Unknown capture_backend '{preferred}' - falling back to auto-detection.")
            elif not backend_available(preferred):
                self.log(f"⚠️  capture_backend '{preferred}' is not installed - falling back to auto-detection.")
            else:
                self._use(preferred)
                return

        candidates = [n for n in _candidate_capture_order() if backend_available(n)]

        for name in candidates:
            if self._probe(name, strict=True):
                self._use(name)
                return

        # Everything came back blank. That is almost always a real problem, but a
        # completely uniform screen (solid wallpaper, nothing open) looks the
        # same, so rather than refuse to start we take the first backend that
        # produced an image at all and say so.
        for name in candidates:
            if self._probe(name, strict=False):
                self._use(name)
                self.log("⚠️  Every capture method returned a blank image during startup. "
                         "Continuing anyway - if no map is ever detected, see "
                         "'capture_backend' in config.ini.")
                return

        raise CaptureError(
            "No working screen-capture method was found.\n"
            + _capture_help())

    def _use(self, name):
        self._grab, self.space, label = CAPTURE_BACKENDS[name]
        self.name = name
        self.log(f"🖵  Screen capture: {label}")

    # -- public ---------------------------------------------------------------
    def grab(self, rect, space='monitor'):
        target = self._to_layout(rect, space) if self.space == 'layout' \
            else self._to_physical(rect, space)
        if target[2] <= 0 or target[3] <= 0:
            return None
        try:
            return self._grab(target)
        except Exception:
            return None


def probe_all_backends(monitor):
    """Diagnostics helper: tries every installed backend once against `monitor`
    and reports what it produced. Returns [(name, verdict), ...]."""
    results = []
    for name in CAPTURE_BACKENDS:
        if not backend_available(name):
            results.append((name, "not installed"))
            continue
        capture = ScreenCapture.__new__(ScreenCapture)
        capture.monitor, capture.log, capture.name = monitor, lambda _m: None, name
        capture._grab, capture.space, _label = CAPTURE_BACKENDS[name]
        region = (monitor['width'] // 4, monitor['height'] // 4,
                  max(64, monitor['width'] // 2), max(64, monitor['height'] // 2))
        image = capture.grab(region, space='monitor')
        if image is None:
            results.append((name, "no image"))
        elif is_blank(image):
            results.append((name, "BLANK - captures nothing usable"))
        else:
            results.append((name, f"OK {image.width}x{image.height}"))
    return results


def _capture_help():
    if IS_WINDOWS:
        return "Install the Python dependencies with:  pip install -r requirements.txt"
    lines = ["Install one of the following for your desktop:"]
    if IS_WAYLAND:
        lines += [
            "  wlroots (Hyprland, Sway, COSMIC, Wayfire): grim",
            "  KDE Plasma:                                 spectacle",
            "  GNOME:                                      gnome-screenshot (or keep GNOME Shell's D-Bus API enabled)",
            "  Anything else:                              xdg-desktop-portal + `pip install jeepney`,",
            "                                              then set capture_backend = portal in config.ini",
        ]
    else:
        lines.append("  X11: pip install mss")
    return "\n".join(lines)


# ---------------------------------------------------------
# Monitor enumeration.
#
# Returned dicts use:
#   width/height  physical pixels
#   x/y           layout (logical) coordinates - what the compositor/Qt reports
#   phys_x/phys_y absolute physical pixel origin - what mss/pyautogui want
# ---------------------------------------------------------
def _hyprctl_monitors(hyprctl_json):
    data = hyprctl_json('monitors')
    if not data:
        return None
    try:
        monitors = []
        for m in data:
            monitors.append({
                'name': m['name'],
                'width': int(m['width']), 'height': int(m['height']),
                'scale': float(m['scale']),
                'x': int(m['x']), 'y': int(m['y']),
                'is_primary': m.get('focused', False),
            })
        return monitors or None
    except Exception:
        return None


def _qt_monitors(app):
    monitors = []
    for i, screen in enumerate(app.screens()):
        geom = screen.geometry()          # logical coordinates
        dpr = float(screen.devicePixelRatio() or 1.0)
        monitors.append({
            'name': screen.name() or f"Monitor {i + 1}",
            'width': int(round(geom.width() * dpr)),
            'height': int(round(geom.height() * dpr)),
            'scale': dpr,
            'x': geom.x(), 'y': geom.y(),
            'is_primary': screen == app.primaryScreen(),
        })
    return monitors


def _attach_physical_origins(monitors):
    """mss knows the real physical layout; use it when we can, otherwise derive
    the origin from the layout coordinates and the scale factor."""
    physical = None
    try:
        import mss
        with mss.mss() as sct:
            physical = [dict(m) for m in sct.monitors[1:]]
    except Exception:
        physical = None

    for m in monitors:
        match = None
        if physical:
            match = next((p for p in physical
                          if p['width'] == m['width'] and p['height'] == m['height']), None)
            if match:
                physical.remove(match)
        if match:
            m['phys_x'], m['phys_y'] = int(match['left']), int(match['top'])
        else:
            m['phys_x'] = int(round(m['x'] * m['scale']))
            m['phys_y'] = int(round(m['y'] * m['scale']))
    return monitors


def get_monitors(app, hyprctl_json=None):
    monitors = None
    if hyprctl_json is not None and HAS_HYPRCTL:
        monitors = _hyprctl_monitors(hyprctl_json)
    if not monitors:
        monitors = _qt_monitors(app)
    return _attach_physical_origins(monitors)


def pick_monitor(monitors, config):
    """Resolves the configured monitor, preferring name then index then size."""
    if not monitors:
        return None
    general = config['General']
    name = general.get('monitor_name', '')
    for m in monitors:
        if name and m['name'] == name:
            return m

    index = general.get('monitor_index', '')
    if index.strip().isdigit():
        i = int(index)
        if 0 <= i < len(monitors):
            return monitors[i]

    resolution = general.get('resolution', '')
    match = re.fullmatch(r'\s*(\d+)\s*x\s*(\d+)\s*', resolution)
    if match:
        w, h = int(match.group(1)), int(match.group(2))
        for m in monitors:
            if m['width'] == w and m['height'] == h:
                return m
    return monitors[0]


def synthetic_monitor(config):
    """Last resort when nothing can enumerate screens (headless-ish setups)."""
    general = config['General']
    w, h = 2560, 1440
    match = re.fullmatch(r'\s*(\d+)\s*x\s*(\d+)\s*', general.get('resolution', ''))
    if match:
        w, h = int(match.group(1)), int(match.group(2))
    scale = float(general.get('monitor_scale', '1.0') or 1.0)
    x = int(general.get('monitor_x', '0') or 0)
    y = int(general.get('monitor_y', '0') or 0)
    return {
        'name': general.get('monitor_name', 'Manual'),
        'width': w, 'height': h, 'scale': scale,
        'x': x, 'y': y,
        'phys_x': int(round(x * scale)), 'phys_y': int(round(y * scale)),
        'is_primary': True,
    }


# ---------------------------------------------------------
# Game window lookup.
#
# Returns (found, rect, space) - rect may be None, in which case the caller uses
# the static monitor region, which is what a fullscreen game occupies anyway.
# ---------------------------------------------------------
DBD_PROCESS_NAMES = {
    'deadbydaylight', 'deadbydaylight.exe',
    'deadbydaylight-win64-shipping.exe', 'deadbydaylight-egs-shipping.exe',
    'deadbydayligh',  # Linux truncates comm to 15 chars
}


def _win_dbd_rect():
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32

    found = []
    title_buf = ctypes.create_unicode_buffer(512)
    class_buf = ctypes.create_unicode_buffer(256)

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        user32.GetWindowTextW(hwnd, title_buf, 512)
        title = title_buf.value or ''
        if 'deadbydaylight' not in title.lower().replace(' ', ''):
            return True
        user32.GetClassNameW(hwnd, class_buf, 256)
        rect = wintypes.RECT()
        if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
            return True
        origin = wintypes.POINT(rect.left, rect.top)
        user32.ClientToScreen(hwnd, ctypes.byref(origin))
        w, h = rect.right - rect.left, rect.bottom - rect.top
        if w > 100 and h > 100:
            found.append((origin.x, origin.y, w, h))
        return False

    try:
        user32.EnumWindows(callback, 0)
    except Exception:
        return None
    return found[0] if found else None


def _x11_dbd_rect():
    if not HAS_XDOTOOL:
        return None
    result = run_cmd(['xdotool', 'search', '--name', 'DeadByDaylight'], text=True)
    if not result or result.returncode != 0 or not result.stdout.strip():
        return None
    win_id = result.stdout.split()[0]
    geom = run_cmd(['xdotool', 'getwindowgeometry', '--shell', win_id], text=True)
    if not geom or geom.returncode != 0:
        return None
    values = {}
    for line in geom.stdout.splitlines():
        if '=' in line:
            key, _, value = line.partition('=')
            values[key.strip()] = value.strip()
    try:
        return (int(values['X']), int(values['Y']), int(values['WIDTH']), int(values['HEIGHT']))
    except (KeyError, ValueError):
        return None


def _process_running():
    import psutil
    for p in psutil.process_iter(['name', 'cmdline']):
        try:
            name = (p.info.get('name') or '').lower()
            if name in DBD_PROCESS_NAMES:
                return True
            cmdline = p.info.get('cmdline') or []
            if any('deadbydaylight' in str(part).lower() for part in cmdline):
                return True
        except Exception:
            continue
    return False


def find_dbd_window(hyprctl_json=None):
    if hyprctl_json is not None and HAS_HYPRCTL:
        clients = hyprctl_json('clients')
        if clients:
            for client in clients:
                if 'steam_app_381210' in client.get('class', '') or \
                        'DeadByDaylight' in client.get('title', ''):
                    return True, (client['at'][0], client['at'][1],
                                  client['size'][0], client['size'][1]), 'layout'

    if IS_WINDOWS:
        rect = _win_dbd_rect()
        if rect:
            return True, rect, 'physical'
    elif HAS_X11 and not IS_WLROOTS:
        rect = _x11_dbd_rect()
        if rect:
            return True, rect, 'physical'

    if _process_running():
        return True, None, None
    return False, None, None


# ---------------------------------------------------------
# Overlay platform glue.
# ---------------------------------------------------------
def apply_overlay_platform_tweaks(widget):
    """Makes the overlay behave like an overlay rather than an app window.

    Qt's WindowTransparentForInput covers click-through on every backend, but
    Windows additionally needs the extended styles or the window shows up in the
    taskbar, steals focus and loses topmost to a fullscreen game.
    """
    if not IS_WINDOWS:
        return
    try:
        import ctypes
        user32 = ctypes.windll.user32
        hwnd = int(widget.winId())

        GWL_EXSTYLE = -20
        WS_EX_LAYERED = 0x00080000
        WS_EX_TRANSPARENT = 0x00000020
        WS_EX_TOOLWINDOW = 0x00000080
        WS_EX_NOACTIVATE = 0x08000000
        WS_EX_TOPMOST = 0x00000008

        get_long = getattr(user32, 'GetWindowLongPtrW', user32.GetWindowLongW)
        set_long = getattr(user32, 'SetWindowLongPtrW', user32.SetWindowLongW)
        get_long.restype = ctypes.c_ssize_t
        set_long.restype = ctypes.c_ssize_t

        styles = get_long(hwnd, GWL_EXSTYLE)
        styles |= (WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW |
                   WS_EX_NOACTIVATE | WS_EX_TOPMOST)
        set_long(hwnd, GWL_EXSTYLE, styles)
    except Exception:
        pass


def raise_overlay(widget):
    """Re-asserts topmost. Games flip themselves above us when they take focus."""
    if not IS_WINDOWS:
        return
    try:
        import ctypes
        user32 = ctypes.windll.user32
        HWND_TOPMOST = -1
        SWP_NOMOVE, SWP_NOSIZE, SWP_NOACTIVATE = 0x0002, 0x0001, 0x0010
        user32.SetWindowPos(int(widget.winId()), HWND_TOPMOST, 0, 0, 0, 0,
                            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
    except Exception:
        pass


def overlay_notes():
    """Advice printed at startup when the platform has known caveats."""
    notes = []
    if IS_WINDOWS:
        notes.append("Set Dead by Daylight to Borderless Window - exclusive "
                     "fullscreen draws over every overlay on Windows.")
    elif IS_WAYLAND and not IS_WLROOTS:
        if HAS_X11:
            notes.append("Running the overlay through XWayland so it can stay on top; "
                         "your compositor has no always-on-top protocol for regular windows.")
        else:
            notes.append("No XWayland available - the overlay may be hidden behind the game. "
                         "Install XWayland, or run the game in a window.")
    return notes


# ---------------------------------------------------------
# Helper entry point.
#
# Invoked as `python platform_support.py --portal-grab` by _grab_portal() when
# DBDMap is running as root, so the D-Bus conversation happens under the
# session user's uid. Prints the screenshot path on stdout.
# ---------------------------------------------------------
if __name__ == '__main__':
    if '--portal-grab' in sys.argv:
        _path = _portal_screenshot_path()
        if _path:
            print(_path)
            sys.exit(0)
        sys.exit(1)
    print(session_summary())
