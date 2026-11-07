import os
import json

DATABASE_PATH = "Dataset" 


print("Checking Student Dataset...\n")

for usn_folder in os.listdir(DATABASE_PATH):

    student_path = os.path.join(DATABASE_PATH, usn_folder)

    if os.path.isdir(student_path):

        print("Student Folder:", usn_folder)

        for file in os.listdir(student_path):

            print("   File:", file)

print("\nDataset checking completed!")