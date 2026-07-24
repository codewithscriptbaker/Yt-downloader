"""Download quality / format presets for yt-dlp."""

from __future__ import annotations

from typing import Literal

Quality = Literal["best", "1080", "720", "360", "audio"]
AudioFormat = Literal["m4a", "mp3"]

QUALITY_CHOICES: tuple[Quality, ...] = ("best", "1080", "720", "360", "audio")
AUDIO_FORMAT_CHOICES: tuple[AudioFormat, ...] = ("m4a", "mp3")

# height caps for video presets
_HEIGHT = {
    "1080": 1080,
    "720": 720,
    "360": 360,
}


def resolve_ydl_format(*, quality: str, url: str, is_tiktok: bool) -> str:
    """Return a yt-dlp format selector string for the chosen quality."""
    q = (quality or "best").lower()
    if q == "audio":
        return "ba/b"

    if q in _HEIGHT:
        h = _HEIGHT[q]
        if is_tiktok:
            # Prefer progressive file at or below height, then merge fallback.
            return f"b[height<=?{h}]/bv*[height<=?{h}]+ba/b"
        return f"bv*[height<=?{h}]+ba/b[height<=?{h}]/bv*[height<=?{h}]+ba/b"

    # best
    if is_tiktok:
        return "b/bv*+ba/b"
    return "bv*+ba/b"


def is_audio_quality(quality: str) -> bool:
    return (quality or "").lower() == "audio"


def audio_postprocessors(audio_format: str) -> list[dict]:
    """FFmpeg postprocessors when extracting audio."""
    fmt = (audio_format or "m4a").lower()
    if fmt == "mp3":
        return [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ]
    # m4a — extract/convert to m4a when needed
    return [
        {
            "key": "FFmpegExtractAudio",
            "preferredcodec": "m4a",
            "preferredquality": "192",
        }
    ]
