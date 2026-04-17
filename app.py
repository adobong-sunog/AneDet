import cv2
from dataclasses import dataclass, field
import numpy as np
import time
import os
import subprocess
import sqlite3
import hashlib
import hmac
import secrets
import base64
import csv
import json
from collections import deque
from flask import Flask, render_template, Response, jsonify, request
import onnxruntime as ort
from ultralytics import YOLO
import threading
from datetime import datetime

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "readings.db")
KEY_PATH = os.path.join(DATA_DIR, ".anedet_secure.key")
RECORDS_EXPORT_DIR = os.path.join(BASE_DIR, "databaseRecords")
HB_MODEL_INT8_PATH = os.path.join(BASE_DIR, "hb_regressor_int8.onnx")
HB_MODEL_FP32_PATH = os.path.join(BASE_DIR, "hb_regressor.onnx")
HB_CONFIG_PATH = os.path.join(BASE_DIR, "hb_regressor_config.json")

def _env_int(name, default):
    raw_val = os.environ.get(name)
    if raw_val is None:
        return default
    try:
        return int(raw_val)
    except ValueError:
        print(f"invalid {name}={raw_val!r}; using default {default}")
        return default


def _env_bool(name, default):
    raw_val = os.environ.get(name)
    if raw_val is None:
        return default
    return raw_val.strip().lower() not in ("0", "false", "no", "off")

def ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)

def _load_encryption_key():
    env_key = os.environ.get("ANEDET_DATA_KEY", "").strip()
    if env_key:
        return hashlib.sha256(env_key.encode("utf-8")).digest()
    ensure_data_dir()
    if os.path.exists(KEY_PATH):
        with open(KEY_PATH, "rb") as key_file:
            key_data = key_file.read().strip()
        if key_data:
            return hashlib.sha256(key_data).digest()
    key_data = secrets.token_bytes(32)
    with open(KEY_PATH, "wb") as key_file:
        key_file.write(base64.urlsafe_b64encode(key_data))
    try:
        os.chmod(KEY_PATH, 0o600)
    except Exception:
        pass
    return hashlib.sha256(key_data).digest()

ENCRYPTION_KEY = _load_encryption_key()

def _stream_xor(data_bytes, key_material, nonce):
    output = bytearray(len(data_bytes))
    counter = 0
    cursor = 0
    while cursor < len(data_bytes):
        block = hashlib.sha256(key_material + nonce + counter.to_bytes(8, "big")).digest()
        take = min(len(block), len(data_bytes) - cursor)
        for i in range(take):
            output[cursor + i] = data_bytes[cursor + i] ^ block[i]
        cursor += take
        counter += 1
    return bytes(output)

def encrypt_bytes(data_bytes):
    if data_bytes is None:
        return None
    nonce = secrets.token_bytes(16)
    cipher_bytes = _stream_xor(data_bytes, ENCRYPTION_KEY, nonce)
    mac = hmac.new(ENCRYPTION_KEY, nonce + cipher_bytes, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(nonce + cipher_bytes + mac).decode("ascii")

def encrypt_text(text_value):
    if text_value is None:
        return None
    text_value = str(text_value)
    if text_value == "":
        return None
    return encrypt_bytes(text_value.encode("utf-8"))

def decrypt_bytes(token_text):
    if token_text is None:
        return None
    blob = base64.urlsafe_b64decode(token_text.encode("ascii"))
    if len(blob) < 48:
        raise ValueError("invalid encrypted payload")
    nonce = blob[:16]
    mac = blob[-32:]
    cipher_bytes = blob[16:-32]
    expected_mac = hmac.new(ENCRYPTION_KEY, nonce + cipher_bytes, hashlib.sha256).digest()
    if not hmac.compare_digest(mac, expected_mac):
        raise ValueError("integrity check failed")
    return _stream_xor(cipher_bytes, ENCRYPTION_KEY, nonce)

def decrypt_text(token_text):
    if not token_text:
        return ""
    return decrypt_bytes(token_text).decode("utf-8", errors="replace")

def init_database():
    ensure_data_dir()
    conn = sqlite3.connect(DB_PATH)
    try:
        desired_columns = [
            "id",
            "created_at",
            "measurement_time",
            "predicted_hb",
            "predicted_status",
            "patient_name_enc",
        ]
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS readings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                measurement_time TEXT NOT NULL,
                predicted_hb REAL NOT NULL,
                predicted_status TEXT NOT NULL,
                patient_name_enc TEXT
            )
            """
        )
        existing_info = conn.execute("PRAGMA table_info(readings)").fetchall()
        existing_columns = [row[1] for row in existing_info]
        if existing_columns != desired_columns:
            common_columns = [col for col in desired_columns if col in existing_columns]
            conn.execute("BEGIN")
            try:
                conn.execute(
                    """
                    CREATE TABLE readings_new (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        created_at TEXT NOT NULL,
                        measurement_time TEXT NOT NULL,
                        predicted_hb REAL NOT NULL,
                        predicted_status TEXT NOT NULL,
                        patient_name_enc TEXT
                    )
                    """
                )
                if common_columns:
                    select_columns = ", ".join(common_columns)
                    conn.execute(
                        f"INSERT INTO readings_new ({select_columns}) SELECT {select_columns} FROM readings"
                    )
                conn.execute("DROP TABLE readings")
                conn.execute("ALTER TABLE readings_new RENAME TO readings")
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        conn.commit()
    finally:
        conn.close()

init_database()

# ── Camera (Raspberry Pi Camera Module 3 via Picamera2) ──────────────────────
from picamera2 import Picamera2
try:
    from libcamera import controls as _libcamera_controls
    _HAS_LIBCAMERA_CONTROLS = True
except ImportError:
    _HAS_LIBCAMERA_CONTROLS = False
    print("WARNING: libcamera controls not available — autofocus will not be configured")

picam2 = None
camera_active = False
_camera_lock = threading.Lock()  # guards picam2.capture_array() across threads
CAMERA_FRAME_WIDTH = _env_int("CAMERA_FRAME_WIDTH", 640)
CAMERA_FRAME_HEIGHT = _env_int("CAMERA_FRAME_HEIGHT", 480)

# At 10-15 cm distance, the reciprocal is 1/0.10 = 10.0 to 1/0.15 ≈ 6.67.
# LensPosition is in dioptres (1/m). Setting a manual fallback of ~8.0 targets ~12.5 cm.
MACRO_LENS_POSITION = float(os.environ.get("LENS_POSITION", "8.0"))


def _stop_camera_device():
    global picam2
    if picam2 is None:
        return
    try:
        picam2.stop()
    except Exception:
        pass
    try:
        picam2.close()
    except Exception:
        pass
    picam2 = None


def _capture_camera_frame():
    """Capture a single frame as BGR numpy array (thread-safe).

    Picamera2 'RGB888' format uses libcamera byte ordering which is
    actually BGR in memory — exactly what OpenCV expects.
    """
    with _camera_lock:
        if picam2 is None:
            return None
        try:
            frame = picam2.capture_array()
            if frame is None or frame.size == 0:
                return None
            return frame  # RGB888 is BGR in memory (libcamera convention)
        except Exception:
            return None


def start_camera():
    global picam2, camera_active
    try:
        _stop_camera_device()

        cam = Picamera2()
        config = cam.create_preview_configuration(
            main={"size": (CAMERA_FRAME_WIDTH, CAMERA_FRAME_HEIGHT), "format": "RGB888"},
        )
        cam.configure(config)
        cam.start()
        time.sleep(0.5)  # let sensor settle

        # ── Configure autofocus for macro range (10-15 cm) ────────────────
        if _HAS_LIBCAMERA_CONTROLS:
            try:
                cam.set_controls({
                    "AfMode": _libcamera_controls.AfModeEnum.Continuous,
                    "AfSpeed": _libcamera_controls.AfSpeedEnum.Fast,
                })
                print("autofocus: Continuous + Fast (Camera Module 3)")
            except Exception as af_err:
                # If Continuous AF fails (e.g. older firmware), fall back to manual lens position.
                print(f"continuous AF unavailable ({af_err}); using manual lens position")
                try:
                    cam.set_controls({
                        "AfMode": _libcamera_controls.AfModeEnum.Manual,
                        "LensPosition": MACRO_LENS_POSITION,
                    })
                    print(f"manual focus set to LensPosition={MACRO_LENS_POSITION} "
                          f"(~{100.0 / MACRO_LENS_POSITION:.0f} cm)")
                except Exception as mf_err:
                    print(f"manual lens position also failed: {mf_err}")
        else:
            print("libcamera controls not available — using camera defaults")

        picam2 = cam
        camera_active = True
        print(
            f"camera initialized (Picamera2, Camera Module 3, "
            f"size={CAMERA_FRAME_WIDTH}x{CAMERA_FRAME_HEIGHT})"
        )
        return True
    except Exception as e:
        print(f"camera init error: {e}")
        camera_active = False
        _stop_camera_device()
        return False

start_camera()

# ── Models ────────────────────────────────────────────────────────────────────
print("load models")
yolo_model = YOLO('best.pt')
hb_model_path = HB_MODEL_INT8_PATH if os.path.exists(HB_MODEL_INT8_PATH) else HB_MODEL_FP32_PATH
if not os.path.exists(hb_model_path):
    raise FileNotFoundError(
        "No hemoglobin ONNX model found. Expected hb_regressor_int8.onnx or hb_regressor.onnx in project root."
    )
_onnx_opts = ort.SessionOptions()
_onnx_opts.intra_op_num_threads = 4
_onnx_opts.inter_op_num_threads = 1
_onnx_opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
hb_session = ort.InferenceSession(
    hb_model_path,
    sess_options=_onnx_opts,
    providers=["CPUExecutionProvider"],
)
hb_input_names  = [tensor.name for tensor in hb_session.get_inputs()]
hb_output_names = [tensor.name for tensor in hb_session.get_outputs()]
HB_IMAGE_INPUT_NAME = "image"       if "image"       in hb_input_names  else hb_input_names[0]
HB_SKIN_INPUT_NAME  = "skin_features" if "skin_features" in hb_input_names else hb_input_names[-1]
HB_OUTPUT_NAME      = "hb_prediction" if "hb_prediction" in hb_output_names else hb_output_names[0]

hb_config = {}
if os.path.exists(HB_CONFIG_PATH):
    try:
        with open(HB_CONFIG_PATH, "r", encoding="utf-8") as cfg_file:
            hb_config = json.load(cfg_file) or {}
        print(f"loaded hb config: {HB_CONFIG_PATH}")
    except Exception as cfg_err:
        print(f"warning: failed to read hb config {HB_CONFIG_PATH}: {cfg_err}")

HB_SKIN_FEATURE_MEAN = np.zeros((6,), dtype=np.float32)
HB_SKIN_FEATURE_STD = np.ones((6,), dtype=np.float32)
_cfg_mean = hb_config.get("skin_feature_mean") if isinstance(hb_config, dict) else None
_cfg_std = hb_config.get("skin_feature_std") if isinstance(hb_config, dict) else None
_has_valid_feature_norm = False
if isinstance(_cfg_mean, list) and isinstance(_cfg_std, list) and len(_cfg_mean) == 6 and len(_cfg_std) == 6:
    try:
        HB_SKIN_FEATURE_MEAN = np.array(_cfg_mean, dtype=np.float32)
        HB_SKIN_FEATURE_STD = np.maximum(np.array(_cfg_std, dtype=np.float32), 1e-6)
        _has_valid_feature_norm = True
    except Exception:
        _has_valid_feature_norm = False

_cfg_cal = hb_config.get("calibration") if isinstance(hb_config, dict) else None
HB_CONFIG_CAL_SLOPE = 1.0
HB_CONFIG_CAL_INTERCEPT = 0.0
_has_valid_cfg_cal = False
if isinstance(_cfg_cal, dict):
    try:
        slope = float(_cfg_cal.get("slope", 1.0))
        intercept = float(_cfg_cal.get("intercept", 0.0))
        if np.isfinite(slope) and np.isfinite(intercept):
            HB_CONFIG_CAL_SLOPE = slope
            HB_CONFIG_CAL_INTERCEPT = intercept
            _has_valid_cfg_cal = True
    except Exception:
        _has_valid_cfg_cal = False

_cfg_anemia_threshold_g_per_l = 120.0
try:
    _cfg_anemia_threshold_g_per_l = float(hb_config.get("anemia_threshold_g_per_l", 120.0))
except Exception:
    _cfg_anemia_threshold_g_per_l = 120.0

_default_threshold_offset_gdl = 0.20  # screening-first: reduce false-normal drift near cutoff
try:
    _default_threshold_offset_gdl = float(os.environ.get("HB_ANEMIA_THRESHOLD_OFFSET_GDL", "0.20"))
except Exception:
    _default_threshold_offset_gdl = 0.20

HB_ANEMIA_THRESHOLD_GDL = float(
    os.environ.get(
        "HB_ANEMIA_THRESHOLD_GDL",
        f"{(_cfg_anemia_threshold_g_per_l / 10.0) + _default_threshold_offset_gdl:.2f}",
    )
)

HB_USE_SKIN_FEATURE_NORM = _env_bool("HB_USE_SKIN_FEATURE_NORM", True if _has_valid_feature_norm else False)
HB_USE_CONFIG_CALIBRATION = _env_bool("HB_USE_CONFIG_CALIBRATION", False)
HB_USE_LEGACY_CALIBRATION_CURVE = _env_bool(
    "HB_USE_LEGACY_CALIBRATION_CURVE",
    False,
)

if _has_valid_feature_norm and not HB_USE_SKIN_FEATURE_NORM:
    print("warning: feature normalization is disabled; this can cause strong normal-bias drift")
if HB_USE_CONFIG_CALIBRATION:
    print("warning: config calibration enabled; disable if outputs look unstable")
if HB_USE_LEGACY_CALIBRATION_CURVE:
    print("warning: legacy calibration curve enabled; this can push borderline anemia toward normal")

print(
    "runtime hb config | "
    f"skin_norm={HB_USE_SKIN_FEATURE_NORM} | "
    f"cfg_cal={HB_USE_CONFIG_CALIBRATION} "
    f"(slope={HB_CONFIG_CAL_SLOPE:.4f}, intercept={HB_CONFIG_CAL_INTERCEPT:.2f}) | "
    f"legacy_curve={HB_USE_LEGACY_CALIBRATION_CURVE} | "
    f"anemia_threshold={HB_ANEMIA_THRESHOLD_GDL:.2f}"
)
print("models loaded")

# ── Global state ──────────────────────────────────────────────────────────────
current_hb     = 0.0
last_scan_time = 0
current_status = "Ready"

# ── Tuning constants (all changes annotated) ──────────────────────────────────
BIAS_OFFSET      = float(os.environ.get("HB_BIAS_OFFSET", "0.0"))
# Slope correction: stretches predictions outward from a pivot to counter
# regression-to-mean compression.  corrected = pivot + stretch * (raw - pivot)
HB_SLOPE_PIVOT   = float(os.environ.get("HB_SLOPE_PIVOT", "12.0"))
HB_SLOPE_STRETCH = float(os.environ.get("HB_SLOPE_STRETCH", "1.3"))
# Balanced capture: sample a bit faster to improve usable sample count.
SCAN_INTERVAL    = float(os.environ.get("HB_SCAN_INTERVAL", "0.8"))

# FIX 2 – slower EMA + tighter per-step cap + wider deadband
SMOOTHING_ALPHA  = float(os.environ.get("HB_SMOOTHING_ALPHA", "0.10"))
MAX_HB_STEP      = float(os.environ.get("HB_MAX_HB_STEP", "0.08"))
# Keep upward moves conservative while allowing faster downward correction.
MAX_HB_STEP_UP   = float(os.environ.get("HB_MAX_HB_STEP_UP", "0.05"))
MAX_HB_STEP_DOWN = float(os.environ.get("HB_MAX_HB_STEP_DOWN", "0.18"))
HB_DEADBAND      = float(os.environ.get("HB_DEADBAND", "0.10"))

# FIX 2 – larger rolling window gives a more robust median
HB_HISTORY_SIZE  = _env_int("HB_HISTORY_SIZE", 15)

LIVE_NAIL_CONF_THRESHOLD    = float(os.environ.get("HB_LIVE_NAIL_CONF", "0.15"))
SESSION_NAIL_CONF_THRESHOLD = float(os.environ.get("HB_SESSION_NAIL_CONF", "0.18"))
ACQUIRE_NAIL_CONF_THRESHOLD = float(os.environ.get("HB_ACQUIRE_NAIL_CONF", "0.34"))
LIVE_MULTI_NAIL_TOP_K       = 1    # was 3 — reduce cross-finger mixing
AUTOFOCUS_STABLE_MODE       = True
YOLO_INFER_SIZE             = 640
LIVE_YOLO_INFER_SIZE        = 416

HB_CALIBRATION_PIVOT  = 11.5
HB_LOW_SLOPE          = 1.05
HB_LOW_INTERCEPT      = -0.6
HB_HIGH_SLOPE         = 1.22
HB_HIGH_INTERCEPT     = -2.555
NEAR_NORMAL_LIFT_START = 10.8
NEAR_NORMAL_LIFT_END   = 12.0
NEAR_NORMAL_LIFT_MAX   = 0.65
NORMAL_LIFT_START      = 12.0
NORMAL_LIFT_BASE       = 0.55
NORMAL_LIFT_GAIN       = 0.35
NORMAL_LIFT_MAX        = 1.40
HB_MIN = 5.0
HB_MAX = 18.0

RAW_HB_SCALE_AUTO_THRESHOLD = 40.0
RAW_HB_G_PER_L_DIVISOR      = 10.0

DISPLAY_SMOOTH_ENABLED       = False
DISPLAY_TEMPORAL_ALPHA       = 0.78
DISPLAY_BILATERAL_D          = 5
DISPLAY_BILATERAL_SIGMA_COLOR = 20
DISPLAY_BILATERAL_SIGMA_SPACE = 20

FOCUS_EVAL_INTERVAL      = 0.25
FOCUS_LOCK_MIN_SHARPNESS = 45.0
FOCUS_UNLOCK_MIN_SHARPNESS = 25.0
FOCUS_STABLE_STD         = 10.0
FOCUS_STABLE_COUNT       = 4
FOCUS_BLUR_COUNT         = 3

PREP_DURATION    = 10.0

# FIX 3 – longer acquisition window → more samples before final median
ACQUIRE_DURATION = float(os.environ.get("HB_ACQUIRE_DURATION", "16.0"))

# FIX 3 – require more samples before accepting a result
MIN_VALID_SAMPLES = _env_int("HB_MIN_VALID_SAMPLES", 6)

# Session-level stabilization controls.
FINAL_RECENT_SAMPLES = _env_int("HB_FINAL_RECENT_SAMPLES", 10)
MAX_FINAL_IQR = float(os.environ.get("HB_MAX_FINAL_IQR", "1.35"))
BORDERLINE_LOW = float(os.environ.get("HB_BORDERLINE_LOW", f"{HB_ANEMIA_THRESHOLD_GDL - 0.3:.2f}"))
BORDERLINE_HIGH = float(os.environ.get("HB_BORDERLINE_HIGH", f"{HB_ANEMIA_THRESHOLD_GDL + 0.3:.2f}"))
BORDERLINE_ANEMIA_RATIO_LOW = float(os.environ.get("HB_BORDERLINE_RATIO_LOW", "0.35"))
BORDERLINE_ANEMIA_RATIO_HIGH = float(os.environ.get("HB_BORDERLINE_RATIO_HIGH", "0.65"))
ANEMIA_GUARD_ENABLED = os.environ.get("HB_ANEMIA_GUARD", "1").strip().lower() not in ("0", "false", "no", "off")
ANEMIA_GUARD_THRESHOLD = float(os.environ.get("HB_ANEMIA_GUARD_THRESHOLD", f"{HB_ANEMIA_THRESHOLD_GDL:.2f}"))
ANEMIA_GUARD_RATIO = float(os.environ.get("HB_ANEMIA_GUARD_RATIO", "0.40"))
ANEMIA_GUARD_PCTL = float(os.environ.get("HB_ANEMIA_GUARD_PCTL", "30"))

# FIX 1 – bounding-box EMA smoothing factor (lower = more stable)
BOX_SMOOTH_ALPHA = 0.25

# YOLO live detection interval — increased from 0.30 for RPi 5 performance
YOLO_LIVE_INTERVAL = float(os.environ.get("YOLO_INTERVAL", "0.40"))

session_lock          = threading.Lock()
session_phase         = "idle"
phase_started_at      = 0.0
session_quality_ok    = False
session_quality_reason = "Press Start"
session_quality_score = 0
session_quality_tip   = "Place index fingernail in the center guide area."
session_samples       = []
scale_warning_shown   = False
inference_reset_token = 0

ADMIN_EXIT_PASSWORD = "group5"

# ── Helpers ───────────────────────────────────────────────────────────────────
def schedule_desktop_exit():
    def _shutdown_worker():
        global camera_active
        time.sleep(0.9)
        stop_capture_session()
        _stop_camera_device()
        camera_active = False
        for cmd in (
            ["pkill", "-f", "chromium-browser"],
            ["pkill", "-f", "chromium"],
            ["pkill", "-f", "chrome --kiosk"],
        ):
            try:
                subprocess.run(cmd, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                pass
        os._exit(0)
    threading.Thread(target=_shutdown_worker, daemon=True).start()

def hb_to_status(hb_value):
    if hb_value < 8.0:
        return "Severe Anemia"
    elif hb_value < 10.0:
        return "Moderate Anemia"
    elif hb_value < 12.0:
        return "Mild Anemia"
    elif hb_value <= 12.2:
        return "Borderline"
    return "Normal"


def summarize_session_samples(raw_samples):
    """Return robust session summary for final Hb/status decisions."""
    if not raw_samples:
        return None

    samples_arr = np.array(raw_samples, dtype=np.float32)

    if FINAL_RECENT_SAMPLES > 0 and len(samples_arr) > FINAL_RECENT_SAMPLES:
        samples_arr = samples_arr[-FINAL_RECENT_SAMPLES:]

    q1, q3 = np.percentile(samples_arr, [25, 75])
    iqr = float(q3 - q1)

    if iqr > 0:
        mask = ((samples_arr >= q1 - 1.5 * iqr) &
                (samples_arr <= q3 + 1.5 * iqr))
        filtered = samples_arr[mask]
        min_filtered = max(3, int(np.ceil(0.6 * len(samples_arr))))
        if len(filtered) >= min_filtered:
            samples_arr = filtered

    median_hb = float(np.median(samples_arr))
    lower_hb = float(np.percentile(samples_arr, ANEMIA_GUARD_PCTL))
    anemia_ratio = float(np.mean(samples_arr < ANEMIA_GUARD_THRESHOLD))

    return {
        "median": median_hb,
        "lower_hb": lower_hb,
        "iqr": iqr,
        "sample_count": int(len(samples_arr)),
        "anemia_ratio": anemia_ratio,
    }


def classify_session_result(hb_value, anemia_ratio, lower_hb):
    """Classify final status with a small uncertainty band near 12.0 g/dL."""
    # The anemia guard already adjusts the final Hb value during session
    # finalization. Classification should follow the adjusted numeric value
    # so hb_to_status() and the Borderline band remain authoritative.

    if BORDERLINE_LOW <= hb_value <= BORDERLINE_HIGH:
        if BORDERLINE_ANEMIA_RATIO_LOW <= anemia_ratio <= BORDERLINE_ANEMIA_RATIO_HIGH:
            return "Borderline - Recheck"
    return hb_to_status(hb_value)


def build_quality_feedback(nail_detected, nail_conf, focus_ok):
    """Return (score_0_100, tip_text) for live user guidance."""
    score = 0.0

    if nail_detected:
        # Give most credit once confidence reaches practical working range.
        score += 60.0 * min(1.0, max(0.0, nail_conf / 0.45))

    if focus_ok:
        score += 40.0

    score_int = int(round(max(0.0, min(100.0, score))))

    if not nail_detected:
        tip = "Place index fingernail in the center and keep skin below nail visible."
    elif nail_conf < SESSION_NAIL_CONF_THRESHOLD:
        tip = "Move slightly closer and keep fingertip centered."
    elif not focus_ok:
        tip = "Hold still and move your finger to a sharper distance; avoid glare."
    else:
        tip = "Great position. Keep still until analysis completes."

    return score_int, tip

def start_capture_session():
    global session_phase, phase_started_at, session_samples
    global current_hb, current_status, session_quality_ok, session_quality_reason
    global session_quality_score, session_quality_tip
    global inference_reset_token
    with session_lock:
        session_phase          = "positioning"
        phase_started_at       = time.time()
        session_samples        = []
        current_hb             = 0.0
        current_status         = "Preparing"
        session_quality_ok     = False
        session_quality_reason = "Place nail in view"
        session_quality_score  = 0
        session_quality_tip    = "Place index fingernail in the center guide area."
        # Signal worker to hard-reset all inference buffers for this new session.
        inference_reset_token += 1

def stop_capture_session():
    global session_phase, phase_started_at, session_samples
    global current_hb, current_status, session_quality_ok, session_quality_reason
    global session_quality_score, session_quality_tip
    with session_lock:
        session_phase          = "idle"
        phase_started_at       = 0.0
        session_samples        = []
        current_hb             = 0.0
        current_status         = "Ready"
        session_quality_ok     = False
        session_quality_reason = "Press Start"
        session_quality_score  = 0
        session_quality_tip    = "Press Start to begin camera preview."

def get_session_snapshot():
    with session_lock:
        phase        = session_phase
        phase_start  = phase_started_at
        quality_ok   = session_quality_ok
        quality_reason = session_quality_reason
        quality_score = int(session_quality_score)
        quality_tip = session_quality_tip
        sample_count = len(session_samples)
        hb_value     = float(current_hb)
        status_value = current_status
    now = time.time()
    if phase == "positioning":
        remaining = max(0.0, PREP_DURATION - (now - phase_start))
    elif phase == "acquiring":
        remaining = max(0.0, ACQUIRE_DURATION - (now - phase_start))
    else:
        remaining = 0.0
    return {
        'phase':        phase,
        'remaining':    round(remaining, 1),
        'quality_ok':   quality_ok,
        'quality_reason': quality_reason,
        'quality_score': quality_score,
        'quality_tip': quality_tip,
        'samples':      sample_count,
        'hb':           round(hb_value, 1),
        'status':       status_value,
    }

def calibrate_hb(hb_raw):
    hb_raw = float(hb_raw)
    if hb_raw <= HB_CALIBRATION_PIVOT:
        hb_calibrated = (HB_LOW_SLOPE * hb_raw) + HB_LOW_INTERCEPT
    else:
        hb_calibrated = (HB_HIGH_SLOPE * hb_raw) + HB_HIGH_INTERCEPT
    if NEAR_NORMAL_LIFT_START <= hb_calibrated < NEAR_NORMAL_LIFT_END:
        span     = NEAR_NORMAL_LIFT_END - NEAR_NORMAL_LIFT_START
        progress = (hb_calibrated - NEAR_NORMAL_LIFT_START) / span
        hb_calibrated += progress * NEAR_NORMAL_LIFT_MAX
    if hb_calibrated >= NORMAL_LIFT_START:
        lift = NORMAL_LIFT_BASE + (NORMAL_LIFT_GAIN * (hb_calibrated - NORMAL_LIFT_START))
        hb_calibrated += min(lift, NORMAL_LIFT_MAX)
    return float(np.clip(hb_calibrated, HB_MIN, HB_MAX))

def normalize_raw_hb_scale(hb_raw):
    """Convert model output from g/L to g/dL.  Always divides by 10.

    The model is trained in g/L space (80-180).  The old conditional
    (only divide when > 40) created a 13 g/dL cliff at the boundary.
    """
    return float(hb_raw) / RAW_HB_G_PER_L_DIVISOR

def crop_from_box(img, box):
    y1, x1, y2, x2 = box
    h, w, _ = img.shape
    y1, y2 = max(0, y1), min(h, y2)
    x1, x2 = max(0, x1), min(w, x2)
    if y2 <= y1 or x2 <= x1:
        if h < 224 or w < 224:
            return cv2.resize(img, (224, 224))
        cx, cy = w // 2, h // 2
        return img[cy-112:cy+112, cx-112:cx+112]
    return img[y1:y2, x1:x2]

def preprocess_nail_image(nail_crop):
    img  = cv2.resize(nail_crop, (224, 224))
    img  = cv2.cvtColor(img, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
    std  = np.array([0.229, 0.224, 0.225], dtype=np.float32)
    img  = (img - mean) / std
    img  = np.transpose(img, (2, 0, 1))[None, ...].astype(np.float32)
    return img

# FIX 4 – wider skin patch reduces sensitivity to box jitter
def derive_skin_crop(img, nail_box, skin_size=96):   # was 64, was 40
    y1, x1, y2, x2 = nail_box
    h, w, _ = img.shape
    cx  = (x1 + x2) // 2
    cy  = y2 + (skin_size // 2) + 4
    sx1 = max(0, cx - skin_size // 2)
    sy1 = max(0, cy - skin_size // 2)
    sx2 = min(w, sx1 + skin_size)
    sy2 = min(h, sy1 + skin_size)
    if sx2 - sx1 < skin_size or sy2 - sy1 < skin_size:
        return None
    return img[sy1:sy2, sx1:sx2]

def derive_center_skin_fallback(img, skin_size=64):
    h, w, _ = img.shape
    size = min(skin_size, h, w)
    if size <= 0:
        return None
    cx, cy = w // 2, h // 2
    sx1 = max(0, cx - size // 2)
    sy1 = max(0, cy - size // 2)
    sx2 = min(w, sx1 + size)
    sy2 = min(h, sy1 + size)
    patch = img[sy1:sy2, sx1:sx2]
    if patch is None or patch.size == 0:
        return None
    if patch.shape[0] != skin_size or patch.shape[1] != skin_size:
        patch = cv2.resize(patch, (skin_size, skin_size))
    return patch

def compute_nail_skin_ratio(nail_patch, skin_patch):
    nail_lab  = cv2.cvtColor(nail_patch, cv2.COLOR_BGR2Lab)
    skin_lab  = cv2.cvtColor(skin_patch, cv2.COLOR_BGR2Lab)
    nail_mean = nail_lab.reshape(-1, 3).mean(axis=0)
    skin_mean = skin_lab.reshape(-1, 3).mean(axis=0)
    return np.concatenate([nail_mean, skin_mean]).astype(np.float32)

def process_image(img, forced_box=None, allow_fallback=True, return_debug=False):
    raw_nails = []
    debug_info = {
        "forced_box": forced_box is not None,
        "allow_fallback": bool(allow_fallback),
        "nail_detection_count": 0,
        "best_nail_conf": 0.0,
        "crop_source": "unknown",
        "skin_source": "unknown",
    }
    if forced_box is None:
        results = yolo_model(img, imgsz=YOLO_INFER_SIZE, verbose=False)
        for r in results:
            for box in r.boxes:
                if int(box.cls[0]) == 0:
                    x1, y1, x2, y2 = map(int, box.xyxy[0])
                    conf = float(box.conf[0])
                    raw_nails.append({'box': [y1, x1, y2, x2], 'conf': conf})

    debug_info["nail_detection_count"] = len(raw_nails)
    if raw_nails:
        debug_info["best_nail_conf"] = float(max(nail["conf"] for nail in raw_nails))

    if forced_box is not None:
        crop = crop_from_box(img, forced_box)
        debug_info["crop_source"] = "forced_box"
    elif not raw_nails:
        if not allow_fallback:
            if return_debug:
                debug_info["failure_reason"] = "No Nail Detected"
                return None, "No Nail Detected", debug_info
            return None, "No Nail Detected"
        h, w, _ = img.shape
        if h < 224 or w < 224:
            crop = cv2.resize(img, (224, 224))
        else:
            cx, cy = w // 2, h // 2
            crop = img[cy-112:cy+112, cx-112:cx+112]
        debug_info["crop_source"] = "center_fallback"
    else:
        best_nail = sorted(raw_nails, key=lambda x: x['conf'], reverse=True)[0]
        crop = crop_from_box(img, best_nail['box'])
        debug_info["crop_source"] = "detected_nail"

    if forced_box is not None:
        nail_box = forced_box
    elif raw_nails:
        nail_box = sorted(raw_nails, key=lambda x: x['conf'], reverse=True)[0]['box']
    else:
        h, w, _ = img.shape
        if h < 224 or w < 224:
            nail_box = [0, 0, h, w]
        else:
            cx, cy = w // 2, h // 2
            nail_box = [cy - 112, cx - 112, cy + 112, cx + 112]

    skin_crop = derive_skin_crop(img, nail_box)
    debug_info["skin_source"] = "detected_box"
    if skin_crop is None and allow_fallback:
        skin_crop = derive_center_skin_fallback(img)
        if skin_crop is not None:
            debug_info["skin_source"] = "center_fallback"
    if skin_crop is None and allow_fallback:
        skin_crop = cv2.resize(crop, (64, 64))
        debug_info["skin_source"] = "nail_fallback"
    if skin_crop is None:
        if return_debug:
            debug_info["failure_reason"] = "No Skin Patch"
            return None, "No Skin Patch", debug_info
        return None, "No Skin Patch"

    # ── Compute skin features once (stable across TTA shifts) ─────────
    skin_features = compute_nail_skin_ratio(crop, skin_crop)
    if HB_USE_SKIN_FEATURE_NORM:
        skin_features = (skin_features - HB_SKIN_FEATURE_MEAN) / HB_SKIN_FEATURE_STD
    skin_input = skin_features[None, :].astype(np.float32)

    # ── TTA: run model on 5 shifted crops and take the median ─────────
    tta_shifts = [(0, 0), (5, 0), (-5, 0), (0, 5), (0, -5)]
    tta_predictions = []
    for dx, dy in tta_shifts:
        shifted_box = [
            nail_box[0] + dy, nail_box[1] + dx,
            nail_box[2] + dy, nail_box[3] + dx,
        ]
        shifted_crop = crop_from_box(img, shifted_box)
        shifted_input = preprocess_nail_image(shifted_crop)
        pred = hb_session.run(
            [HB_OUTPUT_NAME],
            {HB_IMAGE_INPUT_NAME: shifted_input, HB_SKIN_INPUT_NAME: skin_input},
        )[0]
        tta_predictions.append(float(pred.squeeze()))
    hb_raw_model = float(np.median(tta_predictions))

    hb_after_cfg_cal_g_per_l = hb_raw_model
    if HB_USE_CONFIG_CALIBRATION:
        hb_after_cfg_cal_g_per_l = (
            HB_CONFIG_CAL_SLOPE * hb_after_cfg_cal_g_per_l + HB_CONFIG_CAL_INTERCEPT
        )

    hb_raw = normalize_raw_hb_scale(hb_after_cfg_cal_g_per_l) + BIAS_OFFSET
    # Slope correction: stretch predictions outward from the decision boundary.
    # Makes anemic readings more anemic and normal readings more normal.
    if HB_SLOPE_STRETCH != 1.0:
        hb_raw = HB_SLOPE_PIVOT + HB_SLOPE_STRETCH * (hb_raw - HB_SLOPE_PIVOT)
    if HB_USE_LEGACY_CALIBRATION_CURVE:
        hb = calibrate_hb(hb_raw)
    else:
        hb = float(np.clip(hb_raw, HB_MIN, HB_MAX))

    debug_info["hb_raw_model"] = float(hb_raw_model)
    debug_info["hb_after_cfg_cal_g_per_l"] = float(hb_after_cfg_cal_g_per_l)
    debug_info["hb_after_scale_and_bias"] = float(hb_raw)
    debug_info["hb_calibrated"] = float(hb)
    debug_info["model_file"] = os.path.basename(hb_model_path)

    status = hb_to_status(hb)
    if return_debug:
        debug_info["status"] = status
        return hb, status, debug_info
    return hb, status

# ── Routes ────────────────────────────────────────────────────────────────────
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/admin/exit', methods=['POST'])
def admin_exit():
    body = request.get_json(silent=True) or {}
    if body.get('password') != ADMIN_EXIT_PASSWORD:
        return jsonify({'ok': False, 'error': 'Invalid password.'}), 403
    schedule_desktop_exit()
    return jsonify({'ok': True, 'message': 'Exit sequence started.'})

# ── Inference state (shared between background worker and frame generator) ─────
@dataclass
class InferenceState:
    """Thread-safe shared state between inference worker and frame generator."""
    lock: threading.Lock = field(default_factory=threading.Lock)
    cached_boxes: list = field(default_factory=list)
    smoothed_nail_box: list = field(default=None)
    best_live_nail_conf: float = 0.0
    top_live_nail_boxes: list = field(default_factory=list)
    focus_ok: bool = False
    focus_scores: deque = field(default_factory=lambda: deque(maxlen=6))
    quality_ok: bool = False
    quality_reason: str = "Place nail in view"
    quality_score: int = 0
    quality_tip: str = "Place index fingernail in the center guide area."
    smoothed_hb: float = None
    hb_history: deque = field(default_factory=lambda: deque(maxlen=HB_HISTORY_SIZE))
    af_locked: bool = False
    lock_candidate_count: int = 0
    blur_count: int = 0


def _reset_inference_state(inf_state):
    """Clear worker-local state so runs are independent between sessions."""
    with inf_state.lock:
        inf_state.cached_boxes = []
        inf_state.smoothed_nail_box = None
        inf_state.best_live_nail_conf = 0.0
        inf_state.top_live_nail_boxes = []
        inf_state.focus_ok = False
        inf_state.focus_scores.clear()
        inf_state.quality_ok = False
        inf_state.quality_reason = "Place nail in view"
        inf_state.quality_score = 0
        inf_state.quality_tip = "Place index fingernail in the center guide area."
        inf_state.smoothed_hb = None
        inf_state.hb_history.clear()
        inf_state.af_locked = False
        inf_state.lock_candidate_count = 0
        inf_state.blur_count = 0


# ── Background inference worker ───────────────────────────────────────────────
def _inference_worker(inf_state, stop_event):
    """Background thread: runs YOLO detection, focus eval, and ONNX Hb inference.

    This keeps heavy computation off the frame generator so the MJPEG stream
    stays responsive even on RPi 5.
    """
    global current_hb, current_status, camera_active
    global session_phase, phase_started_at, session_samples
    global session_quality_ok, session_quality_reason
    global session_quality_score, session_quality_tip
    global inference_reset_token

    last_yolo_time = 0
    last_af_eval_time = 0
    last_scan_time = 0
    last_seen_reset_token = -1

    while not stop_event.is_set():
        # Skip if camera is not active
        if not camera_active or picam2 is None:
            time.sleep(0.1)
            continue

        # Check session phase and reset token.
        with session_lock:
            phase = session_phase
            reset_token = inference_reset_token

        if reset_token != last_seen_reset_token:
            _reset_inference_state(inf_state)
            last_seen_reset_token = reset_token
            last_yolo_time = 0
            last_af_eval_time = 0
            last_scan_time = 0
            print(f"inference state reset (token={reset_token})")

        if phase == "idle":
            time.sleep(0.1)
            continue

        # Capture a frame for inference (separate from display capture)
        model_frame = _capture_camera_frame()
        if model_frame is None or model_frame.size == 0:
            time.sleep(0.05)
            continue

        # Normalize to 3-channel BGR
        if len(model_frame.shape) == 2:
            model_frame = cv2.cvtColor(model_frame, cv2.COLOR_GRAY2BGR)
        elif model_frame.shape[2] == 4:
            model_frame = cv2.cvtColor(model_frame, cv2.COLOR_BGRA2BGR)

        current_time = time.time()

        # ── Focus evaluation (every FOCUS_EVAL_INTERVAL) ──────────────────
        if current_time - last_af_eval_time >= FOCUS_EVAL_INTERVAL:
            gray = cv2.cvtColor(model_frame, cv2.COLOR_BGR2GRAY)
            h, w = gray.shape

            with inf_state.lock:
                roi_box = list(inf_state.smoothed_nail_box) if inf_state.smoothed_nail_box else None

            if roi_box is not None:
                by1, bx1, by2, bx2 = roi_box
                pad_y = int((by2 - by1) * 0.35)
                pad_x = int((bx2 - bx1) * 0.35)
                y1 = max(0, by1 - pad_y)
                y2 = min(h, by2 + pad_y)
                x1 = max(0, bx1 - pad_x)
                x2 = min(w, bx2 + pad_x)
                if y2 <= y1 or x2 <= x1:
                    y1, y2 = int(h * 0.25), int(h * 0.75)
                    x1, x2 = int(w * 0.25), int(w * 0.75)
            else:
                y1, y2 = int(h * 0.25), int(h * 0.75)
                x1, x2 = int(w * 0.25), int(w * 0.75)

            roi = gray[y1:y2, x1:x2]
            sharpness = cv2.Laplacian(roi, cv2.CV_64F).var()

            with inf_state.lock:
                inf_state.focus_scores.append(float(sharpness))

                if len(inf_state.focus_scores) >= 3:
                    sharp_med = float(np.median(inf_state.focus_scores))
                    sharp_std = float(np.std(inf_state.focus_scores))
                    if AUTOFOCUS_STABLE_MODE:
                        inf_state.focus_ok = sharp_med >= FOCUS_UNLOCK_MIN_SHARPNESS
                    else:
                        if not inf_state.af_locked:
                            if sharp_med >= FOCUS_LOCK_MIN_SHARPNESS and sharp_std <= FOCUS_STABLE_STD:
                                inf_state.lock_candidate_count += 1
                            else:
                                inf_state.lock_candidate_count = 0
                            if inf_state.lock_candidate_count >= FOCUS_STABLE_COUNT:
                                inf_state.af_locked = True
                                inf_state.blur_count = 0
                                print("focus locked")
                                inf_state.lock_candidate_count = 0
                        else:
                            if sharp_med < FOCUS_UNLOCK_MIN_SHARPNESS:
                                inf_state.blur_count += 1
                            else:
                                inf_state.blur_count = 0
                            if inf_state.blur_count >= FOCUS_BLUR_COUNT:
                                inf_state.af_locked = False
                                inf_state.lock_candidate_count = 0
                                print("focus unlocked")
                                inf_state.blur_count = 0
                        inf_state.focus_ok = (inf_state.af_locked or
                                              sharp_med >= FOCUS_LOCK_MIN_SHARPNESS)

            last_af_eval_time = current_time

        # ── YOLO detection (every YOLO_LIVE_INTERVAL) ─────────────────────
        if current_time - last_yolo_time > YOLO_LIVE_INTERVAL:
            results = yolo_model(model_frame, imgsz=LIVE_YOLO_INFER_SIZE, verbose=False)
            new_cached_boxes = []
            new_best_box = None
            new_best_conf = 0.0

            for r in results:
                for box in r.boxes:
                    x1, y1, x2, y2 = map(int, box.xyxy[0])
                    cls_id = int(box.cls[0])
                    conf = float(box.conf[0])
                    new_cached_boxes.append((x1, y1, x2, y2, cls_id, conf))
                    if cls_id == 0 and conf > new_best_conf:
                        new_best_conf = conf
                        new_best_box = [y1, x1, y2, x2]

            nail_boxes_with_conf = [
                ([y1, x1, y2, x2], conf)
                for x1, y1, x2, y2, cls_id, conf in new_cached_boxes
                if cls_id == 0
            ]
            nail_boxes_with_conf.sort(key=lambda item: item[1], reverse=True)
            new_top_boxes = [box for box, _conf in nail_boxes_with_conf[:LIVE_MULTI_NAIL_TOP_K]]

            with inf_state.lock:
                inf_state.cached_boxes = new_cached_boxes
                inf_state.best_live_nail_conf = new_best_conf
                inf_state.top_live_nail_boxes = new_top_boxes

                # FIX 1 – update smoothed box with EMA
                if new_best_box is not None:
                    if inf_state.smoothed_nail_box is None:
                        inf_state.smoothed_nail_box = list(new_best_box)
                    else:
                        inf_state.smoothed_nail_box = [
                            int(inf_state.smoothed_nail_box[i] * (1.0 - BOX_SMOOTH_ALPHA) +
                                new_best_box[i] * BOX_SMOOTH_ALPHA)
                            for i in range(4)
                        ]

            # ── Update quality feedback ───────────────────────────────────
            with inf_state.lock:
                focus_ok_now = inf_state.focus_ok

            nail_ok = (new_best_box is not None and
                       new_best_conf >= SESSION_NAIL_CONF_THRESHOLD)
            quality_ok = nail_ok and focus_ok_now

            if new_best_box is None:
                quality_reason = "Nail not detected"
            elif new_best_conf < SESSION_NAIL_CONF_THRESHOLD:
                quality_reason = "Move closer to nail"
            elif not focus_ok_now:
                quality_reason = "Hold still for focus"
            else:
                quality_reason = "Quality OK"

            q_score, q_tip = build_quality_feedback(
                nail_detected=(new_best_box is not None),
                nail_conf=float(new_best_conf),
                focus_ok=bool(focus_ok_now),
            )

            with inf_state.lock:
                inf_state.quality_ok = quality_ok
                inf_state.quality_reason = quality_reason
                inf_state.quality_score = q_score
                inf_state.quality_tip = q_tip

            with session_lock:
                session_quality_ok = quality_ok
                session_quality_reason = quality_reason
                session_quality_score = q_score
                session_quality_tip = q_tip

            last_yolo_time = current_time

        # ── Phase transition: positioning → acquiring ─────────────────────
        with session_lock:
            phase = session_phase
            phase_start = phase_started_at

        with inf_state.lock:
            q_ok = inf_state.quality_ok

        if phase == "positioning":
            elapsed = time.time() - phase_start
            if elapsed >= PREP_DURATION and q_ok:
                # FIX 2 – reset smoothed_hb and hb_history for clean acquisition
                with inf_state.lock:
                    inf_state.smoothed_hb = None
                    inf_state.hb_history.clear()
                with session_lock:
                    session_phase = "acquiring"
                    phase_started_at = time.time()
                    session_samples = []
                    phase = session_phase
                    phase_start = phase_started_at

        # ── Hb inference (during acquiring phase, every SCAN_INTERVAL) ────
        if current_time - last_scan_time > SCAN_INTERVAL:
            with session_lock:
                phase = session_phase
                phase_start = phase_started_at

            with inf_state.lock:
                s_nail_box = list(inf_state.smoothed_nail_box) if inf_state.smoothed_nail_box else None
                top_boxes = list(inf_state.top_live_nail_boxes)
                nail_conf = inf_state.best_live_nail_conf

            # FIX 5 – use higher confidence threshold for actual Hb samples
            if (phase == "acquiring" and
                    (s_nail_box is not None or len(top_boxes) > 0) and
                    nail_conf >= ACQUIRE_NAIL_CONF_THRESHOLD):

                inference_boxes = []
                if s_nail_box is not None:
                    inference_boxes.append(s_nail_box)
                for candidate_box in top_boxes:
                    if len(inference_boxes) >= LIVE_MULTI_NAIL_TOP_K:
                        break
                    if candidate_box not in inference_boxes:
                        inference_boxes.append(candidate_box)

                hb_candidates = []
                for inference_box in inference_boxes:
                    # FIX 6 – reject skin-crop fallback samples
                    hb_candidate, _status, debug = process_image(
                        model_frame,
                        forced_box=inference_box,
                        allow_fallback=False,
                        return_debug=True,
                    )
                    if hb_candidate is not None:
                        skin_src = debug.get("skin_source", "unknown")
                        if skin_src == "detected_box":
                            hb_candidates.append(float(hb_candidate))

                hb = float(np.median(hb_candidates)) if hb_candidates else None
                if hb is not None:
                    with inf_state.lock:
                        inf_state.hb_history.append(float(hb))
                        hb_median = float(np.median(inf_state.hb_history))
                        if inf_state.smoothed_hb is None:
                            inf_state.smoothed_hb = hb_median
                        else:
                            delta = hb_median - inf_state.smoothed_hb
                            if delta >= 0:
                                delta = float(min(delta, MAX_HB_STEP_UP))
                            else:
                                delta = -float(min(abs(delta), MAX_HB_STEP_DOWN))
                            candidate = inf_state.smoothed_hb + (SMOOTHING_ALPHA * delta)
                            if abs(candidate - inf_state.smoothed_hb) >= HB_DEADBAND:
                                inf_state.smoothed_hb = candidate

                    # Store the RAW per-frame prediction for session aggregation.
                    # The EMA (smoothed_hb) is too heavily damped to vary across
                    # samples — its deadband blocks almost all updates, making
                    # every session_sample identical.  Using the raw frame value
                    # lets summarize_session_samples() do proper IQR filtering
                    # and median computation on genuinely varied data.
                    with session_lock:
                        session_samples.append(float(hb))

            # ── Check if acquisition is complete ──────────────────────────
            if phase == "acquiring" and (current_time - phase_start) >= ACQUIRE_DURATION:
                with session_lock:
                    if len(session_samples) >= MIN_VALID_SAMPLES:
                        summary = summarize_session_samples(session_samples)
                        if summary is not None and summary["sample_count"] >= MIN_VALID_SAMPLES:
                            final_hb = float(summary["median"])

                            if ANEMIA_GUARD_ENABLED:
                                if (
                                    final_hb >= ANEMIA_GUARD_THRESHOLD
                                    and float(summary["lower_hb"]) < ANEMIA_GUARD_THRESHOLD
                                    and float(summary["anemia_ratio"]) >= ANEMIA_GUARD_RATIO
                                ):
                                    # Soft blend instead of hard min to avoid
                                    # discontinuous jumps between sessions.
                                    # 70/30 keeps median dominant while still
                                    # nudging conservatively for screening safety.
                                    final_hb = 0.7 * final_hb + 0.3 * float(summary["lower_hb"])

                            current_hb = float(final_hb)

                            unstable_session = bool(
                                MAX_FINAL_IQR > 0 and summary["iqr"] > MAX_FINAL_IQR
                            )
                            current_status = classify_session_result(
                                hb_value=current_hb,
                                anemia_ratio=float(summary["anemia_ratio"]),
                                lower_hb=float(summary["lower_hb"]),
                            )

                            print(
                                "session finalize | "
                                f"hb={current_hb:.2f} | iqr={summary['iqr']:.2f} | "
                                f"low_q={summary['lower_hb']:.2f} | "
                                f"anemia_ratio={summary['anemia_ratio']:.2f} | "
                                f"samples={summary['sample_count']} | unstable={unstable_session} | status={current_status}"
                            )
                        else:
                            current_hb = 0.0
                            current_status = "No Valid Reading"
                    else:
                        current_hb = 0.0
                        current_status = "No Valid Reading"
                    session_phase = "done"

            last_scan_time = current_time

        # Throttle to avoid busy-waiting
        time.sleep(0.02)


# ── Frame generator (lightweight — display only) ─────────────────────────────
def generate_frames():
    """Yield MJPEG frames for the /video_feed endpoint.

    All heavy computation (YOLO, ONNX, focus eval) runs in _inference_worker.
    This generator only: captures a frame, draws cached overlays, encodes JPEG.
    """
    global camera_active, picam2

    time.sleep(1.0)

    # Create shared inference state and start the background worker
    inf_state = InferenceState()
    stop_event = threading.Event()
    worker = threading.Thread(
        target=_inference_worker,
        args=(inf_state, stop_event),
        daemon=True,
        name="inference_worker",
    )
    worker.start()

    last_camera_retry = 0
    prev_display_frame = None

    def draw_overlay_text(frame, text, org, color, scale=0.62, thickness=2):
        # Double-pass text improves readability over bright skin/background regions.
        cv2.putText(frame, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, (10, 16, 28), thickness + 2, cv2.LINE_AA)
        cv2.putText(frame, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, color, thickness, cv2.LINE_AA)

    try:
        while True:
            current_time = time.time()

            if not camera_active or picam2 is None:
                if current_time - last_camera_retry >= 2.0:
                    print("camera inactive, attempting restart...")
                    start_camera()
                    last_camera_retry = current_time
                time.sleep(0.1)
                continue

            try:
                display_frame = _capture_camera_frame()
                if display_frame is None or display_frame.size == 0:
                    time.sleep(0.05)
                    continue

                # Normalize to 3-channel BGR
                if len(display_frame.shape) == 2:
                    display_frame = cv2.cvtColor(display_frame, cv2.COLOR_GRAY2BGR)
                elif display_frame.shape[2] == 4:
                    display_frame = cv2.cvtColor(display_frame, cv2.COLOR_BGRA2BGR)

                # Optional display smoothing (currently disabled)
                if DISPLAY_SMOOTH_ENABLED:
                    display_frame = cv2.bilateralFilter(
                        display_frame,
                        DISPLAY_BILATERAL_D,
                        DISPLAY_BILATERAL_SIGMA_COLOR,
                        DISPLAY_BILATERAL_SIGMA_SPACE,
                    )
                    if (prev_display_frame is not None and
                            prev_display_frame.shape == display_frame.shape):
                        display_frame = cv2.addWeighted(
                            prev_display_frame, DISPLAY_TEMPORAL_ALPHA,
                            display_frame, 1.0 - DISPLAY_TEMPORAL_ALPHA, 0,
                        )
                    prev_display_frame = display_frame.copy()

                # ── Read cached state from inference worker ───────────────
                with inf_state.lock:
                    boxes = list(inf_state.cached_boxes)
                    quality_ok = inf_state.quality_ok
                    quality_reason = inf_state.quality_reason

                # ── Draw detection boxes ──────────────────────────────────
                for x1, y1, x2, y2, cls_id, conf in boxes:
                    color = (0, 255, 0) if cls_id == 0 else (0, 165, 255)
                    label = f"{'nail' if cls_id == 0 else 'skin'} {conf:.2f}"
                    cv2.rectangle(display_frame, (x1, y1), (x2, y2), color, 2)
                    draw_overlay_text(
                        display_frame,
                        label,
                        (x1, max(y1 - 6, 12)),
                        (240, 248, 255),
                        scale=0.47,
                        thickness=1,
                    )

                # ── Phase label ───────────────────────────────────────────
                with session_lock:
                    phase = session_phase
                    phase_start = phase_started_at

                now = time.time()
                if phase == "positioning":
                    remaining = max(0.0, PREP_DURATION - (now - phase_start))
                    phase_label = f"POSITIONING: {remaining:.1f}s"
                elif phase == "acquiring":
                    remaining = max(0.0, ACQUIRE_DURATION - (now - phase_start))
                    phase_label = f"ANALYZING: {remaining:.1f}s"
                else:
                    phase_label = "IDLE"

                frame_h, frame_w = display_frame.shape[:2]
                panel_left, panel_top = 8, 8
                panel_right = min(frame_w - 8, 420)
                panel_bottom = min(frame_h - 8, 132)
                if panel_right > panel_left + 12 and panel_bottom > panel_top + 12:
                    panel_overlay = display_frame.copy()
                    cv2.rectangle(panel_overlay, (panel_left, panel_top), (panel_right, panel_bottom), (13, 34, 64), -1)
                    cv2.rectangle(panel_overlay, (panel_left, panel_top), (panel_right, panel_bottom), (80, 130, 185), 1)
                    cv2.addWeighted(panel_overlay, 0.32, display_frame, 0.68, 0, display_frame)

                draw_overlay_text(display_frame, f"Calib: +{BIAS_OFFSET}", (14, 34), (150, 235, 255), scale=0.62, thickness=2)
                draw_overlay_text(display_frame, phase_label, (14, 66), (236, 244, 255), scale=0.62, thickness=2)
                draw_overlay_text(
                    display_frame,
                    quality_reason,
                    (14, 98),
                    (178, 255, 204) if quality_ok else (255, 208, 178),
                    scale=0.58,
                    thickness=2,
                )

                # ── Encode and yield ──────────────────────────────────────
                ret, buffer = cv2.imencode('.jpg', display_frame)
                if not ret:
                    continue
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')

            except Exception as e:
                print(f"error grabbing frame: {e}")
                _stop_camera_device()
                camera_active = False
                with inf_state.lock:
                    inf_state.smoothed_nail_box = None
                    inf_state.focus_scores.clear()
                prev_display_frame = None
                time.sleep(0.1)
                continue
    finally:
        stop_event.set()
        worker.join(timeout=2.0)

# ── Flask routes ──────────────────────────────────────────────────────────────
@app.route('/video_feed')
def video_feed():
    return Response(generate_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/get_result')
def get_result():
    return jsonify({'hb': round(current_hb, 1), 'status': current_status})

@app.route('/start_session', methods=['POST'])
def start_session():
    start_capture_session()
    return jsonify({'ok': True})

@app.route('/stop_session', methods=['POST'])
def stop_session():
    stop_capture_session()
    return jsonify({'ok': True})

@app.route('/get_session_state')
def get_session_state():
    return jsonify(get_session_snapshot())

@app.route('/save_measurement', methods=['POST'])
def save_measurement():
    try:
        name              = (request.form.get('name') or '').strip()
        predicted_hb_raw  = (request.form.get('predicted_hb') or '').strip()
        predicted_status  = (request.form.get('predicted_status') or '').strip()

        if not predicted_hb_raw:
            return jsonify({'ok': False, 'error': 'Predicted hemoglobin value is required.'}), 400
        try:
            predicted_hb = float(predicted_hb_raw)
        except ValueError:
            return jsonify({'ok': False, 'error': 'Predicted hemoglobin value is invalid.'}), 400
        if predicted_hb < 3.0 or predicted_hb > 25.0:
            return jsonify({'ok': False, 'error': 'Predicted hemoglobin value is out of range.'}), 400
        if not predicted_status:
            return jsonify({'ok': False, 'error': 'Prediction status is required.'}), 400
        if len(name) > 120:
            return jsonify({'ok': False, 'error': 'Name is too long.'}), 400

        now_utc = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
        conn = sqlite3.connect(DB_PATH)
        try:
            cursor = conn.execute(
                """
                INSERT INTO readings (
                    created_at, measurement_time, predicted_hb,
                    predicted_status, patient_name_enc
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    now_utc,
                    now_utc,
                    round(predicted_hb, 2),
                    predicted_status,
                    encrypt_text(name) if name else None,
                )
            )
            conn.commit()
            reading_id = cursor.lastrowid
        finally:
            conn.close()
        return jsonify({'ok': True, 'message': 'Reading saved successfully.', 'reading_id': reading_id})
    except Exception as exc:
        print(f"save_measurement error: {exc}")
        return jsonify({'ok': False, 'error': 'Failed to save reading.'}), 500

@app.route('/admin/export_records', methods=['POST'])
def admin_export_records():
    body = request.get_json(silent=True) or {}
    if body.get('password') != ADMIN_EXIT_PASSWORD:
        return jsonify({'ok': False, 'error': 'Invalid password.'}), 403
    try:
        ensure_data_dir()
        os.makedirs(RECORDS_EXPORT_DIR, exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                SELECT id, created_at, measurement_time, predicted_hb, predicted_status, patient_name_enc
                FROM readings ORDER BY id ASC
                """
            ).fetchall()
        finally:
            conn.close()

        export_timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
        filename    = f"records_{export_timestamp}.csv"
        output_path = os.path.join(RECORDS_EXPORT_DIR, filename)
        with open(output_path, mode='w', newline='', encoding='utf-8') as csv_file:
            writer = csv.writer(csv_file)
            writer.writerow(['id', 'created_at', 'measurement_time',
                             'predicted_hb', 'predicted_status', 'name'])
            for row in rows:
                try:
                    name_value = decrypt_text(row['patient_name_enc']) if row['patient_name_enc'] else ''
                except Exception:
                    name_value = '[UNREADABLE]'
                writer.writerow([
                    row['id'], row['created_at'], row['measurement_time'],
                    row['predicted_hb'], row['predicted_status'], name_value,
                ])
        return jsonify({
            'ok': True,
            'message': 'Records exported successfully.',
            'filename': filename,
            'record_count': len(rows),
            'folder': 'databaseRecords',
        })
    except Exception as exc:
        print(f"admin_export_records error: {exc}")
        return jsonify({'ok': False, 'error': 'Failed to export records.'}), 500

@app.route('/admin/reset_database', methods=['POST'])
def admin_reset_database():
    body = request.get_json(silent=True) or {}
    if body.get('password') != ADMIN_EXIT_PASSWORD:
        return jsonify({'ok': False, 'error': 'Invalid password.'}), 403
    try:
        conn = sqlite3.connect(DB_PATH)
        try:
            row_count = int(conn.execute("SELECT COUNT(*) FROM readings").fetchone()[0])
            conn.execute("DELETE FROM readings")
            conn.execute("DELETE FROM sqlite_sequence WHERE name = 'readings'")
            conn.commit()
        finally:
            conn.close()
        return jsonify({'ok': True, 'message': 'Database reset successful.', 'deleted_rows': row_count})
    except Exception as exc:
        print(f"admin_reset_database error: {exc}")
        return jsonify({'ok': False, 'error': 'Failed to reset database.'}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)

