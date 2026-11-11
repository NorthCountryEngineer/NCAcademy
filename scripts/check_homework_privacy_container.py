"""Mandatory container check: real local OCR, canonical task and no raw egress."""
import base64
from io import BytesIO
import json

from PIL import Image, ImageDraw, ImageFont

from academy.curated_media import normalize_assignment_image
from academy.homework_deidentification import compile_homework_task, build_public_homework_prompt


def main():
    image = Image.new("RGB", (1150, 800), "white")
    ImageDraw.Draw(image).multiline_text(
        (35, 30), "Name: FICTIONAL PRIVATE CANARY\nPhone: 315-555-1212\n\n1. Draw an array with 3 rows and 5 dots in each row.\n\n7. A gardener puts 24 seedlings equally into 4 trays.\nHow many seedlings go into each tray?\n\n8. A rectangle has 3 rows of 7 unit squares.",
        fill="black", font=ImageFont.load_default(size=34), spacing=20,
    )
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    data_url = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()
    original = base64.b64decode(normalize_assignment_image(data_url)["data_base64"])
    first = compile_homework_task("Question 1", [original])
    seventh = compile_homework_task("Question 7. Look at the sheet.", [original], first.task, first.stage, first.question_number)
    assert first.task == {"kind": "arithmetic", "operation": "multiplication", "values": [3, 5]}, first
    assert seventh.task == {"kind": "arithmetic", "operation": "division", "values": [24, 4]}, seventh
    assert seventh.image_read and seventh.question_number == 7
    wire = json.dumps(build_public_homework_prompt(seventh.task))
    assert all(value not in wire for value in ("FICTIONAL", "CANARY", "315-555", base64.b64encode(original).decode()))
    print("Real OCR selected questions 1 and 7; only reconstructed public math reaches the prompt.")


if __name__ == "__main__":
    main()
