"""Download quality / format presets for yt-dlp."""

from __future__ import annotations

from typing import Any, Literal, Optional

Quality = Literal["best", "1080", "720", "360", "audio"]
AudioFormat = Literal["m4a", "mp3"]

QUALITY_CHOICES: tuple[str, ...] = ("best", "1080", "720", "360", "audio")
AUDIO_FORMAT_CHOICES: tuple[AudioFormat, ...] = ("m4a", "mp3")


def normalize_quality(value: str | None) -> str:
    q = (value or "best").strip().lower()
    if q in ("best", "audio"):
        return q
    # "1080p" / "1080" / "1080p60"
    if q.endswith("p") and q[:-1].isdigit():
        return q[:-1]
    if "p" in q:
        left, _, right = q.partition("p")
        if left.isdigit() and (right == "" or right.isdigit()):
            h = int(left)
            if 144 <= h <= 4320:
                if right.isdigit() and int(right) >= 50:
                    return f"{h}p{int(right)}"
                return str(h)
    if q.isdigit():
        h = int(q)
        if 144 <= h <= 4320:
            return str(h)
    return "best"


def quality_is_allowed(value: str | None) -> bool:
    q = normalize_quality(value)
    if q in ("best", "audio"):
        return True
    if q.isdigit():
        return True
    if "p" in q:
        left, _, right = q.partition("p")
        return left.isdigit() and right.isdigit()
    return False


def height_from_quality(quality: str) -> int | None:
    q = normalize_quality(quality)
    if q.isdigit():
        return int(q)
    if "p" in q:
        left, _, _ = q.partition("p")
        if left.isdigit():
            return int(left)
    return None


def is_audio_quality(quality: str) -> bool:
    return normalize_quality(quality) == "audio"


def resolve_ydl_format(
    *,
    quality: str,
    url: str,
    is_tiktok: bool,
    is_facebook: bool = False,
) -> str:
    """Return a yt-dlp format selector string for the chosen quality."""
    q = normalize_quality(quality)
    if q == "audio":
        return "ba/b"

    # TikTok / Facebook often expose progressive streams more reliably than DASH.
    progressive_first = is_tiktok or is_facebook

    height_token: str | None = None
    fps_pref: int | None = None
    if q.isdigit():
        height_token = q
    elif "p" in q:
        left, _, right = q.partition("p")
        if left.isdigit():
            height_token = left
            if right.isdigit():
                fps_pref = int(right)

    if height_token:
        h = int(height_token)
        fps_filter = f"[fps<=?{fps_pref}]" if fps_pref else ""
        # Prefer exact height when available, else nearest at or below
        if progressive_first:
            return (
                f"b[height={h}]{fps_filter}/"
                f"b[height<=?{h}]{fps_filter}/"
                f"bv*[height={h}]{fps_filter}+ba/"
                f"bv*[height<=?{h}]{fps_filter}+ba/b"
            )
        return (
            f"bv*[height={h}]{fps_filter}+ba/"
            f"bv*[height<=?{h}]{fps_filter}+ba/"
            f"b[height={h}]{fps_filter}/"
            f"b[height<=?{h}]{fps_filter}/b"
        )

    # best
    if progressive_first:
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
    tbr = fmt.get("tbr")
    duration = fmt.get("duration")  # often missing on per-format
    if isinstance(tbr, (int, float)) and tbr > 0 and isinstance(duration, (int, float)):
        return (float(tbr) * 1000 / 8 * float(duration)) / (1024 * 1024)
    return None


def _quality_key(height: int, fps: int | None) -> str:
    """Stable id: '1080' or '1080p60' when high-fps."""
    if fps is not None and fps >= 50:
        return f"{height}p{int(fps)}"
    return str(height)


def _quality_label(height: int, fps: int | None) -> str:
    if fps is not None and fps >= 50:
        return f"{height}p{int(fps)}"
    return f"{height}p"


def summarize_available_qualities(info: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Build the full quality list from yt-dlp `formats` (every distinct height/fps).

    Returns items like:
      {id, label, height, estimated_size_mb, fps, has_video, has_audio}
    """
    formats = info.get("formats") or []
    if not isinstance(formats, list):
        formats = []

    # (height, fps_bucket) -> best meta
    # fps_bucket: None for standard (<=49), else exact high fps
    video_bucket: dict[tuple[int, int | None], dict[str, Any]] = {}
    has_audio = False
    audio_size: Optional[float] = None

    duration = info.get("duration")

    for fmt in formats:
        if not isinstance(fmt, dict):
            continue
        vcodec = str(fmt.get("vcodec") or "none")
        acodec = str(fmt.get("acodec") or "none")
        height = fmt.get("height")
        size = _fmt_size_mb(fmt)
        if size is None and isinstance(duration, (int, float)):
            # Estimate from tbr when filesize missing (common on YouTube)
            tbr = fmt.get("tbr")
            if isinstance(tbr, (int, float)) and tbr > 0:
                size = (float(tbr) * 1000 / 8 * float(duration)) / (1024 * 1024)
        fps_raw = fmt.get("fps")
        fps = int(fps_raw) if isinstance(fps_raw, (int, float)) and fps_raw > 0 else None

        if acodec != "none" and vcodec == "none":
            has_audio = True
            if size is not None and (audio_size is None or size < audio_size):
                audio_size = size

        if vcodec != "none" and isinstance(height, (int, float)) and height > 0:
            raw_h = int(height)
            if raw_h < 144:
                continue
            fps_key = fps if (fps is not None and fps >= 50) else None
            key = (raw_h, fps_key)
            prev = video_bucket.get(key)
            if prev is None:
                video_bucket[key] = {
                    "height": raw_h,
                    "fps": fps_key if fps_key is not None else fps,
                    "estimated_size_mb": size,
                }
            else:
                if size is not None and (
                    prev.get("estimated_size_mb") is None or size > prev["estimated_size_mb"]
                ):
                    prev["estimated_size_mb"] = size
                if fps is not None:
                    prev_fps = prev.get("fps") or 0
                    if fps > prev_fps and fps_key is None:
                        prev["fps"] = fps

        if vcodec != "none" and acodec != "none":
            has_audio = True

    top_h = info.get("height")
    if isinstance(top_h, (int, float)) and top_h > 0 and not video_bucket:
        h = int(top_h)
        video_bucket[(h, None)] = {
            "height": h,
            "estimated_size_mb": _estimate_info_size(info),
            "fps": None,
        }

    qualities: list[dict[str, Any]] = []

    if video_bucket:
        # Sort by height desc, then fps desc
        ordered = sorted(
            video_bucket.values(),
            key=lambda m: (m["height"], m.get("fps") or 0),
            reverse=True,
        )
        max_meta = ordered[0]
        max_h = max_meta["height"]
        best_size = max_meta.get("estimated_size_mb")
        qualities.append(
            {
                "id": "best",
                "label": f"Best ({max_h}p)",
                "height": max_h,
                "estimated_size_mb": round(best_size, 1) if isinstance(best_size, float) else None,
                "fps": max_meta.get("fps"),
                "has_video": True,
                "has_audio": has_audio,
            }
        )
        for meta in ordered:
            h = meta["height"]
            fps = meta.get("fps")
            # Prefer high-fps label only when >= 50
            label_fps = fps if isinstance(fps, int) and fps >= 50 else None
            qid = _quality_key(h, label_fps)
            size = meta.get("estimated_size_mb")
            qualities.append(
                {
                    "id": qid,
                    "label": _quality_label(h, label_fps),
                    "height": h,
                    "estimated_size_mb": round(size, 1) if isinstance(size, float) else None,
                    "fps": label_fps or fps,
                    "has_video": True,
                    "has_audio": has_audio,
                }
            )

    if has_audio or not video_bucket:
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

    return qualities


def _estimate_info_size(info: dict[str, Any]) -> Optional[float]:
    for key in ("filesize", "filesize_approx"):
        val = info.get(key)
        if isinstance(val, (int, float)) and val > 0:
            return float(val) / (1024 * 1024)
    return None
