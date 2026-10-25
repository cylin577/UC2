
[![Build and Release](https://github.com/cylin577/UC2/actions/workflows/release.yml/badge.svg)](https://github.com/cylin577/UC2/actions/workflows/release.yml)

# UC2
**U**niversal **C**apture and **C**ontrol

Capture AND Control your 3DS — no expensive AF hardware
## Why?
I’ve got an O3DS LL, and I wanted to stream its screens to my computer —  
but that’s something only ~~Apple~~ the *New 3DS* can do.  
So I made this!  

UC2 lets you stream and control **any 3DS**, using just a camera.  
No capture card, no soldering.

## Installation

### Option 1: Grab a prebuilt binary
Head to the [Releases](https://github.com/cylin577/UC2/releases) page and download the latest build for your platform:
- **Windows:** `UC2-windows-x64.exe`
- **Linux x64:** `UC2-linux-x64`
- **Linux aarch64:** `UC2-linux-aarch64`

No Python or dependencies needed — just download and run.

### Option 2: Clone and run from source
```bash
git clone https://github.com/cylin577/UC2
cd 3DSC2
```

#### CUDA Support
Prebuilt binaries are **CPU-only** to minimize size (~250MB vs ~4GB).  
If you need GPU acceleration (NVIDIA only), build from source using the default `pyproject.toml`:

```bash
uv sync --all-extras
```
For CPU-only source install:
```bash
cp pyproject.cpu.toml pyproject.toml
uv sync --all-extras
```

### Installing UC2C (3DS)
You can install the calibration companion app directly from Universal-Updater.

Search for:
```text
UC2C
```

Or grab the `.3dsx` / `.cia` from the [Releases](https://github.com/cylin577/UC2/releases) page.

## Devices
Supported consoles are listed in `device.yaml` at the repo root — id, friendly name, screen count, screen size and aspect ratio.  
On startup the app checks GitHub and replaces your local `device.yaml` when the sha256 differs (applies on next start).  
A **Nintendo Switch** works for capture, ROI and AI only — no control path and no calibration client.  
The **3DS** stays the only device with control and auto-calibration.

## How to use
Here’s all you need to get started:

- A working Python environment (using `uv` is recommended)
- A webcam or camera
- A computer or Raspberry Pi
- A 3DS with InputRedirection enabled (Only requried when using the control feature)
- A brain (optional)

This project uses **uv** for seamless dependency management.

1. Run the application
   ```bash
   uv run uc2.py
   ```
   *Note: `uv` will automatically handle all dependencies including OpenCV, PyQt6, and Pygame.*

2. Build a standalone executable (optional)
   ```bash
   uv run pyinstaller uc2.spec
   ```
   The PyInstaller build uses `resources/splash.png` as the splash image and writes the executable to `dist/UC2`.

3. Setup & Calibration
   - Enter your **3DS IP Address**.
   - Select your camera from the dropdown and click **Start Camera**.
   - **Auto Calibration (Recommended):**
     1. Run the `uc2_calib` homebrew app on your 3DS.
     2. Click **Auto Calibrate** in the PC app.
     3. Follow the instructions: Show **AprilTag 0** on the Top Screen, then press `A` on the 3DS to show **AprilTag 1** on the Bottom Screen.
     4. The app will automatically detect and lock the ROIs.
   - **Manual Calibration:** If auto-calibration fails, click 4 corners of your **Top Screen**, followed by 4 corners of your **Bottom Screen** in the "ROI Selector" window. Click **Lock ROIs** once finished.
   - The Top and Bottom screens will appear in separate (or combined) resizable windows.

4. Interaction & TAS
   - **Touch Control:** Click or drag directly on the **Bottom Screen** (or combined) window to control the 3DS touch screen.
   - **Gamepad:** Connect a controller to use physical buttons/sticks. Use **Button Config** to map your controller by recording presses.
   - **Event Replay:** Use the **Record**, **Play**, and **Save/Load Event** buttons to automate or replay your gameplay with 20Hz precision.

That’s it !

## TODOs
 
 1. Super-Resolution support? (But nothing can't be solved using a 4K camera)
 2. Stabilized tracking? (Captured screen won't shift around when you play rhythm games)
 3. Improve AI Agent training efficiency.

## Contribute
If you found this project useful, please star this repo! If you can code, feel free to help with the TODOs or suggest new features via Pull Requests.
