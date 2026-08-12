"""
utils/media.py — Turns a sticker/GIF/video into one representative JPEG frame
(base64-encoded) that can be handed to the vision model, the same way a
regular photo already is.

Static stickers (.webp) are just re-encoded with Pillow.
Animated content (GIF, video stickers, videos — Telegram sends "GIF"
animations as short .mp4 files) has a frame pulled out with ffmpeg via
`imageio-ffmpeg`, which ships its own static ffmpeg binary so nothing extra
needs to be installed on Railway/the OS.

Note: classic Telegram *animated* stickers (.tgs, gzipped Lottie/JSON) are
vector animations, not video — ffmpeg can't rasterize them. Those are
skipped; see `is_unsupported_tgs`.
"""
import io
import logging
import subprocess
import tempfile
import base64
import os

from PIL import Image
import imageio_ffmpeg

logger = logging.getLogger("amy.media")

FRAME_SECOND = "0.3"   # pull the frame a little into the clip, not a black first frame


def is_unsupported_tgs(file_path: str | None) -> bool:
    return bool(file_path) and file_path.lower().endswith(".tgs")


def webp_bytes_to_jpeg_b64(data: bytes) -> str:
    """Static sticker (webp) → JPEG base64."""
    img = Image.open(io.BytesIO(data)).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    return base64.b64encode(buf.getvalue()).decode()


def video_bytes_to_frame_b64(data: bytes, suffix: str = ".mp4") -> str:
    """
    GIF/animation/video/video-sticker bytes → one JPEG frame, base64.
    Uses the ffmpeg binary bundled by imageio-ffmpeg (no system install needed).
    """
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as src:
        src.write(data)
        src_path = src.name

    dst_path = src_path + ".jpg"
    try:
        subprocess.run(
            [
                ffmpeg_exe, "-y",
                "-ss", FRAME_SECOND,
                "-i", src_path,
                "-frames:v", "1",
                "-q:v", "3",
                dst_path,
            ],
            check=True,
            capture_output=True,
            timeout=20,
        )
        with open(dst_path, "rb") as f:
            jpeg_bytes = f.read()
        return base64.b64encode(jpeg_bytes).decode()
    finally:
        for p in (src_path, dst_path):
            try:
                os.remove(p)
            except OSError:
                pass
