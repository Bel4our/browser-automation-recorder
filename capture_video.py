import csv
import os
import sys
import time
import argparse
from pathlib import Path
import qscript

CSV_PATH = "input.csv"

def preview_video(video_url: str, view_seconds: float):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()
        page.goto(video_url)

        if view_seconds > 0:
            time.sleep(view_seconds)
        else:
            input(f"Previewing {video_url}. Press Enter to continue to next video...")

        browser.close()


def main():
    parser = argparse.ArgumentParser(description="CSV-driven Browser Automation Video Recorder & Uploader")
    parser.add_argument("--server", type=str, default=None, help="Server base URL (e.g. http://127.0.0.1:8000)")
    parser.add_argument("--view-seconds", type=float, default=3.0, help="Preview duration in seconds (0 = wait for Enter)")
    args = parser.parse_args()

    if not os.path.exists(CSV_PATH):
        return

    qscript.PATH_TO_VIDEO_FOLDER = "./video"

    with open(CSV_PATH, mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter=";", quotechar='"')
        for row in reader:
            company = row["Company Name"]
            video_path = qscript.CaptureVideo_Magento(
                url=row["URL"],
                company_name=company,
                language=row["Language"],
                country=row["Country"],
                postcode=row["Postcode"],
                city=row["City"],
                street=row["Street"],
                house_no=row["HouseNo"],
            )

            if not video_path:
                continue

            print(video_path)

            if args.server:
                try:
                    import video_client

                    video_url = video_client.UploadVideo(
                        server_url=args.server,
                        video_path=video_path,
                        company_name=company,
                    )
                    print(f"Uploaded: {video_url}")

                    try:
                        preview_video(video_url, args.view_seconds)
                    except Exception as preview_err:
                        print(f"Preview failed for {company}: {preview_err}", file=sys.stderr)

                except Exception as upload_err:
                    print(f"Upload failed for {company}: {upload_err}", file=sys.stderr)


if __name__ == "__main__":
    main()