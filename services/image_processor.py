from __future__ import annotations

import os

from PIL import Image


def validate_uploaded_image(file_storage, max_bytes: int = 16 * 1024 * 1024):
    filename = file_storage.filename or ""
    extension = os.path.splitext(filename)[1].lower()
    allowed_types = {".jpg", ".jpeg", ".png"}

    if extension not in allowed_types:
        raise ValueError("Only JPEG and PNG files are supported.")

    if file_storage.content_length and file_storage.content_length > max_bytes:
        raise ValueError("Uploaded file is larger than the allowed size.")

    file_storage.seek(0)
    image = Image.open(file_storage)
    image.verify()
    file_storage.seek(0)
    image = Image.open(file_storage)
    width, height = image.size
    if width <= 0 or height <= 0:
        raise ValueError("The uploaded image has invalid dimensions.")

    return {"filename": filename, "width": width, "height": height, "format": image.format}
