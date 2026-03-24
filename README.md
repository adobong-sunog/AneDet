# AneDet

A web application for our Capstone Project that uses CNN and XGBoost hybrid models for detection of Anemia through hemoglobin prediction using fingernail image.

### Prerequisites

#### Software Requirements
- Python 3.8 or higher
- Pip (Python package installer)

#### Hardware Requirements
- We used a Raspberry Pi Camera Module 3 for this project so it is the recommended camera module (Pi Camera Module 2 can work as well)

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
