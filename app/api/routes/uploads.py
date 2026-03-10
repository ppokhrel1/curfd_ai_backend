import base64
import logging
import os
import re
import uuid

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

logger = logging.getLogger(__name__)

router = APIRouter()

UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "/tmp/curfd_uploads")
CHAT_IMAGES_DIR = os.path.join(UPLOAD_DIR, "chat-images")

# Ensure directory exists at import time
os.makedirs(CHAT_IMAGES_DIR, exist_ok=True)

_SAFE_FILENAME = re.compile(r"^[a-zA-Z0-9_-]+\.[a-z]+$")

MEDIA_TYPE_EXT = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
}


def save_chat_image(base64_data: str, media_type: str) -> str:
    """Save a base64-encoded image to disk. Returns the filename."""
    ext = MEDIA_TYPE_EXT.get(media_type, "jpg")
    filename = f"{uuid.uuid4().hex}.{ext}"
    filepath = os.path.join(CHAT_IMAGES_DIR, filename)

    image_bytes = base64.b64decode(base64_data)
    with open(filepath, "wb") as f:
        f.write(image_bytes)

    logger.info(f"[UPLOAD] Saved chat image: {filename} ({len(image_bytes)} bytes)")
    return filename


@router.get("/chat-images/{filename}")
async def serve_chat_image(filename: str):
    """Serve a saved chat image."""
    if not _SAFE_FILENAME.match(filename):
        raise HTTPException(status_code=400, detail="Invalid filename")

    filepath = os.path.join(CHAT_IMAGES_DIR, filename)
    if not os.path.isfile(filepath):
        raise HTTPException(status_code=404, detail="Image not found")

    ext = filename.rsplit(".", 1)[-1]
    media_types = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp", "gif": "image/gif"}
    return FileResponse(filepath, media_type=media_types.get(ext, "image/jpeg"))
