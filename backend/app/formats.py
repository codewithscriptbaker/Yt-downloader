"""Download quality / format presets for yt-dlp."""

from __future__ import annotations

from typing import Any, Literal, Optional

Quality = Literal["best", "1080", "720", "360", "audio"]
AudioFormat = Literal["m4a", "mp3"]

QUALITY_CHOICES: tuple[str, ...] = ("best", "1080", "720", "360", "audio")
AUDIO_FORMAT_CHOICES: tuple[AudioFormat, ...] = ("m4a", "mp3")

# Common display buckets (used when snapping nearby heights)
_DISPLAY_HEIGHTS = (2160, 1440, 1080, 720, 480, 360, 240, 144)


def normalize_quality(value: str | None) -> str:
    q = (value or "best").strip().lower()
    if q in ("best", "audio"):
        return q
    if q.endswith("p") and q[:-1].isdigit():
        q = q[:-1]
    if q.isdigit():
        h = int(q)
        if 144 <= h <= 4320:
            return str(h)
    return "best"


def is_audio_quality(quality: str) -> bool:
    return normalize_quality(quality) == "audio"


def resolve_ydl_format(*, quality: str, url: str, is_tiktok: bool) -> str:
    """Return a yt-dlp format selector string for the chosen quality."""
    q = normalize_quality(quality)
    if q == "audio":
        return "ba/b"

    if q.isdigit():
        h = int(q)
        if is_tiktok:
            return f"b[height<=?{h}]/bv*[height<=?{h}]+ba/b"
        return f"bv*[height<=?{h}]+ba/b[height<=?{h}]/bv*[height<=?{h}]+ba/b"

    # best
    if is_tiktok:
        return "b/bv*+ba/b"
    return "bv*+ba/b"


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
    return [
        {
            "key": "FFmpegExtractAudio",
            "preferredcodec": "m4a",
            "preferredquality": "192",
        }
    ]


def _fmt_size_mb(fmt: dict[str, Any]) -> Optional[float]:
    for key in ("filesize", "filesize_approx"):
        val = fmt.get(key)
        if isinstance(val, (int, float)) and val > 0:
            return float(val) / (1024 * 1024)
    return None


def _snap_height(height: int) -> int:
    """Snap an exact stream height to the nearest common label at or below it."""
    for bucket in _DISPLAY_HEIGHTS:
        if height >= bucket:
            return bucket
    return height


def summarize_available_qualities(info: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Build a clean quality list from yt-dlp `formats`.

    Returns items like:
      {id, label, height, estimated_size_mb, fps, has_video, has_audio}
    """
    formats = info.get("formats") or []
    if not isinstance(formats, list):
        formats = []

    # height -> best size / fps among video streams
    video_by_height: dict[int, dict[str, Any]] = {}
    has_audio = False
    audio_size: Optional[float] = None

    for fmt in formats:
        if not isinstance(fmt, dict):
            continue
        vcodec = str(fmt.get("vcodec") or "none")
        acodec = str(fmt.get("acodec") or "none")
        height = fmt.get("height")
        size = _fmt_size_mb(fmt)
        fps = fmt.get("fps")

        if acodec != "none" and vcodec == "none":
            has_audio = True
            if size is not None and (audio_size is None or size < audio_size):
                audio_size = size

        if vcodec != "none" and isinstance(height, (int, float)) and height > 0:
            raw_h = int(height)
            if raw_h < 144:
                continue
            h = _snap_height(raw_h)
            prev = video_by_height.get(h)
            if prev is None:
                video_by_height[h] = {
                    "height": h,
                    "estimated_size_mb": size,
                    "fps": int(fps) if isinstance(fps, (int, float)) and fps > 0 else None,
                }
            else:
                # Prefer richer size estimate; keep higher fps label
                if size is not None and (
                    prev.get("estimated_size_mb") is None or size > prev["estimated_size_mb"]
                ):
                    prev["estimated_size_mb"] = size
                if isinstance(fps, (int, float)) and fps > 0:
                    prev_fps = prev.get("fps") or 0
                    if fps > prev_fps:
                        prev["fps"] = int(fps)

        # Progressive (video+audio in one): counts as video height and implies audio
        if vcodec != "none" and acodec != "none":
            has_audio = True

    # Also check top-level duration-based heuristics: info may only expose height
    top_h = info.get("height")
    if isinstance(top_h, (int, float)) and top_h > 0 and not video_by_height:
        h = _snap_height(int(top_h))
        video_by_height[h] = {
            "height": h,
            "estimated_size_mb": _estimate_info_size(info),
            "fps": None,
        }

    qualities: list[dict[str, Any]] = []

    if video_by_height:
        max_h = max(video_by_height)
        best_size = video_by_height[max_h].get("estimated_size_mb")
        qualities.append(
            {
                "id": "best",
                "label": "Best",
                "height": max_h,
                "estimated_size_mb": round(best_size, 1) if isinstance(best_size, float) else None,
                "fps": video_by_height[max_h].get("fps"),
                "has_video": True,
                "has_audio": has_audio,
            }
        )
        for h in sorted(video_by_height.keys(), reverse=True):
            meta = video_by_height[h]
            size = meta.get("estimated_size_mb")
            qualities.append(
                {
                    "id": str(h),
                    "label": f"{h}p",
                    "height": h,
                    "estimated_size_mb": round(size, 1) if isinstance(size, float) else None,
                    "fps": meta.get("fps"),
                    "has_video": True,
                    "has_audio": has_audio,
                }
            )

    if has_audio or not video_by_height:
        # Always offer audio when we detected an audio stream; if formats are sparse,
        # still offer audio-only as a safe option when site is known media.
        qualities.append(
            {
                "id": "audio",
                "label": "Audio only",
                "height": None,
                "estimated_size_mb": round(audio_size, 1) if isinstance(audio_size, float) else None,
                "fps": None,
                "has_video": False,
                "has_audio": True,
            }
        )

    # Cap list length for UI (Best + heights + audio)
    if len(qualities) > 10:
        # Keep Best, top heights, audio
        head = [q for q in qualities if q["id"] == "best"]
        audio = [q for q in qualities if q["id"] == "audio"]
        mids = [q for q in qualities if q["id"] not in ("best", "audio")][:8]
        qualities = head + mids + audio

    return qualities


def _estimate_info_size(info: dict[str, Any]) -> Optional[float]:
    for key in ("filesize", "filesize_approx"):
        val = info.get(key)
        if isinstance(val, (int, float)) and val > 0:
            return float(val) / (1024 * 1024)
    return None
