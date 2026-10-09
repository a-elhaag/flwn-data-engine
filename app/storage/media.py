"""Audio and video as Azure Speech wants them. Audio containers go straight through; other video
(mp4, mov, mkv) has its sound track pulled out with ffmpeg, which must be installed."""

import shutil
import subprocess
import tempfile
from pathlib import Path

from app.config import settings
from app.storage.errors import Unindexable

AUDIO_MIME = {
    "audio/wav", "audio/x-wav", "audio/wave", "audio/mpeg", "audio/mp3", "audio/ogg", "audio/opus",
    "audio/flac", "audio/x-flac", "audio/aac", "audio/mp4", "audio/x-m4a", "audio/m4a",
    "audio/amr", "audio/webm", "video/webm", "audio/x-ms-wma", "audio/speex",
}  # fmt: skip
AUDIO_EXTENSIONS = {
    ".wav", ".mp3", ".ogg", ".oga", ".opus", ".flac", ".aac", ".m4a", ".amr", ".webm", ".wma", ".spx",
}  # fmt: skip
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".mpeg", ".mpg", ".3gp"}


def _extension(name: str) -> str:
    return "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""


def media_kind(content_type: str | None, name: str) -> str | None:
    """'audio' (sent as is), 'video' (needs ffmpeg), or None."""
    mime = (content_type or "").split(";")[0].strip().lower()
    if mime in AUDIO_MIME or _extension(name) in AUDIO_EXTENSIONS:
        return "audio"
    if mime.startswith("video/") or _extension(name) in VIDEO_EXTENSIONS:
        return "video"
    return None


def audio_track(data: bytes, name: str) -> tuple[bytes, str, str]:
    """Extract the sound of a video as mono 16 kHz Opus in an Ogg file: (bytes, name, mime)."""
    if shutil.which("ffmpeg") is None:
        raise Unindexable("video files need ffmpeg, which is not installed on this server")
    with tempfile.TemporaryDirectory() as folder:
        source, target = Path(folder, "in" + (_extension(name) or ".bin")), Path(folder, "out.ogg")
        source.write_bytes(data)
        try:
            run = subprocess.run(
                ["ffmpeg", "-nostdin", "-v", "error", "-i", str(source), "-vn", "-ac", "1",
                 "-ar", "16000", "-c:a", "libopus", "-b:a", "32k", str(target)],
                capture_output=True,
                timeout=settings.FFMPEG_TIMEOUT_SECONDS,
            )  # fmt: skip
        except subprocess.TimeoutExpired:
            raise Unindexable("reading the video took too long") from None
        if run.returncode != 0 or not target.exists() or target.stat().st_size == 0:
            raise Unindexable("the video has no readable sound track")
        return target.read_bytes(), "audio.ogg", "audio/ogg"
