"""
Document prep — DB mein store karne se pehle
────────────────────────────────────────────
Medical certificates PDF bhi ho sakte hain aur mobile se kheenchi photo bhi.
Photo 4-8 MB ki aa jati hai — usay jaisa ka taisa DB mein daalna DB ko phula
deta hai. Image ho to compress karte hain, PDF ko haath nahi lagate.
"""

import hashlib
import io
import os

# Kis kism ki files qabool hain
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

# Certificate parhne layak rehna chahiye, isliye attendance photo se bara
DOC_MAX_DIM = 1600
DOC_JPEG_QUALITY = 80


class DocumentError(Exception):
    """Upload qabool na ho — message seedha user ko dikhta hai"""


def prepare_document(filename: str, raw: bytes) -> dict:
    """
    Uploaded file ko DB mein store karne layak banao.

    Return: {data, mime_type, file_name, size_bytes, sha256, width, height}
    Ghalat file pe DocumentError uthata hai.
    """
    if not raw:
        raise DocumentError("File khali hai")

    if len(raw) > MAX_UPLOAD_BYTES:
        raise DocumentError(
            f"File bohot bari hai ({len(raw) // (1024 * 1024)} MB) — "
            f"{MAX_UPLOAD_BYTES // (1024 * 1024)} MB tak allowed hai"
        )

    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise DocumentError(
            f"Sirf {', '.join(sorted(ALLOWED_EXTENSIONS))} files allowed hain"
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
            raise DocumentError(f"Image parh nahi paye: {e}")

    elif not raw.startswith(b"%PDF"):
        # ──── Naam .pdf hai magar andar PDF nahi ────
        raise DocumentError("Yeh valid PDF file nahi hai")

    return {
        "data": data,
        "mime_type": mime_type,
        "file_name": os.path.basename(filename or "document"),
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "width": width,
        "height": height,
    }
