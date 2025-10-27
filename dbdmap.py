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
SCREENSHOT_REGION = tuple(map(int, config.get('DEFAULT', 'SCREENSHOT_REGION').split(',')))  # (left, top, width, height)
UPDATE_KEYBIND = config.get('DEFAULT', 'UPDATE_KEYBIND')  # Key to capture screenshot and update image
RESET_KEYBIND = config.get('DEFAULT', 'RESET_KEYBIND')  # Key to reset image
AUTO_UPDATE = config.getboolean('DEFAULT', 'AUTO_UPDATE')  # If True, automatically update image every second
SECONDARY_MONITOR_MODE = config.getboolean('DEFAULT', 'SECONDARY_MONITOR_MODE') # If True, use 2nd monitor fullscreen if available
MINIMAP_SCALE = config.getfloat('DEFAULT', 'MINIMAP_SCALE')  # Scale factor for minimap display
MINIMAP_POSITION = config.get('DEFAULT', 'MINIMAP_POSITION').lower()  # Position of minimap: 'left' or 'right'
MINIMAP_OFFSET_X = config.getint('DEFAULT', 'MINIMAP_OFFSET_X')  # X offset for minimap position
MINIMAP_OFFSET_Y = config.getint('DEFAULT', 'MINIMAP_OFFSET_Y')  # Y offset for minimap position
MINIMAP_ALPHA = config.getfloat('DEFAULT', 'MINIMAP_ALPHA')  # Transparency for minimap window (0.0 to 1.0)
# ----------------

monitors = get_monitors()
if SECONDARY_MONITOR_MODE:
    monitor = monitors[1] if len(monitors) > 1 else monitors[0]
else:
    monitor = monitors[0]

minimap_size_x = round((monitor.width / 6) * MINIMAP_SCALE)
minimap_size_y = round((monitor.width / 6) * MINIMAP_SCALE)

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
        threading.Thread(target=self.listen_for_f10, daemon=True).start()

        self.root.mainloop()

    def listen_for_key(self):
        if not AUTO_UPDATE:
            print(f"Press {UPDATE_KEYBIND} to capture text and load image...")
            while True:
                keyboard.wait(UPDATE_KEYBIND)
                self.update_image()
                time.sleep(0.5)
                self.update_image()
                time.sleep(0.5)
                self.update_image()
        else:
            print("Auto-update mode enabled. Updating image every second...")
            while True: 
                if "DeadByDaylight.exe" in (i.name() for i in psutil.process_iter()) or "DeadByDaylight-EGS-Shipping.exe" in (i.name() for i in psutil.process_iter()):
                    self.update_image()
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
        self.update_counter += 1
        if self.update_counter >= 20:
            os.system('cls' if os.name == 'nt' else 'clear')
            self.update_counter = 0
            if AUTO_UPDATE:
                print("🔹 Auto-update mode enabled. Cannot use Preview Mode.")
                print("Auto-update mode enabled. Updating image every second...")
            else:
                print("🔹 Running in Game Mode. Press F10 to toggle Preview Mode.")
                print(f"Press {UPDATE_KEYBIND} to capture text and load image...")
            print(f"Press {RESET_KEYBIND} to clear the image...")
            


        screenshot = pyautogui.screenshot(region=SCREENSHOT_REGION)
        processed = self.preprocess_for_ocr(screenshot)

        # OCR
        text = pytesseract.image_to_string(processed, config='--psm 6').strip()
        if not text:
            print("❌ No text detected.")
            return

        formatted = text.upper().replace(" ", "_").replace("|", "I").replace("0", "O")
        print(f"🧠 Parsed text: {formatted}")

        # Look for image file
        image_path = None
        for ext in ['.png', '.jpg', '.jpeg']:
            candidate = os.path.join(IMAGE_FOLDER, f"{formatted}{ext}")
            if os.path.exists(candidate):
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

if __name__ == "__main__":
    ImageWindow()
