"""
Cross-platform smoke test.

Verifies the parts of DBDMap that don't need a running game: that the module
imports, that Tesseract is found, that the OCR pipeline recognises rendered map
names, and that the coordinate maths for every capture backend is self
consistent. Run on Linux and Windows in CI.

    python tests/smoke_test.py
"""

import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

# Qt needs *a* platform plugin; CI has no display.
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

failures = []


def check(name, condition, detail=''):
    status = 'PASS' if condition else 'FAIL'
    print(f"  [{status}] {name}" + (f" - {detail}" if detail else ''))
    if not condition:
        failures.append(name)
    return condition


def load_dbdmap():
    """Executes dbdmap.py under a name other than __main__, so its preflight
    guards and main loop stay inert."""
    import types
    module = types.ModuleType('dbdmap_under_test')
    module.__file__ = os.path.join(ROOT, 'dbdmap.py')
    with open(module.__file__, encoding='utf-8') as f:
        source = f.read()
    exec(compile(source, module.__file__, 'exec'), module.__dict__)
    sys.modules['dbdmap_under_test'] = module
    return module


print("== imports ==")
import platform_support as ps
check('platform_support imports', True, ps.session_summary())
dbd = load_dbdmap()
check('dbdmap imports', True)

print("\n== tesseract ==")
path, info = ps.setup_tesseract()
check('tesseract found', path is not None, info)

print("\n== map catalogue ==")
maps_data, realm_presets, map_to_realm = dbd.analyze_maps()
known = [m for maps in maps_data.values() for m in maps]
check('realms discovered', len(maps_data) > 0, f"{len(maps_data)} realms")
check('maps discovered', len(known) > 0, f"{len(known)} maps")
check('every map maps to a realm', all(m in map_to_realm for m in known))
check('default preset is last', realm_presets[-1][0] == 'DEFAULT')

if path:
    print("\n== OCR pipeline ==")
    from PIL import Image, ImageDraw, ImageFont
    import pytesseract

    def font_for(size):
        for candidate in (
            '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
            '/usr/share/fonts/TTF/DejaVuSans-Bold.ttf',
            r'C:\Windows\Fonts\arialbd.ttf',
            r'C:\Windows\Fonts\segoeuib.ttf',
        ):
            if os.path.exists(candidate):
                return ImageFont.truetype(candidate, size)
        return ImageFont.load_default()

    font = font_for(34)
    threshold = 85
    for name in known[:8]:
        label = name.replace('_', ' ')
        image = Image.new('RGB', (1200, 55), (18, 18, 20))
        ImageDraw.Draw(image).text((6, 6), label, fill=(235, 235, 230), font=font)
        processed = dbd.preprocess_for_ocr(image)
        raw = pytesseract.image_to_string(processed, config='--psm 6').strip()
        best, score = dbd.best_map_match(raw, known)
        check(f'OCR {label}', best == name and score >= threshold,
              f'got {best} @ {score:.0f}%')

print("\n== capture backends ==")
from PyQt6.QtWidgets import QApplication
app = QApplication.instance() or QApplication(sys.argv)
monitors = ps.get_monitors(app, dbd.hyprctl_json)
check('at least one monitor', len(monitors) > 0,
      ', '.join(f"{m['name']} {m['width']}x{m['height']}@{m['scale']}" for m in monitors))

if monitors:
    monitor = monitors[0]
    for key in ('width', 'height', 'scale', 'x', 'y', 'phys_x', 'phys_y'):
        if not check(f'monitor has {key}', key in monitor):
            break

    # Coordinate conversions must round-trip regardless of scale factor.
    probe = ps.ScreenCapture.__new__(ps.ScreenCapture)
    probe.monitor = monitor
    region = (100, 200, 640, 48)
    for space in ('layout', 'physical'):
        probe.space = space
        forward = probe._to_layout(region, 'monitor') if space == 'layout' \
            else probe._to_physical(region, 'monitor')
        back = probe._to_physical(forward, space) if space == 'physical' \
            else probe._to_layout(forward, 'layout')
        check(f'{space} conversion is stable', back == forward, f'{region} -> {forward}')

    print("\n  backend availability:")
    for name, verdict in ps.probe_all_backends(monitor):
        print(f"    {name:<17} {verdict}")

print("\n== overlay ==")
import configparser
config = configparser.ConfigParser()
config.read_string(open(os.path.join(ROOT, 'config.ini.example')).read())
try:
    overlay = dbd.PersistentOverlay(config)
    rect = dbd.compute_overlay_rect(config, app, monitors[0] if monitors else ps.synthetic_monitor(config))
    check('overlay constructs', True, f'placement {rect}')
    check('overlay size matches config', rect[2] == 350 and rect[3] == 350)
    overlay.hide_overlay()
except Exception as e:
    check('overlay constructs', False, repr(e))

print("\n" + "=" * 40)
if failures:
    print(f"FAILED ({len(failures)}): " + ', '.join(failures))
    sys.exit(1)
print("All smoke tests passed.")
