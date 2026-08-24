"""
Document prep — before storing in the DB
────────────────────────────────────────────
A medical certificate can be a PDF or a photo taken on a phone. Photos
arrive at 4-8 MB — storing them untouched bloats the DB. Images are
compressed; PDFs are left alone.
"""

import hashlib
import io
import os

# Which kinds of file are accepted
ALLOWED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".webp"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}

MIME_TYPES = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}

MAX_UPLOAD_BYTES = 10 * 1024 * 1024      # 10 MB

# A certificate must stay readable, so this is larger than an attendance photo
DOC_MAX_DIM = 1600
DOC_JPEG_QUALITY = 80


class DocumentError(Exception):
    """The upload was rejected — this message is shown to the user directly"""


def prepare_document(filename: str, raw: bytes) -> dict:
    """
    Make an uploaded file fit to store in the DB.

    Return: {data, mime_type, file_name, size_bytes, sha256, width, height}
    Raises DocumentError on an invalid file.
    """
    if not raw:
        raise DocumentError("The file is empty")

    if len(raw) > MAX_UPLOAD_BYTES:
        raise DocumentError(
            f"The file is too large ({len(raw) // (1024 * 1024)} MB) — "
            f"up to {MAX_UPLOAD_BYTES // (1024 * 1024)} MB is allowed"
        )

    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise DocumentError(
            f"Only {', '.join(sorted(ALLOWED_EXTENSIONS))} files are allowed"
        )

    data = raw
    mime_type = MIME_TYPES.get(ext, "application/octet-stream")
    width = height = None

    if ext in IMAGE_EXTENSIONS:
        try:
            from PIL import Image

            img = Image.open(io.BytesIO(raw))
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")

            if max(img.size) > DOC_MAX_DIM:
                img.thumbnail((DOC_MAX_DIM, DOC_MAX_DIM), Image.LANCZOS)

            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=DOC_JPEG_QUALITY, optimize=True)
            data = buffer.getvalue()
            mime_type = "image/jpeg"
            width, height = img.size

        except DocumentError:
            raise
        except Exception as e:
            raise DocumentError(f"The image could not be read: {e}")

    elif not raw.startswith(b"%PDF"):
        # ──── Named .pdf but not actually a PDF inside ────
        raise DocumentError("This is not a valid PDF file")

    return {
        "data": data,
        "mime_type": mime_type,
        "file_name": os.path.basename(filename or "document"),
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "width": width,
        "height": height,
    }
