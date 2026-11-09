"""Draw detector output on a source JPEG; camera pixels remain the reference."""
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

from app.vision.person_detector import PersonBox


def annotate_live_frame(source_jpeg: bytes, persons: tuple[PersonBox, ...]) -> bytes:
    """Draw frame-sized boxes and whole-number model confidence percentages."""
    if not persons:
        return source_jpeg
    with Image.open(BytesIO(source_jpeg)) as source:
        image = source.convert("RGB")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    width, height = image.size
    for person in persons:
        left, top = person.x1, person.y1
        right, bottom = min(width - 1, person.x2), min(height - 1, person.y2)
        if left >= right or top >= bottom:
            continue
        stroke = max(2, round(min(width, height) / 250))
        draw.rectangle((left, top, right, bottom), outline=(20, 255, 110), width=stroke)
        label = f"Person {person.confidence:.0%}"
        bounds = draw.textbbox((0, 0), label, font=font)
        text_width, text_height = bounds[2] - bounds[0], bounds[3] - bounds[1]
        label_x = min(left, max(0, width - text_width - 7))
        label_y = max(0, top - text_height - 7)
        draw.rectangle((label_x, label_y, min(width-1, label_x + text_width + 6),
                        min(height-1, label_y + text_height + 6)), fill=(0, 55, 28))
        draw.text((label_x + 3, label_y + 3), label, font=font, fill=(255, 255, 255))
    output = BytesIO()
    image.save(output, format="JPEG", quality=85)
    return output.getvalue()
