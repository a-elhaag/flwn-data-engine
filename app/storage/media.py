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


def container_format(data: bytes) -> str | None:
    """The ffmpeg demuxer for what the bytes really are, from their magic numbers. The file's name
    and content type come from the uploader, so they are never used to choose a demuxer: a text
    playlist (HLS, concat) renamed .mp4 could otherwise make ffmpeg fetch URLs or read local files."""
    if data[4:8] == b"ftyp":
        return "mov,mp4,m4a,3gp,3g2,mj2"
    if data[:4] == b"\x1a\x45\xdf\xa3":
        return "matroska,webm"
    if data[:4] == b"RIFF" and data[8:12] == b"AVI ":
        return "avi"
    return None


def audio_track(data: bytes, name: str) -> tuple[bytes, str, str]:
    """Extract the sound of a video as mono 16 kHz Opus in an Ogg file: (bytes, name, mime)."""
    container = container_format(data)
    if container is None:
        raise Unindexable("unsupported video format (mp4, mov, mkv, webm and avi are read)")
    if shutil.which("ffmpeg") is None:
        raise Unindexable("video files need ffmpeg, which is not installed on this server")
    with tempfile.TemporaryDirectory() as folder:
        source, target = Path(folder, "input.bin"), Path(folder, "out.ogg")
        source.write_bytes(data)
        try:
            run = subprocess.run(
                ["ffmpeg", "-nostdin", "-v", "error",
                 "-protocol_whitelist", "file",  # no network, no pipes, no other protocols
                 "-f", container, "-i", str(source),
                 "-vn", "-ac", "1", "-ar", "16000", "-c:a", "libopus", "-b:a", "32k",
                 "-f", "ogg", str(target)],
                capture_output=True,
                timeout=settings.FFMPEG_TIMEOUT_SECONDS,
            )  # fmt: skip
        except subprocess.TimeoutExpired:
            raise Unindexable("reading the video took too long") from None
        if run.returncode != 0 or not target.exists() or target.stat().st_size == 0:
            raise Unindexable("the video has no readable sound track")
        return target.read_bytes(), "audio.ogg", "audio/ogg"
