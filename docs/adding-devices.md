# Adding device support & how calibration works

For contributors. Covers: adding a device to `device.yaml`, how auto-calibration
works end to end, how to build your own calibration client, and the
remote-control backend contract for a device that needs its own transport.

File map:

| File | Owns |
|---|---|
| `device.yaml` (repo root) | the device registry — what consoles exist and what each can do |
| `devices.py` | loading, validating and updating the registry |
| `uc2.py` | Qt app: camera, ROI, calibration state machine, HID packet building, 20 Hz loop |
| `remote_control.py` | per-device backends: transport + HID presses only |
| `calibration_client/` | the 3DS homebrew that displays the calibration tags |
| `ai_agent.py` | optional behavior-cloning subsystem (unrelated to control) |

## 1. Adding a device

### `device.yaml` schema

Every key the app reads (see [device.yaml](../device.yaml) for two working
examples, 3DS and Switch):

| Key | Read by | Meaning |
|---|---|---|
| `default_device` | `devices.py:109` `default_device_id` | device id selected at first start; falls back to the first entry |
| `devices[].id` | `devices.py:102`, `uc2.py:946` | stable key stored in QSettings as `deviceId`; never rename once shipped |
| `devices[].friendly_name` | `uc2.py:1029` etc. | shown in the device dropdown, window titles, IP row label |
| `devices[].control` | `uc2.py:950`, `remote_control.py:93` | `true` → a control path exists: gates the config/IP/TAS widgets, ARM auto-calibration, touch, and builds a real backend |
| `devices[].calibration_client` | `uc2.py:1139`, `uc2.py:1262` | `true` → the **Auto Calibrate** button is enabled; the app knows a client exists for this device |
| `devices[].screens[]` | `uc2.py:956` | one entry per physical screen, top first — order is the stacking order |
| `screens[].name` | `uc2.py:1384`, `uc2.py:1428` | window titles (`"Top Screen"`) and combined-view labels |
| `screens[].width`, `screens[].height` | `uc2.py:958-959` | warp targets for each screen |
| `screens[].aspect_ratio` | nothing (yet) | descriptive only — keep it consistent with width/height |
| `screen_count` | nothing (yet) | descriptive only; the app derives it from `len(screens)` |
| `screens[].x`, `screens[].y` | `devices.py:113` `screen_layout` | **generated, never authored** — filled at load time |

Screens stack vertically and are horizontally centered on the combined canvas
(`devices.py:113`): for the 3DS that is a 400x240 top at (0,0) and a 320x240
bottom at (40,240), giving the 400x480 combined view. A one-screen device simply
produces a one-entry canvas.

### Load flow

```
uc2.py:65      device_data = load_devices()
               └─ devices.py:76  load_devices()       local copy → bundled fallback → raise
                    └─ devices.py:32 valid_device_data() → parsed → returned to the app
uc2.py:945     AppWindow.load_device(device_id)
               └─ devices.py:102 device_by_id  →  devices.py:109 default_device_id fallback
                    ├─ self.device, self.device_id, self.control_enabled
                    ├─ remote_control.py:93 make_backend(device, state)  → self.remote_backend
                    ├─ devices.py:113 screen_layout(device)             → self.combined_size, screens[].x/y
                    ├─ self.top_target / self.bottom_target (None when one screen)
                    ├─ state.touchScreenWidth/Height, self.touch_offset  (bottom screen, control only)
                    └─ QSettings deviceId
uc2.py:1260    AppWindow.apply_device_ui()   enables/disables widgets from control + calibration_client
```

`uc2.py:989` `setup_variables()` calls `load_device` before any UI exists, so the
backend and the touch geometry always exist before the first frame.
`uc2.py:1249` `switch_device()` does the same live: it rebuilds the backend,
drops the old view windows, resets ROIs (they are geometry-specific) and re-runs
`apply_device_ui`.

### GitHub checksum updater

`devices.py:127` `replace_device_yaml_if_changed()`, started as a daemon thread by
`devices.py:153` `start_update_check()` (`uc2.py:941`):

1. GET `DEVICE_YAML_URL` (`devices.py:12`, 5 s timeout).
2. sha256(local) == sha256(remote) → return, nothing happens.
3. Remote copy is run through `valid_device_data` first — a bad upstream commit
   is never written, so it cannot brick a local install.
4. Write to `device.yaml.tmp`, then `os.replace` (atomic).
5. The app shows "device.yaml updated from GitHub (applies on next start)": the
   registry is read once at import, so a new device appears after a restart.

### Validation at load

`valid_device_data` (`devices.py:32`) rejects: non-dict data, no/empty `devices`,
non-dict entries, missing `id` or `friendly_name`, missing/empty `screens`, and
`width`/`height` that are not positive ints (`True` is rejected explicitly — it is
an `int` subclass and would pass as a 1-pixel screen). `default_device`, when
present, must name an existing device.

`load_devices` prefers the writable local copy, falls back to the copy bundled in
the exe (`sys._MEIPASS`), and raises `ValueError`/`FileNotFoundError` if both are
bad — there is no silent "run with a broken registry" path. To add a device:
append an entry to `device.yaml` in the repo and push it; the checksum updater
pushes it out to installed apps.

## 2. How auto-calibration works

3DS-only: `start_interactive_calibration` (`uc2.py:1587`) requires a started
camera, `control: true`, and an IP. It then runs
`CalibrationManager` (`uc2.py:776`) over every camera frame
(`uc2.py:1608`).

### State machine

| State | What happens | Next |
|---|---|---|
| `WAIT_TOP` | look for tag id 0 (`TOP_CALIB_TAG_ID`, `uc2.py:80`) fitted to the 400x240 top screen quad | stable → send **A** → |
| `WAIT_TRANSITION_1` | sleep 2 s while the client swaps to the bottom-screen tag | timeout → |
| `WAIT_BOTTOM` | look for tag id 1 fitted to the 320x240 bottom quad, rejecting anything within 50 px of the top marker's center | stable → send **Select** → `DONE` |
| `DONE` | returns `[top_rect, bottom_rect]`, which become the two ROIs | — |

`process()` bails out immediately (`uc2.py:812`) unless the **live**
`control_enabled` callable is true — a calibration started on a 3DS stays silent
if you switch to a Switch mid-run.

### Marker → screen extrapolation

Detection is AprilTag family **36h11** with corner refinement when OpenCV exposes
it (`uc2.py:796-797`). `find_marker_screen` (`uc2.py:893`):

1. Detect markers; keep the one whose id matches the tag being searched for.
2. Build the *expected* marker quad `get_marker_rect` (`uc2.py:881`): a
   `CALIB_TAG_SIZE`=200 px square, centered, clamped to
   `min(200, screen_w, screen_h - 2*20)` so it always fits.
3. Build the screen quad from the target size.
4. `cv2.getPerspectiveTransform(marker_quad, marker_detected)` → apply it to the
   screen quad: the camera's view of the tag decides where the screen's corners
   land, so the result is a camera-space quad for the whole screen, no manual
   clicking.

### Noise gates

- `check_stability` (`uc2.py:859`): the marker's center must stay within **30 px**
  for **5 consecutive frames** (`REQUIRED_STABILITY`), otherwise the candidate
  resets. Wrong readouts shimmer; a real marker does not.
- `is_overlapping` (`uc2.py:915`): a bottom-screen candidate closer than **50 px**
  to the top marker's center is the same physical screen seen twice and is
  dropped.

### Geometry after calibration

`warp_to_target` (`uc2.py:1690`) perspective-warps each detected quad onto its
screen size. The combined view (`make_combined_view`, `uc2.py:1424`) pastes the
warped screens at their `x/y` on the `combined_size` canvas. Touch works in
screen space: `handle_touch_event` (`uc2.py:1485`) clamps to
`state.touchScreenWidth/Height`, and `handle_combined_touch` subtracts
`self.touch_offset` so a click on the combined view maps to the bottom screen.

### How the app presses buttons on the 3DS

The manager holds a backend (`uc2.py:932`, refreshed in `load_device` at
`uc2.py:955`) and its only device-facing actions are:

```python
self.backend.send_a_press()       # WAIT_TOP found → client swaps tag 0 → tag 1
self.backend.send_select_press()  # WAIT_BOTTOM found → client exits, calibration done
```

Each press is a HID report held 100 ms then released (`remote_control.py:68`),
sent as one UDP datagram to the device (`remote_control.py:52`). See section 4.

### Steady-state input (same transport, no state machine)

`heartbeat_loop` (`uc2.py:1326`) runs on a daemon thread for the app's lifetime
at `TICK_RATE` = 0.050 s (20 Hz, `uc2.py:79`): it reads the live gamepad/touch
into `state`, calls `get_packet_data` (`uc2.py:285`) to build the 20-byte HID
report, and hands it to `self.remote_backend.send_packet`. It also drives TAS
replay: while `state.is_playing` it replays the recorded hex frames one per tick
(a release packet when the list runs out), and while `state.is_recording` it
appends each frame's hex. For a no-control device the whole body is skipped and
the backend is a no-op, so nothing is sent.

`get_packet_data` packs five little-endian u32s
(`struct.pack('<IIIII', hidPad, touchScreenState, circlePadState, cppState, interfaceButtons)`):

| Word | Released | Pressed / moved |
|---|---|---|
| `hidPad` | `0xfff` | active-low 12-bit mask: A=bit 0, B=1, Select=2, Start=3, Right=4, Left=5, Up=6, Down=7, R=8, L=9, X=10, Y=11 (A/B and X/Y swap when the invert boxes are ticked) |
| `touchScreenState` | `0x2000000` | `(1<<24) \| (y<<12) \| x`, `x`/`y` = 0..0xfff scaled from bottom-screen pixels |
| `circlePadState` | `0x7ff7ff` | `(y<<12) \| x`, `x = lx*0x5d0 + 0x800` clamped 0..0xfff |
| `cppState` | `0x80800081` | `(y<<24) \| (x<<16) \| (irButtons<<8) \| 0x81`, 8-bit CPP axes via `sqrt(0.5)*(rx±ry)*0x7f + 0x80`; `irButtons` is the ordered list `[ZR, ZL]` (`uc2.py:303`) packed as `\|= 1 << (i+1)` (`uc2.py:325`) — ZR = bit 1, ZL = bit 2, bit 0 unused |
| `interfaceButtons` | `0` | bit 0 HOME, bit 1 POWER, bit 2 POWER long |

Pacing note: `next_tick += TICK_RATE` with a single sleep, and a resync to
`perf_counter()` when a tick overruns — a fixed 20 Hz clock that never drifts and
never bursts when a frame is slow. Keep that tail untouched when touching this
loop.

## 3. Building a custom calibration client

`calibration_client/` is the 3DS homebrew **UC2C** (`uc2_calib.3dsx` /
`uc2_calib.cia`, also on Universal-Updater). It exists only to put a known
AprilTag on a known screen at a known size; everything else is the PC app's job.

### What it does

- `calibration_client/source/main.c:79` `main()`: draws tag 0 on the top screen
  and clears the bottom; **A** toggles to tag 1 on the bottom; **Start** or
  **Select** exits.
- Tags are drawn black-on-white: `set_pixel` (`main.c:33`) writes the 3DS
  framebuffer directly (3-byte BGR, bottom-up rows), `clear_screen` fills it,
  `draw_tag` (`main.c:51`) blits an 8x8 grid centered in the screen.

### What it deliberately does NOT do

No network, no file I/O, no input beyond A/Select, no camera access: the tag grid
is compiled in, so the client cannot drift out of sync with the PC app, and it
needs no permissions or wifi. The PC side owns detection, geometry and input.

### Toolchain and build

Requirements: **devkitPro/devkitARM** (`DEVKITPRO`, default `/opt/devkitpro`) with
ctrulib and `bannertool`/`smdhtool`/`makerom` for packaging.

```bash
make -C calibration_client 3dsx    # dev build:  uc2_calib.3dsx + .elf
make -C calibration_client cia     # installable: uc2_calib.cia (+ banner/icon via `generate`)
make -C calibration_client clean
```

`Makefile:104` `generate` builds `resources/icon.bin` (smdh) and
`resources/banner.bnr` from `resources/icon.png`, `resources/banner.png` and
`resources/audio.wav`; `Makefile:120` `cia` needs the `.rsf` template
(`resources/uc2_calib.rsf`, title/product code substituted at build).
No devkitPro locally? `remote_build.sh` rsyncs `calibration_client/` and
`resources/` to a build machine over ssh, runs `make 3dsx cia` there and pulls
the artifacts back into `remote_build/`.

### Tag rendering contract (the part a new client must match)

| Aspect | Value | Why |
|---|---|---|
| Family | AprilTag **36h11** | must equal `DICT_APRILTAG_36h11` in `uc2.py:794` |
| Ids | 0 on the first screen, 1 on the second | `uc2.py:80`-`81` |
| Grid | 8x8, 1 bit per cell | `TAG_GRID` in `main.c:8` |
| Exact bits | `cv2.aruco.generateImageMarker(DICT_APRILTAG_36h11, id, 8)` | the arrays in `main.c:11-31` are exactly this |
| Size | 200 px per screen / 25 px per cell, centered | must match `CALIB_TAG_SIZE` in `uc2.py:82` |
| Background | white screen fill, black tag cells | contrast for a webcam; the marker must dominate the frame |
| Screen | top screen first, then bottom | order of `screens[]` in `device.yaml` |

To generate a different tag, print the bits and copy the 8 rows into C:

```python
import cv2
d = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11)
img = cv2.aruco.generateImageMarker(d, ID, 8)   # 1 = white, 0 = black
```

### Input contract

- **A** → advance to the next calibration step. The app sends exactly one A press
  once the first tag is stable; your client should respond within its 2 s window.
- **Select** → finish. The app sends exactly one Select press when the second tag
  is found; UC2C exits on Select (or Start) so the user is dropped back to the
  home menu. A client that must stay alive can ignore Select and just return to
  its idle screen.

A press is `hidPad` = `0xffe` (A, 100 ms) and `0xffb` (Select, 100 ms) — see
`remote_control.py:19-20`.

### Porting to other hardware checklist

1. Pick a detectable fiducial (any AprilTag family, QR, ARCore markers) and fix
   one per screen; document its bit pattern next to the detector.
2. Render it at a fixed pixel size, centered, high contrast, one per screen.
3. Define how the host advances the UI: same button set, or extend
   `RemoteBackend` (section 4) with your own press methods.
4. Keep the client display-only unless there is no other way — every extra
   channel is another thing to debug on a console.
5. Set `control: true` and `calibration_client: true` for the device in
   `device.yaml`, or auto-calibration stays disabled for it.

## 4. Remote-control backend contract

`remote_control.py` is the per-device seam: transport + device HID only. Packet
building (`get_packet_data`, `uc2.py:285`), the camera/ROI/UI code and the 20 Hz
loop stay in `uc2.py`, because they read the app's global state (button remap
settings, TAS frames, the live device flag). **`remote_control.py` imports nothing
from the app, so a new backend can never create an import cycle.**

| Member | Called from | Contract |
|---|---|---|
| `control_enabled` (class attr) | nothing (yet) | inert extension hook, not a gate: `RemoteBackend`/`ThreeDSBackend` default `True`, `NullBackend` = `False` (`remote_control.py:26`, `remote_control.py:78`). The real gating lives in `uc2.py`: `AppWindow.control_enabled` (`uc2.py:950`), `device.get("control")` in `apply_device_ui` (`uc2.py:1261`), and the heartbeat check (`uc2.py:1330`) |
| `send_packet(ba)` | `heartbeat_loop` `uc2.py:1326`, `toggle_play` `uc2.py:1323` | deliver one 20-byte HID report; no-op without a valid target |
| `send_a_press()` | `CalibrationManager` `uc2.py:827` | tap A (advance the client UI) |
| `send_select_press()` | `CalibrationManager` `uc2.py:848` | tap Select (confirm/finish the client UI) |
| `get_release_packet()` | backend + `uc2.py:1306`, `uc2.py:1323` | the all-released HID report (module function, device HID) |
| `make_backend(device, state)` | `uc2.py:951` | the only construction site: `control: true` → the device's backend, else `NullBackend` |

Bundled backends: `ThreeDSBackend` (`remote_control.py:40`, UDP/InputRedirection
port 4950, lazy socket, lazy IP read from `state.ipAddress`) and `NullBackend`
(`remote_control.py:74`, sends nothing).

Adding a device with its own transport:

1. Subclass `RemoteBackend` in `remote_control.py` (keep it dependency-free:
   stdlib + the device's own library only; never import `uc2`).
2. Take whatever target info you need as constructor args or read it lazily from
   the `state` object passed by `make_backend` (`ipAddress`, `touchScreenWidth`,
   ...). Open no socket and touch no hardware in `__init__` — backends are built
   during `load_device`, before any device exists.
3. Press methods must be safe to call when the device is unreachable: swallow
   transport errors (as `ThreeDSBackend.send_packet` does) instead of raising
   into the 20 Hz thread.
4. Add one branch to `make_backend`. That is the whole registration.
5. Set `control: true` for the device in `device.yaml`.

The heartbeat thread itself stays in `uc2.py:1326`: its body reads and writes
app-global state (TAS cursor, recording buffer, live device flag) and it must
survive a live device switch, so a backend that only owns its transport is the
correct split. If a future device needs its own keep-alive thread with different
pacing, add `start_heartbeat()`/`stop_heartbeat()` to your subclass and call them
from `AppWindow.__init__`/`closeEvent` — the contract above is the minimum uc2.py
calls today.
