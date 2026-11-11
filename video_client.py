from pathlib import Path
import requests


def UploadVideo(server_url: str, video_path: str, company_name: str) -> str:
    url = f"{server_url.rstrip('/')}/upload"
    file_path = Path(video_path).resolve()

    if not file_path.exists():
        raise FileNotFoundError(f"Video file not found at: {file_path}")

    with open(file_path, "rb") as f:
        files = {"file": (file_path.name, f, "video/webm")}
        data = {"company_name": company_name}
        response = requests.post(url, files=files, data=data, timeout=60)

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