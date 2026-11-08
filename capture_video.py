import csv
import os
from pathlib import Path
import qscript

CSV_PATH = "input.csv"

def main():
    if not os.path.exists(CSV_PATH):
        print(f"Error: CSV file not found at {CSV_PATH}")
        return

    qscript.PATH_TO_VIDEO_FOLDER = "./video"

    with open(CSV_PATH, mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter=";", quotechar='"')
        
        for row in reader:
            print(f"Processing row for company: {row['Company Name']} ...")
            video_path = qscript.CaptureVideo_Magento(
                url=row["URL"],
                company_name=row["Company Name"],
                language=row["Language"],
                country=row["Country"],
                postcode=row["Postcode"],
                city=row["City"],
                street=row["Street"],
                house_no=row["HouseNo"],
            )

            if video_path:
                print(f"Saved video: {video_path}")
            else:
                print(f"Failed to record video for {row['Company Name']}")

if __name__ == "__main__":
    main()