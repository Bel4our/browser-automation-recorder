# CSV-driven Browser Automation Video Recorder
Tool that records videos of automated browser interactions for each CSV row, paired with a lightweight backend for video uploading, SQLite storage, and browser preview


## Setup & Run
1. Install dependencies for Python:
```bash
pip install -r requirements.txt
python -m playwright install chromium
```


2. Install dependencies for Node.js:
```bash
npm install
```


3. Start backend service:
```bash
npm start
```
*(Runs on `http://127.0.0.1:8000` and automatically creates `videos.db` and `uploads/`)*

4. Run recorder with upload and preview:
```bash
python capture_video.py --server http://127.0.0.1:8000 --view-seconds 3
```