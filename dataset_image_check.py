import os
import cv2

DATABASE_PATH = "dataset"

total_images = 0
valid_images = 0
invalid_images = 0

print("STUDENT IMAGE VALIDATION")
print("=" * 40)

for usn in os.listdir(DATABASE_PATH):

    student_path = os.path.join(DATABASE_PATH, usn)
    images_path = os.path.join(student_path, "images")

    if not os.path.isdir(images_path):
        continue

    for image_name in os.listdir(images_path):

        if image_name.lower().endswith(
            (".jpg", ".jpeg", ".png")
        ):

            image_path = os.path.join(
                images_path, image_name
            )

            total_images += 1

            image = cv2.imread(image_path)

            if image is not None:

                valid_images += 1

                height, width = image.shape[:2]

                print(
                    f"{image_name}: "
                    f"{width}x{height} - VALID"
                )

            else:
                invalid_images += 1
                print(f"{image_name}: INVALID")

print("\n========== SUMMARY ==========")

print("Total Images:", total_images)
print("Valid Images:", valid_images)
print("Invalid Images:", invalid_images)

if invalid_images == 0:
    print("All images are readable!")

print("=============================")