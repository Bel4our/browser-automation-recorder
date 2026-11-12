# Video Web Service & Database in Docker


## Setup & Run
1. Start all services with Docker Compose:
   ```bash
   docker compose up --build
   ```

2. Run the recorder client (in a separate terminal):
   ```bash
   python capture_video.py --server http://127.0.0.1:8000 --view-seconds 3
   ```

3. Stop containers:
   ```bash
   docker compose down
   ```