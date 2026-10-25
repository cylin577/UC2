import sys
import os
import time
# --- Early Splash Screen (before ALL imports) ---
def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

# Fix PyTorch DLL loading issue on Windows
import platform
if platform.system() == "Windows":
    import ctypes
    from importlib.util import find_spec
    try:
        if (spec := find_spec("torch")) and spec.origin and os.path.exists(
            dll_path := os.path.join(os.path.dirname(spec.origin), "lib", "c10.dll")
        ):
            ctypes.CDLL(os.path.normpath(dll_path))
    except Exception:
        pass

from PyQt6.QtWidgets import QApplication, QSplashScreen
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap, QColor

_app = QApplication(sys.argv)
splash_path = resource_path("resources/splash.png")
_splash_pix = QPixmap(splash_path)
_splash = QSplashScreen(_splash_pix, Qt.WindowType.WindowStaysOnTopHint)
_splash.show()
_splash.showMessage("Loading modules...", Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignCenter, QColor("white"))
time.sleep(3) #Wait so user can admire my beautiful splash screen
_app.processEvents()

# --- All other imports (after splash is visible) ---
import math
import struct
import socket
import threading
import json
import cv2
import numpy as np
import pygame
from PyQt6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QFormLayout, QLineEdit, QPushButton, 
                             QCheckBox, QDialog, QComboBox, QLabel, QMessageBox,
                             QGroupBox, QTextEdit, QFileDialog, QSlider)
from PyQt6.QtCore import (QTimer, QSettings, QPoint, QByteArray, 
                          QEvent, QObject, pyqtSignal)
from PyQt6.QtGui import QPainter, QPen, QMouseEvent, QCloseEvent, QImage
from PyQt6.QtNetwork import QUdpSocket, QHostAddress
try:
    from ai_agent import AIManager
except ImportError:
    AIManager = None

from devices import (DeviceUpdateSignals, default_device_id, device_by_id,
                     load_devices, screen_layout, start_update_check)

device_data = load_devices()

_splash.showMessage("Imports complete...", Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignCenter, QColor("white"))
_app.processEvents()

# Custom Splash
pyi_splash = None

# Constants
CPAD_BOUND = 0x5d0
CPP_BOUND = 0x7f
# 3DS calibration targets — auto-calibration is 3DS-only (see calibration_client in device.yaml)
TOP_TARGET = (400, 240)
BOTTOM_TARGET = (320, 240)
TICK_RATE = 0.050 # 50ms = 20Hz
TOP_CALIB_TAG_ID = 0
BOTTOM_CALIB_TAG_ID = 1
CALIB_TAG_SIZE = 200
CALIB_TAG_MARGIN_Y = 20
TOUCH_CALIBRATION_TARGETS = [
    ("top-left", (20, 20)),
    ("top-right", (299, 20)),
    ("bottom-right", (299, 219)),
    ("bottom-left", (20, 219)),
    ("center", (160, 120)),
]
AUTO_TOUCH_TAP_MS = 120
AUTO_TOUCH_GAP_MS = 180

class GamepadButtons:
    ButtonA = 0
    ButtonB = 1
    ButtonX = 2
    ButtonY = 3
    ButtonL1 = 4
    ButtonR1 = 5
    ButtonL2 = 6
    ButtonR2 = 7
    ButtonSelect = 8
    ButtonStart = 9
    ButtonL3 = 10
    ButtonR3 = 11
    ButtonUp = 12
    ButtonDown = 13
    ButtonLeft = 14
    ButtonRight = 15
    ButtonCenter = 16
    ButtonGuide = 17
    ButtonInvalid = -1

class GlobalState:
    lx = 0.0
    ly = 0.0
    rx = 0.0
    ry = 0.0
    buttons = 0 
    interfaceButtons = 0
    ipAddress = ""
    yAxisMultiplier = 1
    abInverse = False
    xyInverse = False
    
    # AI State
    ai_controlled = False

    # Calibration State
    hsv_min = [30, 30, 30]
    hsv_max = [105, 255, 255]
    hsv_tolerance = 20
    last_sampled_hsv = [60, 200, 200]
    sampled_hsv_std = [8.0, 20.0, 20.0]
    sampled_exg_mean = 80.0
    sampled_exg_std = 20.0
    show_mask = False

    # Touch screen size of the active device (0 when the device has no control path)
    touchScreenWidth = 0
    touchScreenHeight = 0

    touchScreenPressed = False
    touchScreenPosition = QPoint(0, 0)
    settings = None
    
    # TAS state
    is_recording = False
    is_playing = False
    tas_frames = [] 
    current_play_idx = 0
    
    udp_socket = None
    heartbeat_running = False

state = GlobalState()

def circular_hue_distance(hue_channel, ref_hue):
    diff = np.abs(hue_channel.astype(np.float32) - float(ref_hue))
    return np.minimum(diff, 180.0 - diff)

def tolerance_scale():
    return max(1.0, state.hsv_tolerance / 10.0)

def sample_screen_color(frame, x, y, radius=10):
    blurred = cv2.GaussianBlur(frame, (5, 5), 0)
    hsv = cv2.cvtColor(blurred, cv2.COLOR_BGR2HSV)
    h, w = hsv.shape[:2]
    x0 = max(0, x - radius)
    x1 = min(w, x + radius + 1)
    y0 = max(0, y - radius)
    y1 = min(h, y + radius + 1)
    patch_hsv = hsv[y0:y1, x0:x1]
    patch_bgr = blurred[y0:y1, x0:x1].astype(np.int16)
    if patch_hsv.size == 0:
        return None

    hsv_mean = patch_hsv.reshape(-1, 3).mean(axis=0)
    hsv_std = patch_hsv.reshape(-1, 3).std(axis=0)
    exg_patch = patch_bgr[:, :, 1] - np.maximum(patch_bgr[:, :, 0], patch_bgr[:, :, 2])

    return {
        "hsv_mean": hsv_mean,
        "hsv_std": hsv_std,
        "exg_mean": float(exg_patch.mean()),
        "exg_std": float(exg_patch.std()),
    }

def update_sampled_color_model(sample):
    if sample is None:
        return

    hsv_mean = sample["hsv_mean"]
    hsv_std = sample["hsv_std"]
    state.last_sampled_hsv = [int(round(v)) for v in hsv_mean]
    state.sampled_hsv_std = [float(max(v, 2.0)) for v in hsv_std]
    state.sampled_exg_mean = float(sample["exg_mean"])
    state.sampled_exg_std = float(max(sample["exg_std"], 4.0))

    scale = tolerance_scale()
    hue_tol = max(6, int(round(state.sampled_hsv_std[0] * scale)))
    sat_tol = max(20, int(round(state.sampled_hsv_std[1] * scale)))
    val_tol = max(20, int(round(state.sampled_hsv_std[2] * scale)))
    state.hsv_min = [
        max(0, state.last_sampled_hsv[0] - hue_tol),
        max(0, state.last_sampled_hsv[1] - sat_tol),
        max(0, state.last_sampled_hsv[2] - val_tol),
    ]
    state.hsv_max = [
        min(179, state.last_sampled_hsv[0] + hue_tol),
        min(255, state.last_sampled_hsv[1] + sat_tol),
        min(255, state.last_sampled_hsv[2] + val_tol),
    ]

def build_calibration_mask(frame):
    blurred = cv2.GaussianBlur(frame, (5, 5), 0)
    hsv = cv2.cvtColor(blurred, cv2.COLOR_BGR2HSV)
    scale = tolerance_scale()

    ref_h, ref_s, ref_v = [float(v) for v in state.last_sampled_hsv]
    std_h, std_s, std_v = state.sampled_hsv_std

    hue_tol = min(40.0, max(6.0, std_h * scale))
    sat_floor = max(20.0, ref_s - max(18.0, std_s * scale * 1.5))
    val_floor = max(20.0, ref_v - max(18.0, std_v * scale * 1.5))
    exg_floor = state.sampled_exg_mean - max(12.0, state.sampled_exg_std * scale * 1.5)

    hue_mask = circular_hue_distance(hsv[:, :, 0], ref_h) <= hue_tol
    sat_mask = hsv[:, :, 1].astype(np.float32) >= sat_floor
    val_mask = hsv[:, :, 2].astype(np.float32) >= val_floor

    bgr = blurred.astype(np.int16)
    exg = bgr[:, :, 1] - np.maximum(bgr[:, :, 0], bgr[:, :, 2])
    exg_mask = exg.astype(np.float32) >= exg_floor

    mask = (hue_mask & sat_mask & val_mask & exg_mask).astype(np.uint8) * 255
    kernel = np.ones((5, 5), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    return mask

def fit_image_to_window(image, window_name):
    try:
        _, _, win_w, win_h = cv2.getWindowImageRect(window_name)
    except cv2.error:
        return image
    if win_w <= 0 or win_h <= 0:
        return image

    img_h, img_w = image.shape[:2]
    scale = min(win_w / img_w, win_h / img_h)
    draw_w = max(1, int(round(img_w * scale)))
    draw_h = max(1, int(round(img_h * scale)))
    resized = cv2.resize(image, (draw_w, draw_h), interpolation=cv2.INTER_AREA)
    canvas = np.zeros((win_h, win_w, image.shape[2]), dtype=image.dtype)
    x0 = (win_w - draw_w) // 2
    y0 = (win_h - draw_h) // 2
    canvas[y0:y0 + draw_h, x0:x0 + draw_w] = resized
    return canvas

def window_to_image_coords(x, y, window_name, image_size):
    img_w, img_h = image_size
    try:
        _, _, win_w, win_h = cv2.getWindowImageRect(window_name)
    except cv2.error:
        return x, y
    if win_w <= 0 or win_h <= 0:
        return x, y

    scale = min(win_w / img_w, win_h / img_h)
    draw_w = max(1, int(round(img_w * scale)))
    draw_h = max(1, int(round(img_h * scale)))
    x0 = (win_w - draw_w) // 2
    y0 = (win_h - draw_h) // 2
    return (x - x0) / scale, (y - y0) / scale

def variant_to_button(val):
    if val is None: return GamepadButtons.ButtonInvalid
    try:
        return int(val)
    except:
        return GamepadButtons.ButtonInvalid

def get_packet_data():
    if not state.settings: return None
    
    def get_btn(name, default):
        return variant_to_button(state.settings.value(name, default))

    hidButtonsAB = [get_btn("ButtonA", GamepadButtons.ButtonA), get_btn("ButtonB", GamepadButtons.ButtonB)]
    hidButtonsMiddle = [
        get_btn("ButtonSelect", GamepadButtons.ButtonSelect),
        get_btn("ButtonStart", GamepadButtons.ButtonStart),
        get_btn("ButtonRight", GamepadButtons.ButtonRight),
        get_btn("ButtonLeft", GamepadButtons.ButtonLeft),
        get_btn("ButtonUp", GamepadButtons.ButtonUp),
        get_btn("ButtonDown", GamepadButtons.ButtonDown),
        get_btn("ButtonR", GamepadButtons.ButtonR1),
        get_btn("ButtonL", GamepadButtons.ButtonL1)
    ]
    hidButtonsXY = [get_btn("ButtonX", GamepadButtons.ButtonX), get_btn("ButtonY", GamepadButtons.ButtonY)]
    irButtons = [get_btn("ButtonZR", GamepadButtons.ButtonR2), get_btn("ButtonZL", GamepadButtons.ButtonL2)]

    hidPad = 0xfff
    if not state.abInverse:
        for i in range(2):
            if state.buttons & (1 << hidButtonsAB[i]): hidPad &= ~(1 << i)
    else:
        for i in range(2):
            if state.buttons & (1 << hidButtonsAB[1-i]): hidPad &= ~(1 << i)

    for i in range(2, 10):
        if state.buttons & (1 << hidButtonsMiddle[i-2]): hidPad &= ~(1 << i)

    if not state.xyInverse:
        for i in range(10, 12):
            if state.buttons & (1 << hidButtonsXY[i-10]): hidPad &= ~(1 << i)
    else:
        for i in range(10, 12):
            if state.buttons & (1 << hidButtonsXY[1-(i-10)]): hidPad &= ~(1 << i)

    irButtonsState = 0
    for i in range(2):
        if state.buttons & (1 << irButtons[i]): irButtonsState |= 1 << (i + 1)

    touchScreenState = 0x2000000 
    circlePadState = 0x7ff7ff
    cppState = 0x80800081

    if state.lx != 0.0 or state.ly != 0.0:
        x = int(state.lx * CPAD_BOUND + 0x800)
        y = int(state.ly * CPAD_BOUND + 0x800)
        x = max(0, min(0xfff, x))
        y = max(0, min(0xfff, y))
        circlePadState = (y << 12) | x

    if state.rx != 0.0 or state.ry != 0.0 or irButtonsState != 0:
        x_val = math.sqrt(0.5) * (state.rx + state.ry) * CPP_BOUND + 0x80
        y_val = math.sqrt(0.5) * (state.ry - state.rx) * CPP_BOUND + 0x80
        x, y = int(x_val), int(y_val)
        x = max(0, min(0xff, x))
        y = max(0, min(0xff, y))
        cppState = (y << 24) | (x << 16) | (irButtonsState << 8) | 0x81

    if state.touchScreenPressed:
        max_tx = state.touchScreenWidth - 1
        max_ty = state.touchScreenHeight - 1
        tx = max(0, min(state.touchScreenPosition.x(), max_tx))
        ty = max(0, min(state.touchScreenPosition.y(), max_ty))
        x = int(round(0xfff * tx / max_tx))
        y = int(round(0xfff * ty / max_ty))
        touchScreenState = (1 << 24) | (y << 12) | x

    return struct.pack('<IIIII', hidPad, touchScreenState, circlePadState, cppState, state.interfaceButtons)

def get_release_packet():
    return struct.pack('<IIIII', 0xfff, 0x2000000, 0x7ff7ff, 0x80800081, 0)

def send_packet(ba):
    if state.ipAddress:
        if state.udp_socket is None:
            state.udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            state.udp_socket.sendto(ba, (state.ipAddress, 4950))
        except:
            pass

class GamepadMonitor(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        os.environ["SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS"] = "1"
        pygame.init()
        pygame.joystick.init()
        self.joysticks = []
        self.rescan_joysticks()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll_gamepad)
        self.timer.start(16)

    def rescan_joysticks(self):
        pygame.joystick.quit()
        pygame.joystick.init()
        self.joysticks = [pygame.joystick.Joystick(i) for i in range(pygame.joystick.get_count())]
        for joy in self.joysticks: 
            joy.init()
            print(f"Gamepad found: {joy.get_name()}")

    def poll_gamepad(self):
        pygame.event.pump()
        if not self.joysticks:
            if pygame.joystick.get_count() > 0: 
                self.rescan_joysticks()
            else: 
                # Reset state if no joystick
                state.lx = 0.0
                state.ly = 0.0
                state.rx = 0.0
                state.ry = 0.0
                state.buttons = 0
                return
        
        # If AI is controlling, don't read gamepad inputs into global state
        if state.ai_controlled:
            return

        joy = self.joysticks[0]
        try:
            state.lx = joy.get_axis(0)
            state.ly = joy.get_axis(1)
            if abs(state.lx) < 0.1: state.lx = 0
            if abs(state.ly) < 0.1: state.ly = 0
            state.ly = state.yAxisMultiplier * -state.ly
            
            if joy.get_numaxes() >= 4:
                rx_axis = 3 if joy.get_numaxes() > 3 else 2
                ry_axis = 4 if joy.get_numaxes() > 4 else 3
                state.rx = joy.get_axis(rx_axis)
                state.ry = joy.get_axis(ry_axis)
                if abs(state.rx) < 0.1: state.rx = 0
                if abs(state.ry) < 0.1: state.ry = 0
                state.ry = state.yAxisMultiplier * -state.ry

            mapping = {0: GamepadButtons.ButtonA, 1: GamepadButtons.ButtonB, 2: GamepadButtons.ButtonX, 
                       3: GamepadButtons.ButtonY, 4: GamepadButtons.ButtonL1, 5: GamepadButtons.ButtonR1, 
                       6: GamepadButtons.ButtonSelect, 7: GamepadButtons.ButtonStart, 8: GamepadButtons.ButtonGuide, 
                       9: GamepadButtons.ButtonL3, 10: GamepadButtons.ButtonR3}
            current_buttons = 0
            for py_btn, qt_btn in mapping.items():
                if py_btn < joy.get_numbuttons() and joy.get_button(py_btn):
                    current_buttons |= (1 << qt_btn)
            state.buttons = current_buttons
            
            if joy.get_numaxes() > 2 and joy.get_axis(2) > 0.5: state.buttons |= (1 << GamepadButtons.ButtonL2)
            if joy.get_numaxes() > 5 and joy.get_axis(5) > 0.5: state.buttons |= (1 << GamepadButtons.ButtonR2)
            
            if joy.get_numhats() > 0:
                hat = joy.get_hat(0)
                if hat[0] == -1: state.buttons |= (1 << GamepadButtons.ButtonLeft)
                if hat[0] == 1: state.buttons |= (1 << GamepadButtons.ButtonRight)
                if hat[1] == 1: state.buttons |= (1 << GamepadButtons.ButtonUp)
                if hat[1] == -1: state.buttons |= (1 << GamepadButtons.ButtonDown)

            def is_pressed(btn):
                if btn == GamepadButtons.ButtonInvalid: return False
                return (state.buttons & (1 << btn)) != 0

            home_btn = variant_to_button(state.settings.value("ButtonHome", GamepadButtons.ButtonInvalid))
            if is_pressed(home_btn): state.interfaceButtons |= 1
            else: state.interfaceButtons &= ~1
            power_btn = variant_to_button(state.settings.value("ButtonPower", GamepadButtons.ButtonInvalid))
            if is_pressed(power_btn): state.interfaceButtons |= 2
            else: state.interfaceButtons &= ~2
            power_long_btn = variant_to_button(state.settings.value("ButtonPowerLong", GamepadButtons.ButtonInvalid))
            if is_pressed(power_long_btn): state.interfaceButtons |= 4
            else: state.interfaceButtons &= ~4

            t1_btn = variant_to_button(state.settings.value("ButtonT1", GamepadButtons.ButtonInvalid))
            t2_btn = variant_to_button(state.settings.value("ButtonT2", GamepadButtons.ButtonInvalid))
            if is_pressed(t1_btn):
                state.touchScreenPressed = True
                state.touchScreenPosition = QPoint(int(state.settings.value("touchButton1X", 0)), int(state.settings.value("touchButton1Y", 0)))
            elif is_pressed(t2_btn):
                state.touchScreenPressed = True
                state.touchScreenPosition = QPoint(int(state.settings.value("touchButton2X", 0)), int(state.settings.value("touchButton2Y", 0)))
        except pygame.error: pass

import base64
from http.server import HTTPServer, BaseHTTPRequestHandler

class ThreadedCamera:
    def __init__(self, source):
        self.cap = cv2.VideoCapture(source)
        # Force low latency flags
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self.running = True
        self.latest_frame = None
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self.update, daemon=True)
        self.thread.start()

    def update(self):
        while self.running:
            ret, frame = self.cap.read()
            if ret:
                with self.lock:
                    self.latest_frame = frame
            else:
                time.sleep(0.01)

    def read(self):
        with self.lock:
            return self.latest_frame is not None, self.latest_frame

    def stop(self):
        self.running = False
        self.thread.join(timeout=1.0)
        self.cap.release()

class RemoteCamHandler(BaseHTTPRequestHandler):
    app_window = None

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        html = """
        <html>
        <head>
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <style>
                body { margin:0; background:#000; color:#fff; font-family:sans-serif; text-align:center; display:flex; flex-direction:column; height:100vh; }
                video { width:100%; max-height:70vh; background:#222; }
                #log { padding:20px; font-size:14px; color:#aaa; overflow-y:auto; flex-grow:1; }
                button { padding:15px; margin:10px; font-size:18px; background:#28a745; color:white; border:none; border-radius:5px; }
            </style>
        </head>
        <body>
            <h3 style="margin:10px;">UC2 Remote Cam</h3>
            <video id="v" autoplay playsinline muted></video>
            <button id="b">1. Start Camera</button>
            <div id="log">Status: Ready. If camera doesn't start, ensure you are using a browser that supports camera access over HTTP (like Chrome on some devices) or use a dedicated IP Cam app.</div>
            
            <script>
                const v = document.getElementById('v');
                const b = document.getElementById('b');
                const l = document.getElementById('log');

                function log(msg) { l.innerText = msg; console.log(msg); }

                b.onclick = async () => {
                    log("Requesting camera...");
                    try {
                        const stream = await navigator.mediaDevices.getUserMedia({ 
                            video: { facingMode: "environment", width: {ideal: 640}, height: {ideal: 480} } 
                        });
                        v.srcObject = stream;
                        b.style.display = 'none';
                        log("Streaming... Look at your PC!");
                        
                        const canvas = document.createElement('canvas');
                        const ctx = canvas.getContext('2d');
                        
                        setInterval(() => {
                            if (v.videoWidth === 0) return;
                            canvas.width = 320; // Lower resolution for ARM stability
                            canvas.height = (v.videoHeight / v.videoWidth) * 320;
                            ctx.drawImage(v, 0, 0, canvas.width, canvas.height);
                            
                            canvas.toBlob((blob) => {
                                fetch('/u', { method: 'POST', body: blob });
                            }, 'image/jpeg', 0.6);
                        }, 66); // ~15 FPS
                    } catch (e) {
                        log("ERROR: " + e.message + "\\n\\nOn iOS, Camera often requires HTTPS. Try using an IP Camera app instead if this fails.");
                    }
                };
            </script>
        </body>
        </html>
        """
        self.wfile.write(html.encode())

    def do_POST(self):
        if self.path == '/u':
            content_length = int(self.headers['Content-Length'])
            img_data = self.rfile.read(content_length)
            nparr = np.frombuffer(img_data, np.uint8)
            frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if frame is not None and RemoteCamHandler.app_window:
                with RemoteCamHandler.app_window.frame_lock:
                    RemoteCamHandler.app_window.latest_frame = frame
        
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

    def log_message(self, format, *args): return # Silence logs

class RemoteCamServer(threading.Thread):
    def __init__(self, window):
        super().__init__(daemon=True)
        RemoteCamHandler.app_window = window
        self.server = HTTPServer(('0.0.0.0', 5000), RemoteCamHandler)
    def run(self):
        self.server.serve_forever()

class CameraSignals(QObject):
    status_update = pyqtSignal(str)
    error_occurred = pyqtSignal(str)

class RemapConfig(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Button Config")
        self.layout = QVBoxLayout(self)
        
        scroll_area = QWidget()
        self.formLayout = QFormLayout(scroll_area)
        
        self.mapping_buttons = {}
        self.record_target = None
        
        self.button_names = {
            GamepadButtons.ButtonA: "A (bottom)", GamepadButtons.ButtonB: "B (right)",
            GamepadButtons.ButtonX: "X (left)", GamepadButtons.ButtonY: "Y (top)",
            GamepadButtons.ButtonRight: "Right", GamepadButtons.ButtonLeft: "Left",
            GamepadButtons.ButtonUp: "Up", GamepadButtons.ButtonDown: "Down",
            GamepadButtons.ButtonL1: "LB / L", GamepadButtons.ButtonR1: "RB / R",
            GamepadButtons.ButtonSelect: "Select", GamepadButtons.ButtonStart: "Start",
            GamepadButtons.ButtonL2: "LT / ZL", GamepadButtons.ButtonR2: "RT / ZR",
            GamepadButtons.ButtonL3: "L3", GamepadButtons.ButtonR3: "R3",
            GamepadButtons.ButtonGuide: "Guide", GamepadButtons.ButtonInvalid: "None"
        }

        keys = [
            "ButtonA", "ButtonB", "ButtonX", "ButtonY",
            "ButtonUp", "ButtonDown", "ButtonLeft", "ButtonRight",
            "ButtonL", "ButtonR", "ButtonSelect", "ButtonStart",
            "ButtonZL", "ButtonZR", "ButtonHome", "ButtonPower",
            "ButtonPowerLong", "ButtonT1", "ButtonT2"
        ]

        for key in keys:
            val = variant_to_button(state.settings.value(key, GamepadButtons.ButtonInvalid))
            btn = QPushButton(self.button_names.get(val, "None"))
            btn.clicked.connect(lambda checked, k=key: self.start_recording(k))
            self.mapping_buttons[key] = btn
            self.formLayout.addRow(key, btn)

        self.t1x = QLineEdit(str(state.settings.value("touchButton1X", 0)))
        self.t1y = QLineEdit(str(state.settings.value("touchButton1Y", 0)))
        self.t2x = QLineEdit(str(state.settings.value("touchButton2X", 0)))
        self.t2y = QLineEdit(str(state.settings.value("touchButton2Y", 0)))
        self.formLayout.addRow("T1 X", self.t1x); self.formLayout.addRow("T1 Y", self.t1y)
        self.formLayout.addRow("T2 X", self.t2x); self.formLayout.addRow("T2 Y", self.t2y)

        self.saveButton = QPushButton("SAVE")
        self.saveButton.clicked.connect(self.save_settings)
        self.layout.addWidget(scroll_area)
        self.layout.addWidget(self.saveButton)
        
        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self.poll_recording)

    def start_recording(self, key):
        self.record_target = key
        for k, btn in self.mapping_buttons.items():
            btn.setEnabled(False)
            if k == key:
                btn.setText("Press any button...")
        self.poll_timer.start(50)

    def poll_recording(self):
        pygame.event.pump()
        if pygame.joystick.get_count() == 0:
            return
        
        joy = pygame.joystick.Joystick(0)
        pressed_btn = -1
        
        # Check standard buttons
        for i in range(joy.get_numbuttons()):
            if joy.get_button(i):
                # Map standard buttons (this logic depends on pygame's internal mapping)
                # We'll use a simple approach: if it's pressed, we try to match it to our GamepadButtons enum
                # Since we don't know the user's specific controller layout, we just record the index
                # and treat it as the GamepadButtons value for simplicity in this hack.
                # Realistically, we should just store the raw pygame button index.
                pressed_btn = i 
                break
        
        # Check hats (D-pad)
        if pressed_btn == -1 and joy.get_numhats() > 0:
            hat = joy.get_hat(0)
            if hat[0] == -1: pressed_btn = GamepadButtons.ButtonLeft
            elif hat[0] == 1: pressed_btn = GamepadButtons.ButtonRight
            elif hat[1] == 1: pressed_btn = GamepadButtons.ButtonUp
            elif hat[1] == -1: pressed_btn = GamepadButtons.ButtonDown
            
        # Check triggers (axes)
        if pressed_btn == -1:
            if joy.get_numaxes() > 2 and joy.get_axis(2) > 0.5: pressed_btn = GamepadButtons.ButtonL2
            if joy.get_numaxes() > 5 and joy.get_axis(5) > 0.5: pressed_btn = GamepadButtons.ButtonR2

        if pressed_btn != -1:
            self.poll_timer.stop()
            state.settings.setValue(self.record_target, pressed_btn)
            self.mapping_buttons[self.record_target].setText(self.button_names.get(pressed_btn, f"Button {pressed_btn}"))
            self.record_target = None
            for btn in self.mapping_buttons.values():
                btn.setEnabled(True)

    def save_settings(self):
        state.settings.setValue("touchButton1X", self.t1x.text())
        state.settings.setValue("touchButton1Y", self.t1y.text())
        state.settings.setValue("touchButton2X", self.t2x.text())
        state.settings.setValue("touchButton2Y", self.t2y.text())
        self.hide()

class AspectImageLabel(QLabel):
    def __init__(self, touch_callback=None, parent=None):
        super().__init__(parent)
        self.touch_callback = touch_callback
        self.qimage = None
        self.image_size = None
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(160, 120)
        self.setStyleSheet("background: #000;")

    def set_frame(self, frame):
        h, w = frame.shape[:2]
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        qimage = QImage(rgb.data, w, h, rgb.strides[0], QImage.Format.Format_RGB888)
        self.qimage = qimage.copy()
        self.image_size = (w, h)
        self.update_pixmap()

    def resizeEvent(self, event):
        self.update_pixmap()
        super().resizeEvent(event)

    def update_pixmap(self):
        if self.qimage is None:
            return
        pixmap = QPixmap.fromImage(self.qimage).scaled(
            self.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.setPixmap(pixmap)

    def image_coords_from_event(self, event):
        if not self.image_size:
            return None
        img_w, img_h = self.image_size
        label_w, label_h = self.width(), self.height()
        scale = min(label_w / img_w, label_h / img_h)
        draw_w = img_w * scale
        draw_h = img_h * scale
        x0 = (label_w - draw_w) / 2.0
        y0 = (label_h - draw_h) / 2.0
        pos = event.position()
        img_x = (pos.x() - x0) / scale
        img_y = (pos.y() - y0) / scale
        return img_x, img_y

    def mousePressEvent(self, event):
        if self.touch_callback and event.button() == Qt.MouseButton.LeftButton:
            coords = self.image_coords_from_event(event)
            if coords:
                self.touch_callback("press", coords[0], coords[1])

    def mouseMoveEvent(self, event):
        if self.touch_callback and event.buttons() & Qt.MouseButton.LeftButton:
            coords = self.image_coords_from_event(event)
            if coords:
                self.touch_callback("move", coords[0], coords[1])

    def mouseReleaseEvent(self, event):
        if self.touch_callback and event.button() == Qt.MouseButton.LeftButton:
            self.touch_callback("release", 0, 0)

class ScreenViewWindow(QWidget):
    def __init__(self, title, size, touch_callback=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.label = AspectImageLabel(touch_callback, self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.label)
        self.resize(*size)

    def set_frame(self, frame):
        self.label.set_frame(frame)

    def closeEvent(self, event):
        self.hide()
        event.ignore()

class CalibrationState:
    IDLE = 0
    WAIT_TOP = 1
    WAIT_TRANSITION_1 = 2
    WAIT_BOTTOM = 3
    DONE = 4

class CalibrationManager:
    def __init__(self, signals, control_enabled=None):
        # Callable, so switching to a no-control device mid-calibration is seen
        # without any re-sync (auto-calibration is 3DS-only, control: true).
        self.control_enabled = control_enabled or (lambda: True)
        self.state = CalibrationState.IDLE
        self.signals = signals
        self.timer = 0
        self.found_top = None
        self.found_bottom = None
        
        # Stability tracking
        self.stable_rect = None
        self.stable_count = 0
        self.REQUIRED_STABILITY = 5 # Number of frames to stay stable
        self.current_candidate = None
        self.marker_dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11)
        self.detector_params = cv2.aruco.DetectorParameters()
        if hasattr(cv2.aruco, "CORNER_REFINE_APRILTAG"):
            self.detector_params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_APRILTAG
        self.marker_detector = cv2.aruco.ArucoDetector(self.marker_dictionary, self.detector_params)
    
    def start(self):
        if not self.control_enabled():
            return
        self.state = CalibrationState.WAIT_TOP
        self.found_top = None
        self.found_bottom = None
        self.stable_rect = None
        self.stable_count = 0
        self.current_candidate = None
        self.signals.status_update.emit("Calib: Show AprilTag 0 on Top Screen")

    def process(self, frame):
        if not self.control_enabled():
            # No-control device (e.g. Switch): calibration A/Select presses must
            # never go out, whatever state a previous device's run left us in.
            return None
        if self.state == CalibrationState.IDLE or self.state == CalibrationState.DONE:
            return None

        current_time = time.time()
        if self.state == CalibrationState.WAIT_TOP:
            rect = self.find_marker_screen(frame, TOP_CALIB_TAG_ID, TOP_TARGET)
            self.current_candidate = rect
            if rect is not None:
                if self.check_stability(rect):
                    self.found_top = rect
                    self.signals.status_update.emit("Top Found! Sending Input...")
                    self.send_a_press()
                    self.timer = current_time
                    self.state = CalibrationState.WAIT_TRANSITION_1
                    self.reset_stability()
            else:
                self.reset_stability()
        
        elif self.state == CalibrationState.WAIT_TRANSITION_1:
            if current_time - self.timer > 2.0: # Wait 2s for 3DS to switch
                self.signals.status_update.emit("Calib: Show AprilTag 1 on Bottom Screen")
                self.state = CalibrationState.WAIT_BOTTOM
        
        elif self.state == CalibrationState.WAIT_BOTTOM:
            rect = self.find_marker_screen(frame, BOTTOM_CALIB_TAG_ID, BOTTOM_TARGET)
            self.current_candidate = rect
            if rect is not None:
                # Check overlap with top to avoid re-detecting same screen
                if not self.is_overlapping(rect, self.found_top):
                    if self.check_stability(rect):
                        self.found_bottom = rect
                        self.signals.status_update.emit("Bottom Found! Finishing...")
                        self.send_select_press()
                        self.state = CalibrationState.DONE
                        self.reset_stability()
                        return [self.found_top, self.found_bottom]
                else:
                    self.reset_stability()
            else:
                self.reset_stability()
        
        return None

    def check_stability(self, new_rect):
        if self.stable_rect is None:
            self.stable_rect = new_rect
            self.stable_count = 1
            return False
        
        # Compare centers
        c_old = np.mean(self.stable_rect, axis=0)
        c_new = np.mean(new_rect, axis=0)
        if np.linalg.norm(c_old - c_new) < 30: # Within 30 pixels
            self.stable_count += 1
            if self.stable_count >= self.REQUIRED_STABILITY:
                return True
        else:
            self.stable_rect = new_rect
            self.stable_count = 1
        return False

    def reset_stability(self):
        self.stable_rect = None
        self.stable_count = 0

    def get_marker_rect(self, screen_size):
        screen_w, screen_h = screen_size
        marker_size = min(CALIB_TAG_SIZE, screen_w, screen_h - (CALIB_TAG_MARGIN_Y * 2))
        marker_x = (screen_w - marker_size) / 2.0
        marker_y = (screen_h - marker_size) / 2.0
        return np.array([
            [marker_x, marker_y],
            [marker_x + marker_size, marker_y],
            [marker_x + marker_size, marker_y + marker_size],
            [marker_x, marker_y + marker_size],
        ], dtype=np.float32)

    def find_marker_screen(self, frame, marker_id, screen_size):
        corners, ids, _ = self.marker_detector.detectMarkers(frame)
        if ids is None:
            return None

        ids = ids.flatten()
        for detected_id, detected_corners in zip(ids, corners):
            if int(detected_id) != marker_id:
                continue
            marker_quad = detected_corners.reshape(4, 2).astype(np.float32)
            marker_rect = self.get_marker_rect(screen_size)
            screen_rect = np.array([
                [0, 0],
                [screen_size[0] - 1, 0],
                [screen_size[0] - 1, screen_size[1] - 1],
                [0, screen_size[1] - 1],
            ], dtype=np.float32)
            transform = cv2.getPerspectiveTransform(marker_rect, marker_quad)
            return cv2.perspectiveTransform(screen_rect.reshape(1, 4, 2), transform)[0]

        return None

    def is_overlapping(self, rect1, rect2):
        # Simple center point distance check
        c1 = np.mean(rect1, axis=0)
        c2 = np.mean(rect2, axis=0)
        dist = np.linalg.norm(c1 - c2)
        return dist < 50 # If centers are close, assume same screen

    def send_a_press(self):
        # Send A press
        # HID format: <IIIII
        # hidPad (0xFFF default). A is bit 0. Clear bit 0 to press.
        packet_press = struct.pack('<IIIII', 0xffe, 0x2000000, 0x7ff7ff, 0x80800081, 0)
        send_packet(packet_press)
        
        # Schedule release after 100ms
        time.sleep(0.1)
        send_packet(get_release_packet())

    def send_select_press(self):
        # Send Select press
        # Select is Bit 2 (1 << 2 = 4)
        # 0xFFF & ~4 = 0xFFB
        packet_press = struct.pack('<IIIII', 0xffb, 0x2000000, 0x7ff7ff, 0x80800081, 0)
        send_packet(packet_press)
        
        time.sleep(0.1)
        send_packet(get_release_packet())

class AppWindow(QMainWindow):
    def __init__(self):
        # print("DEBUG: AppWindow init start") # Debugs removed
        super().__init__()
        self.signals = CameraSignals()
        state.settings = QSettings("cylin_TW", "UC2")
        self.migrate_legacy_settings()
        self.setup_variables()
        self.setup_ui()
        self.gamepad_monitor = GamepadMonitor(self)
        self.calibration_manager = CalibrationManager(self.signals, lambda: self.control_enabled)
        self.ai_manager = None
        self.setup_connections()
        
        state.heartbeat_running = True
        self.heartbeat_thread = threading.Thread(target=self.heartbeat_loop, daemon=True)
        self.device_update_signals = DeviceUpdateSignals()
        self.device_update_signals.updated.connect(self.status_label.setText)
        start_update_check(self.device_update_signals)

        self.heartbeat_thread.start()

    def load_device(self, device_id):
        device = device_by_id(device_data, device_id) or \
            device_by_id(device_data, default_device_id(device_data))
        self.device = device
        self.device_id = device["id"]
        self.control_enabled = bool(device.get("control"))
        screens = device["screens"]
        self.combined_size = screen_layout(device)
        self.top_target = (screens[0]["width"], screens[0]["height"])
        self.bottom_target = (screens[1]["width"], screens[1]["height"]) if len(screens) > 1 else None
        if device.get("control"):
            touch = screens[-1]          # 3DS: touch is the bottom screen, at (40, 240)
            state.touchScreenWidth = touch["width"]
            state.touchScreenHeight = touch["height"]
            self.touch_offset = (touch["x"], touch["y"])
        else:
            state.touchScreenWidth = 0
            state.touchScreenHeight = 0
            state.touchScreenPressed = False
            # A run started on a 3DS cannot keep its playback cursor on a
            # no-control device: restart from 0 when we go back.
            state.is_playing = False
            state.current_play_idx = 0
            self.touch_offset = (0, 0)
        state.settings.setValue("deviceId", self.device_id)

    def migrate_legacy_settings(self):
        # shortcut: one-time import of pre-rename "3DSC2" settings; delete after the next release.
        legacy = QSettings("cylin_TW", "3DSC2")
        if legacy.contains("ipAddress") and not state.settings.contains("ipAddress"):
            for key in ("ipAddress", "camUrl", "useIpCam", "invertY", "invertAB", "invertXY",
                        "ButtonA", "ButtonB", "ButtonX", "ButtonY", "ButtonUp", "ButtonDown",
                        "ButtonLeft", "ButtonRight", "ButtonL", "ButtonR", "ButtonSelect",
                        "ButtonStart", "ButtonZL", "ButtonZR", "ButtonHome", "ButtonPower",
                        "ButtonPowerLong", "ButtonT1", "ButtonT2",
                        "touchButton1X", "touchButton1Y", "touchButton2X", "touchButton2Y"):
                if legacy.contains(key):
                    state.settings.setValue(key, legacy.value(key))

    def setup_variables(self):
        self.camera_index = 0
        self.frame_width = 2560
        self.frame_height = 1440
        self.load_device(state.settings.value("deviceId", default_device_id(device_data)))
        self.latest_frame = None
        self.last_top_frame = None
        self.last_bottom_frame = None
        self.top_view_window = None
        self.bottom_view_window = None
        self.combined_view_window = None
        self.frame_lock = threading.Lock()
        self.running = False
        self.roi_points = []
        self.screens = []
        self.all_points = []
        self.cap = None
        self.camera_thread = None
        self.windows_created = False
        self.view_mode = None
        self.sampling_color = False
        self.roi_locked = False
        self.combined_view = False
        
        state.ipAddress = state.settings.value("ipAddress", "")
        state.yAxisMultiplier = -1 if state.settings.value("invertY", False, type=bool) else 1
        state.abInverse = state.settings.value("invertAB", False, type=bool)    
        state.xyInverse = state.settings.value("invertXY", False, type=bool)

    def setup_ui(self):
        self.setWindowTitle("UC2")
        self.setMinimumSize(600, 750)
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        cam_group = QGroupBox("Camera & Network")
        cam_layout = QFormLayout(cam_group)
        self.device_combo = QComboBox()
        for device in device_data["devices"]:
            self.device_combo.addItem(device["friendly_name"], device["id"])
        self.device_combo.setCurrentText(self.device["friendly_name"])
        cam_layout.addRow("Device:", self.device_combo)
        self.camera_combo = QComboBox()
        self.camera_combo.addItems([f"Camera {i}" for i in range(8)])
        cam_layout.addRow("Camera:", self.camera_combo)
        self.url_edit = QLineEdit(state.settings.value("camUrl", ""))
        self.url_edit.setPlaceholderText("http://192.168.x.x:4747/video")
        cam_layout.addRow("IP Cam URL:", self.url_edit)
        
        self.use_ip_cam = QCheckBox("Use IP Camera")
        self.use_ip_cam.setChecked(state.settings.value("useIpCam", False, type=bool))
        cam_layout.addRow("", self.use_ip_cam)
        
        self.ip_edit = QLineEdit(state.ipAddress)
        self.ip_row_label = QLabel()
        cam_layout.addRow(self.ip_row_label, self.ip_edit)
        layout.addWidget(cam_group)

        btn_layout = QHBoxLayout()
        self.start_btn = QPushButton("Start Camera")
        self.stop_btn = QPushButton("Stop Camera")
        self.stop_btn.setEnabled(False)
        self.remote_btn = QPushButton("Start Remote Server")
        self.auto_calib_btn = QPushButton("Auto Calibrate")
        self.reset_btn = QPushButton("Reset ROIs")
        self.config_btn = QPushButton("Button Config")
        btn_layout.addWidget(self.start_btn); btn_layout.addWidget(self.stop_btn)
        btn_layout.addWidget(self.remote_btn)
        btn_layout.addWidget(self.auto_calib_btn)
        btn_layout.addWidget(self.reset_btn); btn_layout.addWidget(self.config_btn)
        layout.addLayout(btn_layout)

        tas_group = QGroupBox("Event Replay")
        tas_layout = QHBoxLayout(tas_group)
        self.record_btn = QPushButton("Record")
        self.play_btn = QPushButton("Play")
        self.save_tas_btn = QPushButton("Save Event")
        self.load_tas_btn = QPushButton("Load Event")
        tas_layout.addWidget(self.record_btn); tas_layout.addWidget(self.play_btn)
        tas_layout.addWidget(self.save_tas_btn); tas_layout.addWidget(self.load_tas_btn)
        layout.addWidget(tas_group)

        ai_group = QGroupBox("Behavior Cloning (AI)")
        ai_layout = QHBoxLayout(ai_group)
        self.ai_record_btn = QPushButton("Record Expert")
        self.ai_train_btn = QPushButton("Train Model")
        self.ai_reset_btn = QPushButton("Clear Data")
        self.ai_enable_btn = QPushButton("Enable AI Agent")
        self.ai_enable_btn.setCheckable(True)
        ai_layout.addWidget(self.ai_record_btn)
        ai_layout.addWidget(self.ai_train_btn)
        ai_layout.addWidget(self.ai_reset_btn)
        ai_layout.addWidget(self.ai_enable_btn)
        layout.addWidget(ai_group)

        # Calibration Adjustments
        cal_group = QGroupBox("Calibration Settings")
        cal_layout = QVBoxLayout(cal_group)
        
        self.pick_color_btn = QPushButton("Sample Color Fallback")
        self.lock_roi_checkbox = QCheckBox("Lock ROIs")
        
        tol_layout = QHBoxLayout()
        self.tol_slider = QSlider(Qt.Orientation.Horizontal)
        self.tol_slider.setRange(5, 50)
        self.tol_slider.setValue(state.hsv_tolerance)
        tol_layout.addWidget(QLabel("Tolerance:")); tol_layout.addWidget(self.tol_slider)
        
        self.mask_checkbox = QCheckBox("Show Calibration Mask")
        self.mask_checkbox.setChecked(state.show_mask)
        self.combined_view_cb = QCheckBox("Combined View")
        self.combined_view_cb.setChecked(self.combined_view)
        
        cal_layout.addWidget(self.pick_color_btn)
        cal_layout.addWidget(self.lock_roi_checkbox)
        cal_layout.addLayout(tol_layout)
        cal_layout.addWidget(self.mask_checkbox)
        cal_layout.addWidget(self.combined_view_cb)
        layout.addWidget(cal_group)

        inv_layout = QHBoxLayout()
        self.inv_y = QCheckBox("Invert Y"); self.inv_y.setChecked(state.yAxisMultiplier == -1)
        self.inv_ab = QCheckBox("Invert A-B"); self.inv_ab.setChecked(state.abInverse)
        self.inv_xy = QCheckBox("Invert X-Y"); self.inv_xy.setChecked(state.xyInverse)
        inv_layout.addWidget(self.inv_y); inv_layout.addWidget(self.inv_ab); inv_layout.addWidget(self.inv_xy)
        layout.addLayout(inv_layout)

        self.status_label = QLabel("Ready")
        layout.addWidget(self.status_label)
        self.instr = QTextEdit(self.device_instructions())
        self.instr.setReadOnly(True); self.instr.setMaximumHeight(80)
        layout.addWidget(self.instr)

        self.fps_label = QLabel("FPS: --")
        layout.addWidget(self.fps_label)

        self.remap_dlg = RemapConfig(self)
        self.apply_device_ui()

    def device_instructions(self):
        step = 1
        parts = []
        if self.device.get("control"):
            parts.append(f"{step}. Set the device IP.")
            step += 1
        parts.append(f"{step}. Start camera. {step + 1}. Click 4 corners of every screen "
                     f"in the ROI Selector to lock the ROIs.")
        step += 2
        parts.append(f"{step}. Auto Calibrate needs the calibration client running on your 3DS."
                     if self.device.get("calibration_client") else
                     f"{step}. Auto Calibrate is unavailable: no calibration client exists for this device.")
        return " ".join(parts)

    def setup_connections(self):
        self.start_btn.clicked.connect(self.start_camera)
        self.stop_btn.clicked.connect(self.stop_camera)
        self.remote_btn.clicked.connect(self.start_remote_server)
        self.auto_calib_btn.clicked.connect(self.start_interactive_calibration)
        self.reset_btn.clicked.connect(self.reset_rois)
        self.config_btn.clicked.connect(self.remap_dlg.show)
        self.ip_edit.textChanged.connect(self.update_ip)
        self.camera_combo.currentIndexChanged.connect(lambda: self.use_ip_cam.setChecked(False))
        self.device_combo.currentIndexChanged.connect(self.switch_device)
        self.url_edit.textChanged.connect(lambda text: self.use_ip_cam.setChecked(True) if text.strip() else None)
        self.inv_y.stateChanged.connect(self.update_settings)
        self.inv_ab.stateChanged.connect(self.update_settings)
        self.inv_xy.stateChanged.connect(self.update_settings)
        
        self.record_btn.clicked.connect(self.toggle_record)
        self.play_btn.clicked.connect(self.toggle_play)
        self.save_tas_btn.clicked.connect(self.save_tas)
        self.load_tas_btn.clicked.connect(self.load_tas)

        self.ai_record_btn.clicked.connect(self.toggle_ai_record)
        self.ai_train_btn.clicked.connect(self.train_ai_model)
        self.ai_reset_btn.clicked.connect(self.reset_ai_data)
        self.ai_enable_btn.toggled.connect(self.toggle_ai_active)

        # Calibration Adjustments
        self.pick_color_btn.clicked.connect(self.start_sampling_color)
        self.lock_roi_checkbox.stateChanged.connect(self.toggle_roi_lock)
        self.tol_slider.valueChanged.connect(self.update_tolerance)
        self.mask_checkbox.toggled.connect(self.toggle_mask_view)
        self.combined_view_cb.toggled.connect(self.toggle_combined)

        self.signals.status_update.connect(self.status_label.setText)
        self.signals.error_occurred.connect(lambda m: QMessageBox.critical(self, "Error", m))

    def get_ai_manager(self):
        if self.ai_manager is None:
            if AIManager is None:
                self.status_label.setText("AI unavailable: PyTorch not installed")
                QApplication.processEvents()
                return None
            self.status_label.setText("Loading AI module...")
            QApplication.processEvents()
            self.ai_manager = AIManager()
            self.ai_manager.status_update.connect(self.status_label.setText)
        return self.ai_manager

    def toggle_ai_record(self):
        ai_manager = self.get_ai_manager()
        if ai_manager is None:
            return
        recording = ai_manager.toggle_recording()
        self.ai_record_btn.setText("Stop Recording" if recording else "Record Expert")

    def train_ai_model(self):
        ai_manager = self.get_ai_manager()
        if ai_manager is None:
            return
        ai_manager.train()

    def reset_ai_data(self):
        ai_manager = self.get_ai_manager()
        if ai_manager is None:
            return
        ai_manager.reset_data()
        
    def toggle_ai_active(self, checked):
        if AIManager is None:
            self.ai_enable_btn.setChecked(False)
            state.ai_controlled = False
            self.status_label.setText("AI unavailable: PyTorch not installed")
            return
        state.ai_controlled = checked
        if checked:
            ai_manager = self.get_ai_manager()
            if ai_manager is None:
                self.ai_enable_btn.setChecked(False)
                state.ai_controlled = False
                return
            success = ai_manager.toggle_active()
            if not success:
                self.ai_enable_btn.setChecked(False)
                state.ai_controlled = False
            else:
                self.status_label.setText("AI Agent Active")
        else:
            if self.ai_manager is not None:
                self.ai_manager.is_active = False
            self.status_label.setText("AI Agent Disabled")

    def toggle_roi_lock(self, s):
        self.roi_locked = (s != 0)
        self.status_label.setText("ROIs Locked" if self.roi_locked else "ROIs Unlocked")

    def update_ip(self, t): 
        state.ipAddress = t
        state.settings.setValue("ipAddress", t)
    
    def update_settings(self):
        state.yAxisMultiplier = -1 if self.inv_y.isChecked() else 1
        state.abInverse = self.inv_ab.isChecked()
        state.xyInverse = self.inv_xy.isChecked()
        state.settings.setValue("invertY", self.inv_y.isChecked())
        state.settings.setValue("invertAB", self.inv_ab.isChecked())
        state.settings.setValue("invertXY", self.inv_xy.isChecked())

    def switch_device(self, _index):
        self.load_device(self.device_combo.currentData())
        self.reset_rois()                      # ROIs are device-geometry specific
        self.hide_view_windows()               # drop the previous device's windows
        self.top_view_window = None
        self.bottom_view_window = None
        self.combined_view_window = None       # recreated at the new device's sizes
        if self.windows_created:               # camera already running: rebuild now
            self.sync_view_windows(force=True)
        self.apply_device_ui()

    def apply_device_ui(self):
        control = bool(self.device.get("control"))
        client = bool(self.device.get("calibration_client"))
        for widget in (self.config_btn, self.inv_y, self.inv_ab, self.inv_xy, self.record_btn,
                       self.play_btn, self.save_tas_btn, self.load_tas_btn,
                       self.ip_row_label, self.ip_edit):
            widget.setEnabled(control)
        self.auto_calib_btn.setEnabled(control and client)
        self.ip_row_label.setText(f"{self.device['friendly_name']} IP:")
        self.instr.setText(self.device_instructions())
        self.status_label.setText(
            "No control path: capture only" if not control else f"Device: {self.device['friendly_name']}")

    def start_sampling_color(self):
        self.sampling_color = True
        self.status_label.setText("Click screen area in 'ROI Selector' for fallback color sample")

    def start_touch_calibration(self):
        # Feature removed
        pass

    def update_tolerance(self, val):
        state.hsv_tolerance = val
        update_sampled_color_model({
            "hsv_mean": np.array(state.last_sampled_hsv, dtype=np.float32),
            "hsv_std": np.array(state.sampled_hsv_std, dtype=np.float32),
            "exg_mean": state.sampled_exg_mean,
            "exg_std": state.sampled_exg_std,
        })

    def toggle_mask_view(self, checked):
        state.show_mask = checked

    def toggle_combined(self, checked):
        self.combined_view = checked
        if self.windows_created:
            self.sync_view_windows(force=True)

    def toggle_record(self):
        if not state.is_recording:
            state.tas_frames = []
            state.is_recording = True
            self.record_btn.setText("Stop Recording")
            self.signals.status_update.emit("Recording...")
        else:
            state.is_recording = False
            release_hex = get_release_packet().hex()
            for _ in range(5): state.tas_frames.append(release_hex)
            self.record_btn.setText("Record")
            self.signals.status_update.emit(f"Recorded {len(state.tas_frames)} frames")

    def toggle_play(self):
        if not state.tas_frames:
            self.signals.error_occurred.emit("No TAS data loaded")
            return
        if not state.is_playing:
            state.current_play_idx = 0
            state.is_playing = True
            self.play_btn.setText("Stop Playback")
            self.signals.status_update.emit("Playing...")
        else:
            state.is_playing = False
            self.play_btn.setText("Play")
            send_packet(get_release_packet()) 
            self.signals.status_update.emit("Playback stopped")

    def heartbeat_loop(self):
        next_tick = time.perf_counter()
        while state.heartbeat_running:
            # No-control devices (e.g. Switch) must not map the gamepad or send anything.
            if self.control_enabled:
                if state.is_playing:
                    if state.current_play_idx < len(state.tas_frames):
                        frame_hex = state.tas_frames[state.current_play_idx]
                        ba = bytes.fromhex(frame_hex)
                        send_packet(ba)
                        state.current_play_idx += 1
                    else:
                        state.is_playing = False
                        send_packet(get_release_packet())
                else:
                    ba = get_packet_data()
                    if ba:
                        send_packet(ba)
                        if state.is_recording:
                            state.tas_frames.append(ba.hex())

            next_tick += TICK_RATE
            sleep_time = next_tick - time.perf_counter()
            if sleep_time > 0:
                time.sleep(sleep_time)
            else:
                next_tick = time.perf_counter()

    def save_tas(self):
        if not state.tas_frames: return
        path, _ = QFileDialog.getSaveFileName(self, "Save TAS", "", "JSON Files (*.json)")
        if path:
            with open(path, 'w') as f: json.dump(state.tas_frames, f)

    def load_tas(self):
        path, _ = QFileDialog.getOpenFileName(self, "Load TAS", "", "JSON Files (*.json)")
        if path:
            with open(path, 'r') as f: state.tas_frames = json.load(f)
            self.signals.status_update.emit(f"Loaded {len(state.tas_frames)} frames")

    def camera_worker(self):
        while self.running:
            ret, frame = self.threaded_cap.read()
            if not ret or frame is None:
                time.sleep(0.01)
                continue
            with self.frame_lock:
                self.latest_frame = frame.copy()

    def create_opencv_windows(self):
        cv2.namedWindow("ROI Selector", cv2.WINDOW_NORMAL | cv2.WINDOW_KEEPRATIO)
        cv2.setMouseCallback("ROI Selector", self.mouse_roi_callback)
        self.sync_view_windows(force=True)
        self.windows_created = True

    def ensure_view_windows(self):
        screens = self.device["screens"]
        if self.top_view_window is None:
            self.top_view_window = ScreenViewWindow(f'{screens[0]["name"].capitalize()} Screen', self.top_target)
        if self.bottom_view_window is None and len(screens) > 1:
            self.bottom_view_window = ScreenViewWindow(
                f'{screens[1]["name"].capitalize()} Screen', self.bottom_target,
                self.handle_bottom_touch if self.device.get("control") else None)
        if self.combined_view_window is None:
            self.combined_view_window = ScreenViewWindow(
                self.combined_window_name(), self.combined_size,
                self.handle_combined_touch if self.device.get("control") else None)

    def combined_window_name(self):
        return f'{self.device["friendly_name"]} Combined'

    def sync_view_windows(self, force=False):
        mode = "combined" if self.combined_view else "split"
        if not force and self.view_mode == mode:
            return
        self.ensure_view_windows()

        if mode == "combined":
            self.destroy_opencv_window("Top Screen")
            self.destroy_opencv_window("Bottom Screen")
            self.top_view_window.hide()
            if self.bottom_view_window is not None:
                self.bottom_view_window.hide()
            self.combined_view_window.show()
            self.combined_view_window.set_frame(self.make_combined_view())
        else:
            self.destroy_opencv_window(self.combined_window_name())
            self.combined_view_window.hide()
            self.top_view_window.show()
            if self.bottom_view_window is not None:
                self.bottom_view_window.show()
            if self.last_top_frame is not None:
                self.top_view_window.set_frame(self.last_top_frame)
            if self.bottom_view_window is not None and self.last_bottom_frame is not None:
                self.bottom_view_window.set_frame(self.last_bottom_frame)
        cv2.waitKey(1)
        self.view_mode = mode

    def make_combined_view(self):
        width, height = self.combined_size
        combined = np.full((height, width, 3), 24, dtype=np.uint8)
        screens = self.device["screens"]
        cv2.putText(combined, screens[0]["name"].upper(), (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (120, 120, 120), 2)
        if len(screens) > 1:
            cv2.putText(combined, screens[1]["name"].upper(), (52, 268), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (120, 120, 120), 2)
        frames = [self.last_top_frame, self.last_bottom_frame]
        for i, screen in enumerate(screens):
            frame = frames[i] if i < len(frames) else None
            if frame is not None:
                combined[screen["y"]:screen["y"] + screen["height"],
                         screen["x"]:screen["x"] + screen["width"]] = frame
        if state.touchScreenPressed:
            draw_touch = (state.touchScreenPosition.x() + self.touch_offset[0],
                          state.touchScreenPosition.y() + self.touch_offset[1])
            cv2.circle(combined, draw_touch, 6, (0, 0, 255), -1)
        return combined

    def destroy_opencv_window(self, name):
        try:
            cv2.destroyWindow(name)
        except cv2.error:
            pass

    def hide_view_windows(self):
        for window in (self.top_view_window, self.bottom_view_window, self.combined_view_window):
            if window is not None:
                window.hide()

    def mouse_roi_callback(self, event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN:
            if self.roi_locked or len(self.screens) >= len(self.device["screens"]):
                return

            if self.sampling_color:
                with self.frame_lock:
                    if self.latest_frame is not None:
                        h, w = self.latest_frame.shape[:2]
                        x_clamped = max(0, min(w-1, x))
                        y_clamped = max(0, min(h-1, y))
                        sample = sample_screen_color(self.latest_frame, x_clamped, y_clamped)
                        update_sampled_color_model(sample)
                        self.sampling_color = False
                        self.status_label.setText(
                            f"Sampled patch HSV: {state.last_sampled_hsv}, std: "
                            f"{[int(round(v)) for v in state.sampled_hsv_std]}"
                        )
                return

            self.roi_points.append([x, y])
            self.all_points.append((x, y))
            if len(self.roi_points) == 4:
                self.screens.append(np.array(self.roi_points, dtype=np.float32).reshape(4, 2))
                self.roi_points = []
                self.signals.status_update.emit(f"ROI {len(self.screens)} selected")
                if len(self.screens) >= len(self.device["screens"]):
                    self.roi_locked = True
                    self.lock_roi_checkbox.setChecked(True)
                    self.signals.status_update.emit("ROIs complete. Locked.")

    def handle_touch_event(self, action, raw_x, raw_y):
        raw_x = max(0, min(int(raw_x), state.touchScreenWidth - 1))
        raw_y = max(0, min(int(raw_y), state.touchScreenHeight - 1))

        if action == "press":
            state.touchScreenPressed = True
            state.touchScreenPosition = QPoint(raw_x, raw_y)
        elif action == "move":
            state.touchScreenPosition = QPoint(raw_x, raw_y)
        elif action == "release":
            state.touchScreenPressed = False

    def handle_bottom_touch(self, action, x, y):
        self.handle_touch_event(action, x, y)

    def handle_combined_touch(self, action, x, y):
        self.handle_touch_event(action, x - self.touch_offset[0], y - self.touch_offset[1])

    def get_local_ip(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
        except:
            ip = "127.0.0.1"
        finally:
            s.close()
        return ip

    def start_remote_server(self):
        ip = self.get_local_ip()
        self.remote_server = RemoteCamServer(self)
        self.remote_server.start()
        self.remote_btn.setEnabled(False)
        self.url_edit.setText(f"http://{ip}:5000")
        self.use_ip_cam.setChecked(True)
        self.status_label.setText(f"Server at: http://{ip}:5000")
        QMessageBox.information(self, "Remote Cam", f"Open this URL on your iPhone:\nhttp://{ip}:5000")
        
        # We also need to start the display loop
        self.running = True
        self.create_opencv_windows()
        self.display_timer = QTimer()
        self.display_timer.timeout.connect(self.update_display)
        self.display_timer.start(33)
        self.stop_btn.setEnabled(True)

    def start_camera(self):
        if self.use_ip_cam.isChecked():
            url = self.url_edit.text().strip()
            if not url:
                self.signals.error_occurred.emit("Please enter an IP Cam URL or uncheck 'Use IP Camera'")
                return
            source = url
            state.settings.setValue("camUrl", url)
        else:
            source = self.camera_combo.currentIndex()
        
        state.settings.setValue("useIpCam", self.use_ip_cam.isChecked())
        
        self.threaded_cap = ThreadedCamera(source)
        if not self.threaded_cap.cap.isOpened():
            self.signals.error_occurred.emit(f"Cannot open: {source}")
            return
        
        self.running = True
        self.camera_thread = threading.Thread(target=self.camera_worker, daemon=True)
        self.camera_thread.start()
        self.create_opencv_windows()
        self.start_btn.setEnabled(False); self.stop_btn.setEnabled(True)
        self.display_timer = QTimer()
        self.display_timer.timeout.connect(self.update_display)
        self.display_timer.start(33)

    def stop_camera(self):
        self.running = False
        if hasattr(self, 'display_timer'):
            self.display_timer.stop()
            try:
                self.display_timer.timeout.disconnect(self.update_display)
            except TypeError:
                pass
        if self.camera_thread:
            self.camera_thread.join(timeout=1.0)
            self.camera_thread = None
        threaded_cap = getattr(self, 'threaded_cap', None)
        if threaded_cap is not None:
            threaded_cap.stop()
            self.threaded_cap = None
        self.hide_view_windows()
        self.destroy_opencv_window("ROI Selector")
        self.windows_created = False; self.view_mode = None
        self.start_btn.setEnabled(True); self.stop_btn.setEnabled(False)

    def reset_rois(self):
        self.screens.clear(); self.all_points.clear(); self.roi_points.clear()
        self.last_top_frame = None
        self.last_bottom_frame = None
        self.roi_locked = False
        self.lock_roi_checkbox.setChecked(False)
        self.signals.status_update.emit("ROIs Reset & Unlocked")

    def start_interactive_calibration(self):
        if self.latest_frame is None:
            self.signals.error_occurred.emit("Start camera first!")
            return
        if not self.device.get("control"):
            self.signals.error_occurred.emit(f"No control path for {self.device['friendly_name']}")
            return
        if not state.ipAddress:
            self.signals.error_occurred.emit(f"Set {self.device['friendly_name']} IP address first!")
            return
        self.roi_points.clear()
        self.signals.status_update.emit("Run calibration client on 3DS, then show tag 0 / tag 1 as prompted")
        self.calibration_manager.start()

    def update_display(self):
        with self.frame_lock: frame = self.latest_frame.copy() if self.latest_frame is not None else None
        if frame is None: return
        if self.windows_created:
            self.sync_view_windows()
        
        # Run calibration logic
        new_screens = self.calibration_manager.process(frame)
        if new_screens:
            self.screens = new_screens
            self.roi_locked = True
            self.lock_roi_checkbox.setChecked(True)
            self.all_points = []
            for screen in self.screens:
                for pt in screen:
                    self.all_points.append(tuple(pt.astype(int)))
            self.signals.status_update.emit("Calibration Complete! ROIs Locked.")

        display = frame.copy()
        
        # Real-time mask feedback
        if state.show_mask:
            mask = build_calibration_mask(frame)
            display = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)

        # Visual feedback for calibration
        if self.calibration_manager.state != CalibrationState.IDLE and self.calibration_manager.state != CalibrationState.DONE:
             cv2.putText(display, "CALIBRATING: Follow instructions", (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
             if self.calibration_manager.current_candidate is not None:
                 pts = self.calibration_manager.current_candidate.astype(int)
                 cv2.polylines(display, [pts], True, (0, 255, 255), 2)
                 cv2.putText(display, f"Stability: {self.calibration_manager.stable_count}/5", (50, 90), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)

        for pt in self.all_points: cv2.circle(display, pt, 6, (0, 255, 0), -1)
        if len(self.roi_points) > 1:
            for i in range(1, len(self.roi_points)):
                cv2.line(display, tuple(self.roi_points[i-1]), tuple(self.roi_points[i]), (255, 255, 0), 2)

        top = None
        bottom = None

        if len(self.screens) >= 1:
            top = self.warp_to_target(frame, self.screens[0], self.top_target)
            self.last_top_frame = top

            # AI Logic
            if self.ai_manager is not None and self.ai_manager.is_recording:
                self.ai_manager.add_sample(top, [state.lx, state.ly, state.rx, state.ry], state.buttons)
            
            if state.ai_controlled and self.ai_manager is not None and self.ai_manager.is_active:
                axes, buttons = self.ai_manager.predict(top)
                if axes is not None:
                    state.lx, state.ly, state.rx, state.ry = axes
                    state.buttons = int(buttons)

        if len(self.screens) >= 2:
            bottom = self.warp_to_target(frame, self.screens[1], self.bottom_target)
            self.last_bottom_frame = bottom

        if self.combined_view:
            combined = self.make_combined_view()
            if self.combined_view_window is not None:
                self.combined_view_window.set_frame(combined)
        else:
            if top is not None:
                if self.top_view_window is not None:
                    self.top_view_window.set_frame(top)
            if bottom is not None:
                if state.touchScreenPressed:
                    cv2.circle(
                        bottom,
                        (state.touchScreenPosition.x(), state.touchScreenPosition.y()),
                        6,
                        (0, 0, 255),
                        -1
                    )
                if self.bottom_view_window is not None:
                    self.bottom_view_window.set_frame(bottom)

        if hasattr(self, 'threaded_cap') and self.threaded_cap:
            fps = self.threaded_cap.cap.get(cv2.CAP_PROP_FPS)
            self.fps_label.setText(f"FPS: {fps:.1f}")
        else:
            self.fps_label.setText("FPS: --")
            
        cv2.imshow("ROI Selector", display)
        cv2.waitKey(1)


    def warp_to_target(self, image, pts, target_size):
        rect = np.zeros((4, 2), dtype="float32")
        s = pts.sum(axis=1); rect[0] = pts[np.argmin(s)]; rect[2] = pts[np.argmax(s)]
        diff = np.diff(pts, axis=1); rect[1] = pts[np.argmin(diff)]; rect[3] = pts[np.argmax(diff)]
        w, h = target_size
        dst = np.array([[0, 0], [w-1, 0], [w-1, h-1], [0, h-1]], dtype=np.float32)
        M = cv2.getPerspectiveTransform(rect, dst)
        return cv2.warpPerspective(image, M, (w, h))

    def closeEvent(self, e): 
        state.is_playing = False
        state.heartbeat_running = False
        self.stop_camera(); 
        e.accept()

def main():
    global _app, _splash
    print("DEBUG: Entering main")
    app = _app
    splash = _splash

    splash.showMessage("Starting UC2...", Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignCenter, QColor("white"))
    app.processEvents()

    print("DEBUG: QApplication created")
    style = """
    QWidget { background-color: #1e1e1e; color: #e0e0e0; font-family: sans-serif; }
    QLineEdit, QComboBox, QTextEdit { background-color: #2d2d2d; border: 1px solid #3d3d3d; border-radius: 4px; color: #fff; padding: 4px; }
    QPushButton { background-color: #333; border: 1px solid #444; border-radius: 6px; padding: 8px; color: #fff; font-weight: bold; }
    QPushButton:hover { background-color: #444; }
    QGroupBox { border: 1px solid #444; margin-top: 15px; font-weight: bold; padding-top: 20px; }
    QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; color: #aaa; }
    """
    app.setStyleSheet(style)
    splash.showMessage("Initializing UI...", Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignCenter, QColor("white"))
    app.processEvents()

    print("DEBUG: Style set")
    win = AppWindow()
    print("DEBUG: Window created")
    win.show()
    splash.finish(win)
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
