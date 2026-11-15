# Browser Automation Video Recorder & Zoom Service

## Setup & Run

1. Install Python dependencies and Playwright browser:
   ```bash
   pip install -r requirements.txt
   python -m playwright install chromium
   ```

2. Start the Docker stack (Apache + Node.js + ffmpeg + PostgreSQL):
   ```bash
   docker compose up --build
   ```

3. Run the recorder and uploader:
   ```bash
   python capture_video.py --server http://127.0.0.1:8000 --view-seconds 3
   ```

4. Post-process a single video locally:
   ```bash
   python apply_zoom.py --video video/MagentoDev_Berlin.webm --dry-run
   python apply_zoom.py --video video/MagentoDev_Berlin.webm
   ```

5. Trigger manual zoom processing for a job by ID:
   ```bash
   curl -X POST http://127.0.0.1:8000/jobs/1/process
   ```

6. Run unit tests:
   ```bash
   pytest
   ```

7. Stop containers:
   ```bash
   docker compose down
   ```