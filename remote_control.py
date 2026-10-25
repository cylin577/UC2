"""Per-device remote-control backends. Transport + device HID only.
Packet building and camera/ROI/UI stay in uc2.py.

uc2.py imports this module; this module never imports uc2.py, so adding a
backend for a new device cannot drag the app (or its cv2/PyQt imports) into a
cycle. Full contract: docs/adding-devices.md, "Remote-control backend contract"."""
import socket
import struct
import time

# InputRedirection (3DS homebrew) listens on this UDP port.
INPUT_REDIRECTION_PORT = 4950

# A HID report is 5 little-endian u32s: hidPad, touch, circle pad, CPP, interface.
HID_FORMAT = '<IIIII'
# The idle report: no button, no stick, no touch, no interface button.
HID_RELEASE = (0xfff, 0x2000000, 0x7ff7ff, 0x80800081, 0)
# hidPad bit per 3DS button: A = bit 0, Select = bit 2.
HID_PAD_A = 0xffe
HID_PAD_SELECT = 0xffb

class RemoteBackend:
    """Interface every per-device backend implements (see docs/adding-devices.md)."""

    # True only for a device with a real control path; gates the UI and every send.
    control_enabled = True

    def send_packet(self, ba):
        """Hand one HID report to the device."""
        raise NotImplementedError

    def send_a_press(self):
        """Tap A: advance a client UI (auto-calibration moves top screen -> bottom)."""
        raise NotImplementedError

    def send_select_press(self):
        """Tap Select: confirm / finish a client UI (auto-calibration ends here)."""
        raise NotImplementedError

class ThreeDSBackend(RemoteBackend):
    """3DS over InputRedirection: HID reports as UDP datagrams to port 4950.

    The target IP is read from state.ipAddress on every send (lazy, as before),
    so editing it in the UI takes effect without re-creating the backend.
    The socket is opened on first send, never at construction: make_backend()
    runs during load_device() and a dead IP must not break startup."""

    def __init__(self, state):
        self.state = state
        self._socket = None

    def send_packet(self, ba):
        if not self.state.ipAddress:
            return
        if self._socket is None:
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self._socket.sendto(ba, (self.state.ipAddress, INPUT_REDIRECTION_PORT))
        except:
            pass

    def send_a_press(self):
        self._tap(struct.pack(HID_FORMAT, HID_PAD_A, *HID_RELEASE[1:]))

    def send_select_press(self):
        self._tap(struct.pack(HID_FORMAT, HID_PAD_SELECT, *HID_RELEASE[1:]))

    def _tap(self, press_packet):
        # Hold for 100ms so the 3DS sees the press, then go idle again.
        self.send_packet(press_packet)
        time.sleep(0.1)
        self.send_packet(get_release_packet())

class NullBackend(RemoteBackend):
    """No control path (e.g. Nintendo Switch): capture, ROI and AI only.
    Sends nothing, so CalibrationManager presses are structurally silent."""

    control_enabled = False

    def send_packet(self, ba):
        pass

    def send_a_press(self):
        pass

    def send_select_press(self):
        pass

def get_release_packet():
    """The "no button, no stick, no touch" HID report."""
    return struct.pack(HID_FORMAT, *HID_RELEASE)

def make_backend(device, state):
    """Backend for one device.yaml entry: control: true -> 3DS UDP, else null.

    This is the seam for new devices: add a branch here (and a backend class)
    when a device gets a control path of its own."""
    if device.get("control"):
        return ThreeDSBackend(state)
    return NullBackend()
