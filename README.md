# AneDet
> Complete technical documentation can be found [here](https://adobong-sunog.github.io/AneDet/).

A web application for our Capstone Project that uses CNN and XGBoost hybrid models for detection of Anemia in adults through hemoglobin prediction using fingernail image.

## Interface 
![Interface](static/Screenshot%202026-04-14%20162002.png)


## Prerequisites
### Note
> Trained model files are intentionally excluded from this repository to reduce privacy risks, including potential model inversion or membership inference attacks on sensitive data patterns. This supports privacy-by-design practices aligned with the Philippine Data Privacy Act of 2012 (Republic Act No. 10173).

#### Software Requirements
- Python 3.9 or higher
- Pip (Python package installer)

#### Hardware Requirements
- This project was made to run on a Raspberry Pi 5 with a Raspberry Pi Camera Module 3 for the image capture. Pi Camera Module 3 is recommended for better image quality, which can improve the accuracy of the anemia detection model. However, it may be possible to use other compatible camera modules with appropriate adjustments to the code.

## Installation
### Clone this repository
```bash
git clone https://github.com/adobong-sunog/AneDet.git
```
### Install dependencies
```bash
pip install -r requirements.txt
```

If Picamera2 fails to install on Raspberry Pi OS, install it with:
```bash
sudo apt install python3-picamera2
```

### Run the server
```bash
python app.py
```

Server starts on `http://localhost:5000`

## Notes
- This webapp was setup to run on a Raspberry Pi 5 connected to a 5 inch touch screen LCD display, so the UI is designed to have large buttons and controls for touch interactions.

## Recommendations
- Specify demographic targets due to different thresholds for anemia across age groups.
- Gather as much relevant data as possible for improving model accuracy and generalization.
