import cv2
import numpy as np
import time
import os
import subprocess
from collections import deque
from flask import Flask, render_template, Response, jsonify, request
from tensorflow.keras.models import load_model
from xgboost import XGBRegressor
from picamera2 import Picamera2
from libcamera import controls
from ultralytics import YOLO
import threading

app = Flask(__name__)

# Camera settings
picam2 = None
camera_active = False

def start_camera():
    global picam2, camera_active
    try:
        if picam2 is not None:
            try:
                picam2.stop()
            except Exception:
                pass

        picam2 = Picamera2()
        config = picam2.create_preview_configuration(main={"size": (640, 480), "format": "BGR888"})
        picam2.configure(config)
        picam2.start()

        picam2.set_controls({
            "AfMode": controls.AfModeEnum.Continuous,
            "AfRange": controls.AfRangeEnum.Macro
        })

        camera_active = True
        print("autofocus enabled")
        return True
    except Exception as e:
        print(f"no camera detected error: {e}")
        camera_active = False
        picam2 = None
        return False

start_camera()

# Load models
print("load models")
cnn = load_model('anemia_cnn.h5')
xgb = XGBRegressor()
xgb.load_model('anemia_xgb.json')
yolo_model = YOLO('best.pt')
print("models loaded")

# Global states
current_hb = 0.0
last_scan_time = 0
current_status = "Ready"
BIAS_OFFSET = 0.1
SCAN_INTERVAL = 0.8
SMOOTHING_ALPHA = 0.55
MAX_HB_STEP = 0.6
HB_DEADBAND = 0.01
HB_HISTORY_SIZE = 3
LIVE_NAIL_CONF_THRESHOLD = 0.35
SESSION_NAIL_CONF_THRESHOLD = 0.45
HB_CALIBRATION_PIVOT = 11.5
HB_LOW_SLOPE = 1.05
HB_LOW_INTERCEPT = -0.6
HB_HIGH_SLOPE = 1.22
HB_HIGH_INTERCEPT = -2.555
NORMAL_LIFT_START = 12.0
NORMAL_LIFT_BASE = 0.55
NORMAL_LIFT_GAIN = 0.35
NORMAL_LIFT_MAX = 1.40
HB_MIN = 5.0
HB_MAX = 18.0
FOCUS_EVAL_INTERVAL = 0.25
FOCUS_LOCK_MIN_SHARPNESS = 45.0
FOCUS_UNLOCK_MIN_SHARPNESS = 28.0
FOCUS_STABLE_STD = 10.0
FOCUS_STABLE_COUNT = 4
FOCUS_BLUR_COUNT = 3
PREP_DURATION = 5.0
ACQUIRE_DURATION = 6.0
MIN_VALID_SAMPLES = 3

session_lock = threading.Lock()
session_phase = "idle"
phase_started_at = 0.0
session_quality_ok = False
session_quality_reason = "Press Start"
session_samples = []

ADMIN_EXIT_PASSWORD = "group5"


def schedule_desktop_exit():
    def _shutdown_worker():
        global picam2, camera_active

        time.sleep(0.9)
        stop_capture_session()

        try:
            if picam2 is not None:
                picam2.stop()
        except Exception:
            pass

        camera_active = False
        picam2 = None

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
    status = "Normal"
    if hb_value < 8.0:
        status = "Severe Anemia"
    elif hb_value < 10.0:
        status = "Moderate Anemia"
    elif hb_value < 12.0:
        status = "Mild Anemia"
    return status


def start_capture_session():
    global session_phase, phase_started_at, session_samples
    global current_hb, current_status, session_quality_ok, session_quality_reason

    with session_lock:
        session_phase = "positioning"
        phase_started_at = time.time()
        session_samples = []
        current_hb = 0.0
        current_status = "Preparing"
        session_quality_ok = False
        session_quality_reason = "Place nail in view"


def stop_capture_session():
    global session_phase, phase_started_at, session_samples
    global current_hb, current_status, session_quality_ok, session_quality_reason

    with session_lock:
        session_phase = "idle"
        phase_started_at = 0.0
        session_samples = []
        current_hb = 0.0
        current_status = "Ready"
        session_quality_ok = False
        session_quality_reason = "Press Start"


def get_session_snapshot():
    with session_lock:
        phase = session_phase
        phase_start = phase_started_at
        quality_ok = session_quality_ok
        quality_reason = session_quality_reason
        sample_count = len(session_samples)
        hb_value = float(current_hb)
        status_value = current_status

    now = time.time()
    if phase == "positioning":
        remaining = max(0.0, PREP_DURATION - (now - phase_start))
    elif phase == "acquiring":
        remaining = max(0.0, ACQUIRE_DURATION - (now - phase_start))
    else:
        remaining = 0.0

    return {
        'phase': phase,
        'remaining': round(remaining, 1),
        'quality_ok': quality_ok,
        'quality_reason': quality_reason,
        'samples': sample_count,
        'hb': round(hb_value, 1),
        'status': status_value
    }

def calibrate_hb(hb_raw):
    hb_raw = float(hb_raw)

    # Piecewise calibration avoids over-lifting low/mild Hb predictions.
    if hb_raw <= HB_CALIBRATION_PIVOT:
        hb_calibrated = (HB_LOW_SLOPE * hb_raw) + HB_LOW_INTERCEPT
    else:
        hb_calibrated = (HB_HIGH_SLOPE * hb_raw) + HB_HIGH_INTERCEPT

    # Lift values only in the normal range to reduce underestimation in non-anemic users.
    if hb_calibrated >= NORMAL_LIFT_START:
        lift = NORMAL_LIFT_BASE + (NORMAL_LIFT_GAIN * (hb_calibrated - NORMAL_LIFT_START))
        hb_calibrated += min(lift, NORMAL_LIFT_MAX)

    return float(np.clip(hb_calibrated, HB_MIN, HB_MAX))

def crop_from_box(img, box):
    y1, x1, y2, x2 = box
    h, w, _ = img.shape
    y1, y2 = max(0, y1), min(h, y2)
    x1, x2 = max(0, x1), min(w, x2)

    # If box is invalid, fallback to center crop.
    if y2 <= y1 or x2 <= x1:
        if h < 224 or w < 224:
            return cv2.resize(img, (224, 224))
        cx, cy = w // 2, h // 2
        return img[cy-112:cy+112, cx-112:cx+112]

    return img[y1:y2, x1:x2]

def process_image(img, forced_box=None, allow_fallback=True):
    results = yolo_model(img, verbose=False)
    raw_nails = []

    # Extract the nail boxes
    for r in results:
        for box in r.boxes:
            # Class 0 = nail, 1 = skin
            if int(box.cls[0]) == 0:
                x1, y1, x2, y2 = map(int, box.xyxy[0])
                conf = float(box.conf[0])
                raw_nails.append({'box': [y1, x1, y2, x2], 'conf': conf})
    if forced_box is not None:
        crop = crop_from_box(img, forced_box)
    # Use center crop only when explicitly allowed.
    elif not raw_nails:
        if not allow_fallback:
            return None, "No Nail Detected"
        h, w, _ = img.shape
        if h < 224 or w < 224:
            crop = cv2.resize(img, (224, 224))
        else:
            cx, cy = w // 2, h // 2
            crop = img[cy-112:cy+112, cx-112:cx+112]
    else:
    # Sort by confidence and grab the best nail detected
        best_nail = sorted(raw_nails, key=lambda x: x['conf'], reverse=True)[0]
        crop = crop_from_box(img, best_nail['box'])

    # CNN preprocessing
    img_array = cv2.resize(crop, (224, 224))
    img_array = img_array / 255.0
    img_array = np.expand_dims(img_array, axis=0)

    # Inference
    features = cnn.predict(img_array, verbose=0)
    prediction = xgb.predict(features)[0]
    hb_raw = float(prediction) + BIAS_OFFSET
    hb = calibrate_hb(hb_raw)

    # Classification
    status = "Normal"
    if hb < 8.0: status = "Severe Anemia"
    elif hb < 10.0: status = "Moderate Anemia"
    elif hb < 12.0: status = "Mild Anemia"

    return hb, status

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

def generate_frames():
    global current_hb, current_status, last_scan_time, camera_active, picam2
    global session_phase, phase_started_at, session_quality_ok, session_quality_reason, session_samples
    time.sleep(1.0) 

    # Box caching to speed up drawing per frame
    cached_boxes = []
    last_yolo_time = 0
    last_camera_retry = 0
    smoothed_hb = None
    hb_history = deque(maxlen=HB_HISTORY_SIZE)
    best_live_nail_box = None
    focus_scores = deque(maxlen=6)
    af_locked = False
    lock_candidate_count = 0
    blur_count = 0
    last_af_eval_time = 0
    last_lens_position = None

    # YOLO runtime 
    YOLO_INTERVAL = 0.2 

    while True:
        current_time = time.time()

        # If capture fails/disconnects, keep trying to reinitialize camera.
        if not camera_active or picam2 is None:
            if current_time - last_camera_retry >= 2.0:
                print("camera inactive, attempting restart...")
                start_camera()
                last_camera_retry = current_time
            time.sleep(0.1)
            continue

        try:
            frame = picam2.capture_array("main")
            if frame is None or frame.size == 0:
                print("empty frame received")
                time.sleep(0.05)
                continue

            frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)

            # Adaptive autofocus: lock focus when image is stably sharp, unlock if blur appears.
            if current_time - last_af_eval_time >= FOCUS_EVAL_INTERVAL:
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                h, w = gray.shape
                y1, y2 = int(h * 0.25), int(h * 0.75)
                x1, x2 = int(w * 0.25), int(w * 0.75)
                roi = gray[y1:y2, x1:x2]
                sharpness = cv2.Laplacian(roi, cv2.CV_64F).var()
                focus_scores.append(float(sharpness))

                try:
                    metadata = picam2.capture_metadata()
                    if metadata is not None and "LensPosition" in metadata:
                        last_lens_position = metadata["LensPosition"]
                except Exception:
                    pass

                if len(focus_scores) >= 3:
                    sharp_med = float(np.median(focus_scores))
                    sharp_std = float(np.std(focus_scores))

                    if not af_locked:
                        if sharp_med >= FOCUS_LOCK_MIN_SHARPNESS and sharp_std <= FOCUS_STABLE_STD:
                            lock_candidate_count += 1
                        else:
                            lock_candidate_count = 0

                        if lock_candidate_count >= FOCUS_STABLE_COUNT:
                            try:
                                if last_lens_position is not None:
                                    picam2.set_controls({
                                        "AfMode": controls.AfModeEnum.Manual,
                                        "LensPosition": float(last_lens_position)
                                    })
                                else:
                                    picam2.set_controls({"AfMode": controls.AfModeEnum.Manual})
                                af_locked = True
                                blur_count = 0
                                print("focus locked")
                            except Exception as focus_err:
                                print(f"focus lock failed: {focus_err}")
                            lock_candidate_count = 0
                    else:
                        if sharp_med < FOCUS_UNLOCK_MIN_SHARPNESS:
                            blur_count += 1
                        else:
                            blur_count = 0

                        if blur_count >= FOCUS_BLUR_COUNT:
                            try:
                                picam2.set_controls({"AfMode": controls.AfModeEnum.Continuous})
                                af_locked = False
                                lock_candidate_count = 0
                                print("focus unlocked")
                            except Exception as focus_err:
                                print(f"focus unlock failed: {focus_err}")
                            blur_count = 0

                last_af_eval_time = current_time

            # Run only if 0.2 seconds have passed
            if current_time - last_yolo_time > YOLO_INTERVAL:

                # force YOLO to process a smaller, faster image to prevent lag on video output
                results = yolo_model(frame, imgsz=320, verbose=False)

                # Clear old boxes and save the new ones
                cached_boxes = []
                best_live_nail_box = None
                best_live_nail_conf = 0.0
                for r in results:
                    for box in r.boxes:
                        x1, y1, x2, y2 = map(int, box.xyxy[0])
                        cls_id = int(box.cls[0])
                        conf = float(box.conf[0])
                        cached_boxes.append((x1, y1, x2, y2, cls_id, conf))

                        if cls_id == 0 and conf > best_live_nail_conf:
                            best_live_nail_conf = conf
                            best_live_nail_box = [y1, x1, y2, x2]

                last_yolo_time = current_time

            # Draw the cached boxes
            for x1, y1, x2, y2, cls_id, conf in cached_boxes:
                color = (0, 255, 0) if cls_id == 0 else (0, 165, 255)
                cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)

            cv2.putText(frame, f"Calib: +{BIAS_OFFSET}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0,255,255), 2)
            af_text = "AF: LOCKED" if af_locked else "AF: AUTO"
            cv2.putText(frame, af_text, (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255,255,0), 2)

            focus_ok = af_locked
            if not focus_ok and len(focus_scores) >= 3:
                focus_ok = float(np.median(focus_scores)) >= FOCUS_LOCK_MIN_SHARPNESS

            nail_ok = best_live_nail_box is not None and best_live_nail_conf >= SESSION_NAIL_CONF_THRESHOLD
            quality_ok = nail_ok and focus_ok
            if best_live_nail_box is None:
                quality_reason = "Nail not detected"
            elif best_live_nail_conf < SESSION_NAIL_CONF_THRESHOLD:
                quality_reason = "Move closer to nail"
            elif not focus_ok:
                quality_reason = "Hold still for focus"
            else:
                quality_reason = "Quality OK"

            with session_lock:
                session_quality_ok = quality_ok
                session_quality_reason = quality_reason
                phase = session_phase
                phase_start = phase_started_at

            phase_label = "IDLE"
            phase_remaining = 0.0
            now = time.time()

            if phase == "positioning":
                elapsed = now - phase_start
                phase_remaining = max(0.0, PREP_DURATION - elapsed)
                phase_label = f"POSITIONING: {phase_remaining:.1f}s"

                if elapsed >= PREP_DURATION and quality_ok:
                    with session_lock:
                        session_phase = "acquiring"
                        phase_started_at = now
                        session_samples = []
                        phase = session_phase
                        phase_start = phase_started_at

            if phase == "acquiring":
                elapsed = now - phase_start
                phase_remaining = max(0.0, ACQUIRE_DURATION - elapsed)
                phase_label = f"ANALYZING: {phase_remaining:.1f}s"

            cv2.putText(frame, phase_label, (10, 90), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
            cv2.putText(frame, quality_reason, (10, 120), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (180, 255, 180) if quality_ok else (180, 180, 255), 2)

            # CNN+XGBoost scan
            if current_time - last_scan_time > SCAN_INTERVAL:
                with session_lock:
                    phase = session_phase

                if phase == "acquiring" and best_live_nail_box is not None and best_live_nail_conf >= SESSION_NAIL_CONF_THRESHOLD:
                    hb, _ = process_image(frame, forced_box=best_live_nail_box, allow_fallback=False)
                    if hb is not None:
                        hb_history.append(float(hb))

                        # Median suppresses spikes from small hand/light perturbations.
                        hb_median = float(np.median(hb_history))
                        if smoothed_hb is None:
                            smoothed_hb = hb_median
                        else:
                            delta = hb_median - smoothed_hb
                            delta = float(np.clip(delta, -MAX_HB_STEP, MAX_HB_STEP))
                            candidate = smoothed_hb + (SMOOTHING_ALPHA * delta)

                            if abs(candidate - smoothed_hb) >= HB_DEADBAND:
                                smoothed_hb = candidate

                        sample_value = smoothed_hb if smoothed_hb is not None else hb_median
                        with session_lock:
                            session_samples.append(float(sample_value))

                with session_lock:
                    phase = session_phase
                    phase_start = phase_started_at

                if phase == "acquiring" and (current_time - phase_start) >= ACQUIRE_DURATION:
                    with session_lock:
                        if len(session_samples) >= MIN_VALID_SAMPLES:
                            current_hb = float(np.median(session_samples))
                            current_status = hb_to_status(current_hb)
                        else:
                            current_hb = 0.0
                            current_status = "No Valid Reading"
                        session_phase = "done"

                last_scan_time = current_time

            ret, buffer = cv2.imencode('.jpg', frame)
            if not ret:
                print("failed to encode frame to jpeg")
                continue

            frame_bytes = buffer.tobytes()
            yield (b'--frame\r\n' b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')

        except Exception as e:
            # Do not break generator; mark camera inactive and recover.
            print(f"error grabbing frame: {e}")
            try:
                if picam2 is not None:
                    picam2.stop()
            except Exception:
                pass
            camera_active = False
            af_locked = False
            lock_candidate_count = 0
            blur_count = 0
            focus_scores.clear()
            last_lens_position = None
            time.sleep(0.1)
            continue

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

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)

