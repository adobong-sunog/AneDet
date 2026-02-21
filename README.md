# AneDet

A web application for our Capstone Project that uses CNN and XGBoost hybrid models for detection of Anemia through hemoglobin prediction using fingernail image.

### Prerequisites

#### Software Requirements
- Python 3.8 or higher
- Pip (Python package installer)

#### Hardware Requirements
- Webcam (or any camera module that supports the default system camera index) for using the live image capture feature

### Installation
### Clone this repository
```bash
git clone https://github.com/adobong-sunog/AneDet.git
```
### Install dependencies
```bash
pip install flask opencv-python numpy tensorflow xgboost
```

### Run the server
```bash
python app.py
```

Server starts on `http://localhost:5000`

## Notes
- This webapp was setup to run on a Raspberry Pi 5 connected to a 5 inch touch screen LCD display, so the UI is designed to have large buttons and controls for touch interactions.

## Bugs
- None at the moment

## TODO
- Improve UI design
- Find the proper offset bias for the model to improve accuracy
- Gather more data to train the model for better performance
