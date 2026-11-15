import time
from pathlib import Path
import requests


def UploadVideo(server_url: str, video_path: str, company_name: str) -> str:
    url = f"{server_url.rstrip('/')}/upload"
    file_path = Path(video_path).resolve()

    if not file_path.exists():
        raise FileNotFoundError(f"Video file not found at: {file_path}")

    sidecar_path = Path(f"{file_path}.zoom.json")
    if not sidecar_path.exists():
        alt_sidecar = file_path.with_suffix(".zoom.json")
        if alt_sidecar.exists():
            sidecar_path = alt_sidecar

    data = {"company_name": company_name}

    with open(file_path, "rb") as vf:
        files = [("file", (file_path.name, vf, "video/webm"))]
        if sidecar_path.exists():
            with open(sidecar_path, "rb") as sf:
                files.append(("sidecar", (sidecar_path.name, sf, "application/json")))
                response = requests.post(url, files=files, data=data, timeout=120)
        else:
            response = requests.post(url, files=files, data=data, timeout=120)

    response.raise_for_status()
    payload = response.json()
    return payload["video_url"]


def GetVideoURL(server_url: str, company_name: str) -> str:
    url = f"{server_url.rstrip('/')}/get_video_url"
    params = {"company_name": company_name}
    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()
    payload = response.json()
    return payload["video_url"]


def WaitForProcessedVideo(server_url: str, company_name: str, timeout: float = 120.0, poll_interval: float = 1.5) -> str:
    url = f"{server_url.rstrip('/')}/get_video_url"
    params = {"company_name": company_name}
    deadline = time.monotonic() + timeout
    payload = {}

    while time.monotonic() < deadline:
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        payload = response.json()
        status = payload.get("status")
        if status == "done" and payload.get("processed_video_url"):
            return payload["processed_video_url"]
        if status in ("skipped", "failed"):
            return payload["video_url"]
        time.sleep(poll_interval)

    return payload.get("video_url", "")