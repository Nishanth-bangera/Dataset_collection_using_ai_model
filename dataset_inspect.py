import os
import json

DATABASE_PATH = "Dataset"

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png")

print("STUDENT DATASET INSPECTION")
print("=" * 40)

for usn in os.listdir(DATABASE_PATH):

    student_path = os.path.join(DATABASE_PATH, usn)

    if not os.path.isdir(student_path):
        continue

    print(f"\nStudent: {usn}")

    # Check JSON file
    json_path = os.path.join(
        student_path, "student_details.json"
    )

    if os.path.exists(json_path):

        with open(json_path, "r", encoding="utf-8") as file:
            data = json.load(file)

        if isinstance(data, dict):
            print("JSON fields:", list(data.keys()))
        else:
            print("JSON type:", type(data).__name__)

    # Check images
    images_path = os.path.join(student_path, "images")

    if os.path.isdir(images_path):

        images = [
            img for img in os.listdir(images_path)
            if img.lower().endswith(IMAGE_EXTENSIONS)
        ]

        print("Number of images:", len(images))
        print("Image files:", images)

    else:
        print("Images folder not found!")

print("\nInspection completed!")