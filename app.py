import cv2
import numpy as np
import time
from flask import Flask, render_template, Response, jsonify, request
from tensorflow.keras.models import load_model
from xgboost import XGBRegressor

app = Flask(__name__)

BIAS_OFFSET = 0.1

# Camera
try:
    camera = cv2.VideoCapture(0)
    camera.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    camera_active = True
except:
    print("No Camera. Upload Mode Only.")
    camera_active = False

# Model initialization
print("Load models")
cnn = load_model('anemia_cnn.h5')
xgb = XGBRegressor()
xgb.load_model('anemia_xgb.json')
print("Models loaded")

# Global state
current_hb = 0.0
last_scan_time = 0
SCAN_INTERVAL = 2.0 

def process_image(img):
    # Crop to center (green box)
    h, w, _ = img.shape
    if h < 224 or w < 224:
        crop = cv2.resize(img, (224, 224))
    else:
        cx, cy = w // 2, h // 2
        crop = img[cy-112:cy+112, cx-112:cx+112]

    # Pre process with usual /255
    img_array = cv2.resize(crop, (224, 224))
    img_array = img_array / 255.0  
    img_array = np.expand_dims(img_array, axis=0)

    # Inference
    features = cnn.predict(img_array, verbose=0)
    prediction = xgb.predict(features)[0]
    hb = float(prediction)
    
    # Bias correction
    hb = hb + BIAS_OFFSET

    # Classification
    status = "Normal"
    if hb < 8.0: status = "Severe Anemia"
    elif hb < 10.0: status = "Moderate Anemia"
    elif hb < 12.0: status = "Mild Anemia"

    return hb, status

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload():
    file = request.files['file']
    npimg = np.frombuffer(file.read(), np.uint8)
    img = cv2.imdecode(npimg, cv2.IMREAD_COLOR)
    hb, status = process_image(img)
    return jsonify({'hb': round(hb, 1), 'status': status})

def generate_frames():
    global current_hb, last_scan_time
    while camera_active:
        success, frame = camera.read()
        if not success: break

        # Draw Green Box
        h, w, _ = frame.shape
        cv2.rectangle(frame, (w//2-112, h//2-112), (w//2+112, h//2+112), (0, 255, 0), 2)
        
        # Display calibration status
        cv2.putText(frame, f"Calib: +{BIAS_OFFSET}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0,255,255), 2)

        if time.time() - last_scan_time > SCAN_INTERVAL:
            hb, status = process_image(frame)
            current_hb = hb 
            last_scan_time = time.time()

        ret, buffer = cv2.imencode('.jpg', frame)
        frame = buffer.tobytes()
        yield (b'--frame\r\n' b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')

# Show camera video
@app.route('/video_feed')
def video_feed():
    return Response(generate_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/get_result')
def get_result():
    status = "Normal"
    if current_hb < 8.0: status = "Severe Anemia"
    elif current_hb < 10.0: status = "Moderate Anemia"
    elif current_hb < 12.0: status = "Mild Anemia"
    return jsonify({'hb': round(current_hb, 1), 'status': status})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)