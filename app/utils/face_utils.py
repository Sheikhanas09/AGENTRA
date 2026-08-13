import numpy as np
import base64
from PIL import Image
import io


def decode_base64_image(base64_str: str):
    """Base64 string → PIL Image"""
    try:
        if "," in base64_str:
            base64_str = base64_str.split(",")[1]
        img_bytes = base64.b64decode(base64_str)
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        return img
    except Exception as e:
        print(f"Image decode error: {e}")
        return None

def get_face_embedding(image) -> list:
    """
    MediaPipe FaceLandmarker (new API)
    """
    try:
        import mediapipe as mp
        from mediapipe.tasks import python
        from mediapipe.tasks.python import vision
        import urllib.request
        import tempfile
        import os

        # ──── Model download karo ────
        model_path = os.path.join(
            os.path.dirname(__file__), "face_landmarker.task"
        )

        if not os.path.exists(model_path):
            print("Downloading face landmarker model...")
            url = "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task"
            urllib.request.urlretrieve(url, model_path)
            print("Model downloaded!")

        # ──── Options ────
        base_options = python.BaseOptions(model_asset_path=model_path)
        options = vision.FaceLandmarkerOptions(
            base_options=base_options,
            num_faces=1,
            min_face_detection_confidence=0.3,
            min_face_presence_confidence=0.3  # ← yeh change karo
        )

        # ──── PIL → MediaPipe Image ────
        img_array = np.array(image)
        mp_image = mp.Image(
            image_format=mp.ImageFormat.SRGB,
            data=img_array
        )

        # ──── Detect ────
        with vision.FaceLandmarker.create_from_options(options) as detector:
            result = detector.detect(mp_image)

        if not result.face_landmarks:
            print("No face detected")
            return None

        landmarks = result.face_landmarks[0]

        # ──── Key points ────
        key_indices = [
            33, 7, 163, 144, 145, 153, 154, 155, 133,
            246, 161, 160, 159, 158, 157, 173,
            1, 2, 5, 4, 195, 197,
            61, 84, 17, 314, 405, 321, 375, 291,
            152, 148, 176, 149, 150, 136, 172,
            70, 63, 105, 66, 107, 336, 296, 334, 293, 300,
            116, 123, 147, 213, 192, 345, 352, 376, 433, 416,
            10, 338, 297, 332, 284, 251, 389, 109, 67, 103
        ]

        embedding = []
        for idx in key_indices:
            if idx < len(landmarks):
                lm = landmarks[idx]
                embedding.extend([lm.x, lm.y, lm.z])

        emb_array = np.array(embedding, dtype=np.float32)
        emb_array[0::3] -= emb_array[0::3].mean()
        emb_array[1::3] -= emb_array[1::3].mean()

        scale = np.std(emb_array)
        if scale > 0:
            emb_array = emb_array / scale

        return emb_array.tolist()

    except Exception as e:
        print(f"MediaPipe embedding error: {e}")
        return None


def compute_face_distance(embedding1: list, embedding2: list) -> float:
    """Cosine distance between embeddings"""
    try:
        e1 = np.array(embedding1, dtype=np.float32)
        e2 = np.array(embedding2, dtype=np.float32)

        if len(e1) != len(e2):
            print(f"Embedding size mismatch: {len(e1)} vs {len(e2)}")
            return 1.0

        dot = np.dot(e1, e2)
        n1 = np.linalg.norm(e1)
        n2 = np.linalg.norm(e2)

        if n1 == 0 or n2 == 0:
            return 1.0

        similarity = dot / (n1 * n2)
        distance = 1 - similarity
        return float(distance)

    except Exception as e:
        print(f"Distance error: {e}")
        return 1.0


def verify_face(
    live_image_base64: str,
    stored_embedding: list,
    threshold: float = 0.05
    # ↑ MediaPipe ke saath strict threshold
    # Same person = 0.01-0.04
    # Different person = 0.05+
) -> dict:

    image = decode_base64_image(live_image_base64)
    if image is None:
        return {"verified": False, "distance": 1.0, "error": "Image decode failed"}

    live_embedding = get_face_embedding(image)
    if live_embedding is None:
        # ──── Face detect nahi hua ────
        return {"verified": False, "distance": 1.0, "error": "No face detected"}

    if len(live_embedding) != len(stored_embedding):
        # ──── Old enrollment → Re-enroll karo ────
        return {
            "verified": False,
            "distance": 1.0,
            "error": "Re-enrollment required"
        }

    distance = compute_face_distance(live_embedding, stored_embedding)
    print(f"MediaPipe face distance: {distance:.4f} (threshold: {threshold})")

    verified = distance < threshold

    return {
        "verified": verified,
        "distance": round(distance, 4),
        "error": None
    }


def enroll_face_from_images(base64_images: list) -> list:
    embeddings = []
    for b64_img in base64_images:
        image = decode_base64_image(b64_img)
        if image is None:
            continue
        embedding = get_face_embedding(image)
        if embedding is not None:
            embeddings.append(embedding)

    if not embeddings:
        # ──── Face detect nahi hua ────
        # Simple pixel embedding use karo
        print("Warning: Using pixel embedding")
        for b64_img in base64_images:
            image = decode_base64_image(b64_img)
            if image:
                arr = np.array(image.resize((64, 64)).convert("L"), dtype=np.float32) / 255.0
                embeddings.append(arr.flatten().tolist())
                break

    if not embeddings:
        return [0.5] * 4096

    return np.mean(embeddings, axis=0).tolist()


def prepare_photo_for_db(
    base64_str: str,
    max_dim: int = 640,
    quality: int = 75,
    max_input_bytes: int = 12 * 1024 * 1024
) -> dict:
    """
    Base64 camera photo → chhota compressed JPEG (DB mein store karne ke liye).

    Browser ka canvas.toDataURL() 1-4 MB ka base64 deta hai. Usay jaisa ka
    taisa DB mein daalna DB ko phula deta hai, isliye:
      - lamba side max 640px tak resize (attendance record ke liye kaafi)
      - JPEG quality 75 pe re-encode
      - result ~30-60 KB
    sha256 bhi deta hai taake baad mein verify ho sake image badli to nahi.

    Return: {data, mime_type, width, height, size_bytes, sha256} ya None
    """
    import hashlib

    if not base64_str:
        return None

    try:
        raw = base64_str.split(",", 1)[1] if "," in base64_str else base64_str

        # ──── Bohot bara payload pehle hi reject ────
        if len(raw) > max_input_bytes:
            print(f"Photo too large: {len(raw)} bytes")
            return None

        img = Image.open(io.BytesIO(base64.b64decode(raw)))

        # ──── Transparency/palette wali images JPEG mein nahi jatin ────
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")

        width, height = img.size
        if max(width, height) > max_dim:
            img.thumbnail((max_dim, max_dim), Image.LANCZOS)

        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=quality, optimize=True)
        data = buffer.getvalue()

        return {
            "data": data,
            "mime_type": "image/jpeg",
            "width": img.size[0],
            "height": img.size[1],
            "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }

    except Exception as e:
        print(f"Photo prepare error: {e}")
        return None
