"""Server-side media trim + waveform peaks via ffmpeg (args list only — never shell=True)."""

from __future__ import annotations

import logging
import shutil
import struct
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

AUDIO_EXTENSIONS = {".mp3", ".m4a", ".aac", ".wav", ".ogg", ".opus", ".flac"}
VIDEO_EXTENSIONS = {".mp4", ".webm", ".mkv", ".mov", ".m4v"}
TRIMMABLE_EXTENSIONS = AUDIO_EXTENSIONS | VIDEO_EXTENSIONS

# Wall-clock caps for ffmpeg work
TRIM_TIMEOUT_AUDIO = 120
TRIM_TIMEOUT_VIDEO = 480
EDIT_TIMEOUT_STITCH = 600
EDIT_TIMEOUT_INSERT = 600
PEAKS_TIMEOUT = 90
# Re-encode path: keep clips bounded so API workers stay healthy
MAX_REENCODE_SECONDS = 15 * 60
MIN_TRIM_SECONDS = 0.25
# Production edit guards (source / output / segment count / insert size)
MAX_EDIT_SOURCE_SECONDS = 60 * 60
MAX_EDIT_OUTPUT_SECONDS = 30 * 60
MAX_EDIT_SEGMENTS = 20
MAX_INSERT_FILE_BYTES = 200 * 1024 * 1024
MAX_CROSSFADE_SECONDS = 2.0
# Low-rate mono PCM for peak extraction (keeps memory tiny)
PEAKS_SAMPLE_RATE = 8000
# Redis TTL for cached waveform peaks (file TTL is usually shorter; this is a ceiling)
WAVEFORM_CACHE_TTL_SECONDS = 6 * 60 * 60

ProgressCallback = Callable[[float], None]
CancelCheck = Callable[[], bool]


class MediaEditError(Exception):
    """User-facing trim / probe / edit failure."""


class MediaEditCancelled(MediaEditError):
    """Edit aborted by cancel flag."""


@dataclass(frozen=True)
class MediaProbe:
    duration: float
    has_video: bool
    has_audio: bool
    size_bytes: int


def is_audio_file(path: Path | str) -> bool:
    return Path(path).suffix.lower() in AUDIO_EXTENSIONS


def is_video_file(path: Path | str) -> bool:
    return Path(path).suffix.lower() in VIDEO_EXTENSIONS


def is_trimmable_file(path: Path | str) -> bool:
    return Path(path).suffix.lower() in TRIMMABLE_EXTENSIONS


def media_type_for(path: Path | str) -> str:
    ext = Path(path).suffix.lower()
    return {
        ".mp3": "audio/mpeg",
        ".m4a": "audio/mp4",
        ".aac": "audio/aac",
        ".wav": "audio/wav",
        ".ogg": "audio/ogg",
        ".opus": "audio/ogg",
        ".flac": "audio/flac",
        ".webm": "video/webm",
        ".mp4": "video/mp4",
        ".m4v": "video/mp4",
        ".mov": "video/quicktime",
        ".mkv": "video/x-matroska",
    }.get(ext, "application/octet-stream")


def _run(
    cmd: list[str],
    *,
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            shell=False,
        )
    except FileNotFoundError as exc:
        raise MediaEditError("Media tools (ffmpeg) are not available on the server.") from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaEditError("Processing took too long. Try a shorter clip.") from exc


def _run_ffmpeg_progress(
    cmd: list[str],
    *,
    timeout: int,
    output_duration: float,
    on_progress: ProgressCallback | None,
    cancel_check: CancelCheck | None = None,
) -> subprocess.CompletedProcess[str]:
    """
    Run ffmpeg with -progress pipe:1 and report 0..1 fraction based on out_time.
    """
    if not cmd or cmd[0] != "ffmpeg":
        return _run(cmd, timeout=timeout)

    # Insert progress flags before the output path (last arg)
    full_cmd = [*cmd[:-1], "-progress", "pipe:1", "-nostats", cmd[-1]]
    try:
        proc = subprocess.Popen(
            full_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            shell=False,
        )
    except FileNotFoundError as exc:
        raise MediaEditError("Media tools (ffmpeg) are not available on the server.") from exc

    deadline = time.monotonic() + timeout
    last_frac = 0.0
    assert proc.stdout is not None

    def _kill() -> None:
        if proc.poll() is None:
            proc.kill()
            try:
                proc.wait(timeout=5)
            except Exception:
                pass

    try:
        while True:
            if cancel_check and cancel_check():
                _kill()
                raise MediaEditCancelled("Edit cancelled.")
            if time.monotonic() > deadline:
                _kill()
                raise MediaEditError("Processing took too long. Try a shorter clip.")

            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    break
                time.sleep(0.05)
                continue

            line = line.strip()
            if line.startswith("out_time_ms=") and on_progress and output_duration > 0:
                raw = line.split("=", 1)[1].strip()
                try:
                    ms = int(raw)
                except ValueError:
                    continue
                if ms < 0:
                    continue
                frac = min(1.0, max(0.0, (ms / 1_000_000.0) / output_duration))
                if frac >= last_frac + 0.01 or frac >= 0.99:
                    last_frac = frac
                    on_progress(frac)
            elif line == "progress=end" and on_progress:
                last_frac = 1.0
                on_progress(1.0)

        stderr = proc.stderr.read() if proc.stderr else ""
        return subprocess.CompletedProcess(
            args=full_cmd,
            returncode=proc.returncode if proc.returncode is not None else 1,
            stdout="",
            stderr=stderr or "",
        )
    finally:
        _kill()


def probe_media(path: Path) -> MediaProbe:
    """Duration + stream presence via ffprobe."""
    if not path.is_file():
        raise MediaEditError("File not found.")

    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration:stream=codec_type",
        "-of",
        "json",
        str(path),
    ]
    result = _run(cmd, timeout=30)
    if result.returncode != 0:
        logger.warning("ffprobe_failed stderr=%s", (result.stderr or "")[:300])
        raise MediaEditError("Could not read media duration.")

    import json

    try:
        data = json.loads(result.stdout or "{}")
        duration = float(data.get("format", {}).get("duration") or 0)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise MediaEditError("Could not read media duration.") from exc

    if duration <= 0:
        raise MediaEditError("Media has no measurable duration.")

    streams = data.get("streams") or []
    has_video = any(s.get("codec_type") == "video" for s in streams)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    # Some audio-only containers still report oddly — fall back to extension
    if not has_video and not has_audio:
        has_audio = is_audio_file(path)
        has_video = is_video_file(path)

    try:
        size_bytes = path.stat().st_size
    except OSError:
        size_bytes = 0

    return MediaProbe(
        duration=duration,
        has_video=has_video,
        has_audio=has_audio,
        size_bytes=size_bytes,
    )


def probe_duration_seconds(path: Path) -> float:
    return probe_media(path).duration


def waveform_cache_key(path: Path, *, bar_count: int) -> str:
    """Stable cache key: path + size + mtime + bar count."""
    resolved = str(path.resolve())
    try:
        st = path.stat()
        return f"waveform:v1:{resolved}:{st.st_size}:{st.st_mtime_ns}:{bar_count}"
    except OSError:
        return f"waveform:v1:{resolved}:missing:{bar_count}"


def compute_peaks(path: Path, *, bar_count: int = 128) -> tuple[list[float], MediaProbe]:
    """
    Extract normalized 0–1 peak bars from the audio stream (works for video too).

    Streams low-rate mono PCM so long videos never load fully into RAM.
    """
    bars = max(16, min(int(bar_count), 256))
    probe = probe_media(path)
    if not probe.has_audio:
        return [0.15] * bars, probe

    # Estimate samples; ffmpeg may differ slightly
    est_samples = max(bars, int(probe.duration * PEAKS_SAMPLE_RATE))
    block = max(1, est_samples // bars)

    with tempfile.NamedTemporaryFile(suffix=".f32", delete=False) as tmp:
        pcm_path = Path(tmp.name)

    try:
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(PEAKS_SAMPLE_RATE),
            "-f",
            "f32le",
            str(pcm_path),
        ]
        result = _run(cmd, timeout=PEAKS_TIMEOUT)
        if result.returncode != 0 or not pcm_path.is_file():
            logger.warning("peaks_ffmpeg_failed stderr=%s", (result.stderr or "")[:300])
            raise MediaEditError("Could not build waveform.")

        peaks = [0.0] * bars
        idx = 0
        peak = 0.0
        bar_i = 0
        chunk = 8192 * 4  # 8k floats
        with pcm_path.open("rb") as fh:
            while True:
                raw = fh.read(chunk)
                if not raw:
                    break
                n = len(raw) // 4
                if n <= 0:
                    break
                samples = struct.unpack(f"<{n}f", raw[: n * 4])
                for v in samples:
                    av = abs(v)
                    if av > peak:
                        peak = av
                    idx += 1
                    if idx >= block and bar_i < bars:
                        peaks[bar_i] = peak
                        bar_i += 1
                        peak = 0.0
                        idx = 0
                        if bar_i >= bars:
                            # Drain rest without storing
                            fh.read()
                            break
        if bar_i < bars and peak > 0:
            peaks[bar_i] = peak
            bar_i += 1
        # Fill any unused bars
        while bar_i < bars:
            peaks[bar_i] = peaks[bar_i - 1] if bar_i else 0.12
            bar_i += 1

        max_p = max(peaks) if peaks else 0.0
        if max_p > 0:
            peaks = [min(1.0, p / max_p) for p in peaks]
        else:
            peaks = [0.12] * bars
        return peaks, probe
    finally:
        pcm_path.unlink(missing_ok=True)


def get_or_compute_peaks(
    path: Path,
    *,
    bar_count: int = 128,
    cache_get: Callable[[str], str | None] | None = None,
    cache_set: Callable[[str, str, int], None] | None = None,
    cache_ttl: int = WAVEFORM_CACHE_TTL_SECONDS,
) -> tuple[list[float], MediaProbe]:
    """
    Return peaks + probe, using an optional string cache (Redis).
    Cache miss still runs compute_peaks once.
    """
    import json

    bars = max(16, min(int(bar_count), 256))
    key = waveform_cache_key(path, bar_count=bars)

    if cache_get is not None:
        try:
            raw = cache_get(key)
            if raw:
                data = json.loads(raw)
                peaks = [float(x) for x in data["peaks"]]
                probe = MediaProbe(
                    duration=float(data["duration"]),
                    has_video=bool(data["has_video"]),
                    has_audio=bool(data["has_audio"]),
                    size_bytes=int(data.get("size_bytes") or 0),
                )
                if len(peaks) == bars and probe.duration > 0:
                    return peaks, probe
        except Exception:
            logger.debug("waveform_cache_miss_or_corrupt key=%s", key[:80])

    peaks, probe = compute_peaks(path, bar_count=bars)

    if cache_set is not None:
        try:
            payload = json.dumps(
                {
                    "peaks": peaks,
                    "duration": probe.duration,
                    "has_video": probe.has_video,
                    "has_audio": probe.has_audio,
                    "size_bytes": probe.size_bytes,
                },
                separators=(",", ":"),
            )
            cache_set(key, payload, max(60, int(cache_ttl)))
        except Exception:
            logger.debug("waveform_cache_set_failed", exc_info=True)

    return peaks, probe


def _validate_range(start: float, end: float, duration: float) -> tuple[float, float]:
    if start < 0:
        raise MediaEditError("Start time cannot be negative.")
    if end <= start:
        raise MediaEditError("End time must be after start time.")
    if start >= duration:
        raise MediaEditError("Start time is past the end of the media.")
    end = min(end, duration)
    if end - start < MIN_TRIM_SECONDS:
        raise MediaEditError(f"Select at least {MIN_TRIM_SECONDS} seconds to keep.")
    return start, end


def assert_edit_source_ok(probe: MediaProbe, *, path: Path | None = None) -> None:
    """Reject edits that would overload workers."""
    if probe.duration > MAX_EDIT_SOURCE_SECONDS:
        raise MediaEditError(
            f"Editing is limited to sources under {MAX_EDIT_SOURCE_SECONDS // 60} minutes."
        )
    if path is not None and path.is_file():
        try:
            size = path.stat().st_size
        except OSError:
            size = probe.size_bytes
        # Soft ceiling: 1.25× download max (allow already-downloaded large files)
        soft_max = max(MAX_INSERT_FILE_BYTES, int(probe.size_bytes or 0))
        if size > soft_max and size > 600 * 1024 * 1024:
            raise MediaEditError("File is too large to edit on the server.")


def assert_output_duration_ok(seconds: float) -> None:
    if seconds > MAX_EDIT_OUTPUT_SECONDS:
        raise MediaEditError(
            f"Edited output is limited to {MAX_EDIT_OUTPUT_SECONDS // 60} minutes. "
            "Keep fewer or shorter segments."
        )
    if seconds < MIN_TRIM_SECONDS:
        raise MediaEditError(f"Select at least {MIN_TRIM_SECONDS} seconds to keep.")


def _raise_if_cancelled(cancel_check: CancelCheck | None) -> None:
    if cancel_check and cancel_check():
        raise MediaEditCancelled("Edit cancelled.")


def _video_audio_encode_args(ext: str) -> tuple[list[str], list[str]]:
    if ext == ".webm":
        vcodec = ["-c:v", "libvpx-vp9", "-crf", "32", "-b:v", "0", "-row-mt", "1"]
        acodec = ["-c:a", "libopus", "-b:a", "128k"]
    else:
        vcodec = [
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
        ]
        acodec = ["-c:a", "aac", "-b:a", "192k"]
    return vcodec, acodec


def _audio_encode_args(ext: str) -> list[str]:
    if ext == ".mp3":
        return ["-c:a", "libmp3lame", "-q:a", "2"]
    if ext in {".m4a", ".aac"}:
        return ["-c:a", "aac", "-b:a", "192k"]
    if ext == ".wav":
        return ["-c:a", "pcm_s16le"]
    if ext in {".ogg", ".opus"}:
        return ["-c:a", "libopus", "-b:a", "128k"]
    return ["-c:a", "aac", "-b:a", "192k"]


def _atomic_replace(source: Path, tmp: Path) -> None:
    backup = source.with_name(f"{source.stem}.bak{source.suffix}")
    try:
        if backup.exists():
            backup.unlink(missing_ok=True)
        shutil.move(str(source), str(backup))
        shutil.move(str(tmp), str(source))
        backup.unlink(missing_ok=True)
    except OSError as exc:
        if backup.is_file() and not source.is_file():
            shutil.move(str(backup), str(source))
        tmp.unlink(missing_ok=True)
        raise MediaEditError("Could not save trimmed file.") from exc


def _trim_audio(
    source: Path,
    tmp: Path,
    start: float,
    end: float,
    *,
    on_progress: ProgressCallback | None = None,
    cancel_check: CancelCheck | None = None,
) -> None:
    timeout = TRIM_TIMEOUT_AUDIO
    duration_sel = max(0.01, end - start)
    copy_cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-ss",
        f"{start:.3f}",
        "-to",
        f"{end:.3f}",
        "-map",
        "0:a:0",
        "-c",
        "copy",
        str(tmp),
    ]
    result = _run_ffmpeg_progress(
        copy_cmd,
        timeout=timeout,
        output_duration=duration_sel,
        on_progress=on_progress,
        cancel_check=cancel_check,
    )
    if result.returncode == 0 and tmp.is_file() and tmp.stat().st_size >= 64:
        return

    tmp.unlink(missing_ok=True)
    encode = _audio_encode_args(source.suffix.lower())

    encode_cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-ss",
        f"{start:.3f}",
        "-to",
        f"{end:.3f}",
        "-map",
        "0:a:0",
        *encode,
        str(tmp),
    ]
    result = _run_ffmpeg_progress(
        encode_cmd,
        timeout=timeout,
        output_duration=duration_sel,
        on_progress=on_progress,
        cancel_check=cancel_check,
    )
    if result.returncode != 0 or not tmp.is_file():
        err = (result.stderr or result.stdout or "ffmpeg failed").strip()
        logger.warning("ffmpeg_audio_trim_failed err=%s", err[:400])
        tmp.unlink(missing_ok=True)
        raise MediaEditError("Could not trim this audio. Try again.")


def _trim_video(
    source: Path,
    tmp: Path,
    start: float,
    end: float,
    *,
    precise: bool = False,
    on_progress: ProgressCallback | None = None,
    cancel_check: CancelCheck | None = None,
) -> None:
    """
    Production-fast video trim:
    1) Stream copy (near-instant; may snap to keyframes) unless precise=True
    2) Fast re-encode (-ss before -i)
    3) Accurate re-encode (-ss after -i) for short/medium clips
    """
    duration_sel = end - start
    timeout = TRIM_TIMEOUT_VIDEO
    ext = source.suffix.lower()
    vcodec, acodec = _video_audio_encode_args(ext)

    def copy_streams() -> subprocess.CompletedProcess[str]:
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{start:.3f}",
            "-i",
            str(source),
            "-t",
            f"{duration_sel:.3f}",
            "-map",
            "0:v:0?",
            "-map",
            "0:a:0?",
            "-c",
            "copy",
            "-avoid_negative_ts",
            "make_zero",
            "-movflags",
            "+faststart",
            str(tmp),
        ]
        return _run_ffmpeg_progress(
            cmd,
            timeout=timeout,
            output_duration=duration_sel,
            on_progress=on_progress,
            cancel_check=cancel_check,
        )

    def encode(accurate: bool) -> subprocess.CompletedProcess[str]:
        # accurate=True: -ss after -i (frame-accurate, slower seek)
        # accurate=False: -ss before -i (fast)
        if accurate:
            cmd = [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(source),
                "-ss",
                f"{start:.3f}",
                "-t",
                f"{duration_sel:.3f}",
                "-map",
                "0:v:0?",
                "-map",
                "0:a:0?",
                *vcodec,
                *acodec,
                "-movflags",
                "+faststart",
                str(tmp),
            ]
        else:
            cmd = [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-ss",
                f"{start:.3f}",
                "-i",
                str(source),
                "-t",
                f"{duration_sel:.3f}",
                "-map",
                "0:v:0?",
                "-map",
                "0:a:0?",
                *vcodec,
                *acodec,
                "-movflags",
                "+faststart",
                str(tmp),
            ]
        return _run_ffmpeg_progress(
            cmd,
            timeout=timeout,
            output_duration=duration_sel,
            on_progress=on_progress,
            cancel_check=cancel_check,
        )

    def ok() -> bool:
        return tmp.is_file() and tmp.stat().st_size >= 1024

    # Default / long clips: stream copy first (production-fast)
    if not precise:
        result = copy_streams()
        if result.returncode == 0 and ok():
            return
        tmp.unlink(missing_ok=True)

    # Re-encode path (precise mode, or copy failed)
    if duration_sel > MAX_REENCODE_SECONDS and precise:
        tmp.unlink(missing_ok=True)
        raise MediaEditError(
            "Exact cut is limited to "
            f"{MAX_REENCODE_SECONDS // 60} minutes. "
            "Turn off Exact cut or select a shorter range."
        )

    # Fast seek re-encode first, then accurate for short/medium clips
    result = encode(accurate=False)
    if result.returncode == 0 and ok():
        return
    tmp.unlink(missing_ok=True)

    if duration_sel <= MAX_REENCODE_SECONDS:
        result = encode(accurate=True)
        if result.returncode == 0 and ok():
            return
        err = (result.stderr or result.stdout or "ffmpeg failed").strip()
        logger.warning("ffmpeg_video_encode_failed err=%s", err[:400])
        tmp.unlink(missing_ok=True)
        raise MediaEditError("Could not trim this video. Try a slightly different range.")

    tmp.unlink(missing_ok=True)
    raise MediaEditError(
        "Stream copy failed and this clip is too long to re-encode. "
        f"Try keeping under {MAX_REENCODE_SECONDS // 60} minutes."
    )


def trim_media_file(
    source: Path,
    *,
    start_seconds: float,
    end_seconds: float,
    dest: Path | None = None,
    precise: bool = False,
    on_progress: ProgressCallback | None = None,
    cancel_check: CancelCheck | None = None,
) -> MediaProbe:
    """
    Trim audio or video to [start_seconds, end_seconds).

    If dest is None, replaces source in place (legacy).
    Otherwise writes the trimmed file to dest and leaves source untouched.

    precise=True forces video re-encode (frame-accurate); default is copy-first.
    """
    if not source.is_file():
        raise MediaEditError("File not found.")
    if not is_trimmable_file(source):
        raise MediaEditError("Trim is only available for audio and video files.")

    probe = probe_media(source)
    assert_edit_source_ok(probe, path=source)
    start, end = _validate_range(start_seconds, end_seconds, probe.duration)
    assert_output_duration_ok(end - start)
    out = dest if dest is not None else source

    tmp = out.with_name(f"{out.stem}.trimtmp{out.suffix}")
    if tmp.exists():
        tmp.unlink(missing_ok=True)

    def _report(frac: float) -> None:
        _raise_if_cancelled(cancel_check)
        if on_progress:
            on_progress(min(1.0, max(0.0, frac)))

    try:
        _report(0.02)
        if is_video_file(source) or probe.has_video:
            _trim_video(
                source,
                tmp,
                start,
                end,
                precise=precise,
                on_progress=_report,
                cancel_check=cancel_check,
            )
        else:
            _trim_audio(
                source,
                tmp,
                start,
                end,
                on_progress=_report,
                cancel_check=cancel_check,
            )
        if dest is not None:
            if out.exists() and out.resolve() != source.resolve():
                out.unlink(missing_ok=True)
            shutil.move(str(tmp), str(out))
        else:
            _atomic_replace(source, tmp)
        _report(1.0)
    except MediaEditError:
        tmp.unlink(missing_ok=True)
        raise
    except Exception:
        tmp.unlink(missing_ok=True)
        raise

    return probe_media(out)


# Back-compat alias used by older imports / tests
def trim_audio_file(
    source: Path,
    *,
    start_seconds: float,
    end_seconds: float,
) -> Path:
    if not is_audio_file(source):
        raise MediaEditError("Trim is only available for audio files.")
    trim_media_file(source, start_seconds=start_seconds, end_seconds=end_seconds)
    return source


def _concat_parts(
    parts: list[Path],
    dest: Path,
    *,
    is_video: bool,
    output_duration: float,
    on_progress: ProgressCallback | None,
    cancel_check: CancelCheck | None,
) -> None:
    """Concat demuxer (copy) then re-encode fallback."""
    if not parts:
        raise MediaEditError("Nothing to stitch.")
    if len(parts) == 1:
        if dest.exists():
            dest.unlink(missing_ok=True)
        shutil.copy2(parts[0], dest)
        if on_progress:
            on_progress(1.0)
        return

    list_path = dest.with_name(f"{dest.stem}.concat.txt")
    tmp = dest.with_name(f"{dest.stem}.stitchtmp{dest.suffix}")
    tmp.unlink(missing_ok=True)
    try:
        lines = []
        for p in parts:
            # Escape single quotes for concat demuxer
            escaped = str(p.resolve()).replace("'", r"'\''")
            lines.append(f"file '{escaped}'")
        list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        copy_cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_path),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(tmp),
        ]
        result = _run_ffmpeg_progress(
            copy_cmd,
            timeout=EDIT_TIMEOUT_STITCH,
            output_duration=max(0.1, output_duration),
            on_progress=on_progress,
            cancel_check=cancel_check,
        )
        min_size = 1024 if is_video else 64
        if result.returncode == 0 and tmp.is_file() and tmp.stat().st_size >= min_size:
            if dest.exists():
                dest.unlink(missing_ok=True)
            shutil.move(str(tmp), str(dest))
            return

        tmp.unlink(missing_ok=True)
        ext = dest.suffix.lower()
        if is_video:
            vcodec, acodec = _video_audio_encode_args(ext)
            encode_extra = [*vcodec, *acodec, "-movflags", "+faststart"]
        else:
            encode_extra = [*_audio_encode_args(ext)]

        encode_cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_path),
            *encode_extra,
            str(tmp),
        ]
        result = _run_ffmpeg_progress(
            encode_cmd,
            timeout=EDIT_TIMEOUT_STITCH,
            output_duration=max(0.1, output_duration),
            on_progress=on_progress,
            cancel_check=cancel_check,
        )
        if result.returncode != 0 or not tmp.is_file() or tmp.stat().st_size < min_size:
            err = (result.stderr or result.stdout or "ffmpeg failed").strip()
            logger.warning("ffmpeg_stitch_failed err=%s", err[:400])
            tmp.unlink(missing_ok=True)
            raise MediaEditError("Could not stitch segments. Try Exact cut or fewer cuts.")
        if dest.exists():
            dest.unlink(missing_ok=True)
        shutil.move(str(tmp), str(dest))
    finally:
        list_path.unlink(missing_ok=True)
        tmp.unlink(missing_ok=True)


def stitch_media_ranges(
    source: Path,
    ranges: list[tuple[float, float]],
    *,
    dest: Path,
    precise: bool = False,
    on_progress: ProgressCallback | None = None,
    cancel_check: CancelCheck | None = None,
) -> MediaProbe:
    """
    Keep multiple ranges from source (in the given order) and stitch into dest.

    Order is output order (supports reorder). Ranges may be non-contiguous.
    """
    if not source.is_file():
        raise MediaEditError("File not found.")
    if not is_trimmable_file(source):
        raise MediaEditError("Edit is only available for audio and video files.")
    if not ranges:
        raise MediaEditError("Add at least one segment to keep.")
    if len(ranges) > MAX_EDIT_SEGMENTS:
        raise MediaEditError(f"At most {MAX_EDIT_SEGMENTS} segments allowed.")

    probe = probe_media(source)
    assert_edit_source_ok(probe, path=source)

    validated: list[tuple[float, float]] = []
    total = 0.0
    for start, end in ranges:
        s, e = _validate_range(start, end, probe.duration)
        validated.append((s, e))
        total += e - s
    assert_output_duration_ok(total)

    if len(validated) == 1:
        s, e = validated[0]
        return trim_media_file(
            source,
            start_seconds=s,
            end_seconds=e,
            dest=dest,
            precise=precise,
            on_progress=on_progress,
            cancel_check=cancel_check,
        )

    is_video = is_video_file(source) or probe.has_video
    work_dir = Path(tempfile.mkdtemp(prefix="stitch_", dir=str(dest.parent)))
    parts: list[Path] = []

    def _map_progress(base: float, span: float) -> ProgressCallback:
        def _inner(frac: float) -> None:
            if on_progress:
                on_progress(min(0.95, base + span * min(1.0, max(0.0, frac))))

        return _inner

    try:
        n = len(validated)
        extract_span = 0.75
        for i, (s, e) in enumerate(validated):
            _raise_if_cancelled(cancel_check)
            part = work_dir / f"part_{i:03d}{source.suffix}"
            trim_media_file(
                source,
                start_seconds=s,
                end_seconds=e,
                dest=part,
                precise=precise,
                on_progress=_map_progress(i * (extract_span / n), extract_span / n),
                cancel_check=cancel_check,
            )
            parts.append(part)

        def _concat_prog(frac: float) -> None:
            if on_progress:
                on_progress(min(1.0, 0.75 + 0.25 * min(1.0, max(0.0, frac))))

        _concat_parts(
            parts,
            dest,
            is_video=is_video,
            output_duration=total,
            on_progress=_concat_prog,
            cancel_check=cancel_check,
        )
        if on_progress:
            on_progress(1.0)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    return probe_media(dest)


def insert_media_at(
    base: Path,
    insert_path: Path,
    *,
    at_seconds: float,
    dest: Path,
    replace_audio: bool = False,
    crossfade_seconds: float = 0.0,
    on_progress: ProgressCallback | None = None,
    cancel_check: CancelCheck | None = None,
) -> MediaProbe:
    """
    Insert another media file at at_seconds, or replace the base audio track.

    crossfade_seconds > 0 forces a re-encode join (video xfade / audio acrossfade).
    """
    if not base.is_file() or not insert_path.is_file():
        raise MediaEditError("File not found.")
    if not is_trimmable_file(base):
        raise MediaEditError("Edit is only available for audio and video files.")
    if not is_trimmable_file(insert_path):
        raise MediaEditError("Inserted file must be audio or video.")

    try:
        insert_size = insert_path.stat().st_size
    except OSError as exc:
        raise MediaEditError("Could not read insert file.") from exc
    if insert_size > MAX_INSERT_FILE_BYTES:
        raise MediaEditError(
            f"Insert file is too large (max {MAX_INSERT_FILE_BYTES // (1024 * 1024)} MB)."
        )

    base_probe = probe_media(base)
    insert_probe = probe_media(insert_path)
    assert_edit_source_ok(base_probe, path=base)
    assert_edit_source_ok(insert_probe, path=insert_path)

    at = float(at_seconds)
    if at < 0 or at > base_probe.duration:
        raise MediaEditError("Insert point is outside the media duration.")

    fade = max(0.0, min(float(crossfade_seconds), MAX_CROSSFADE_SECONDS))
    is_video = is_video_file(base) or base_probe.has_video

    if replace_audio:
        if not insert_probe.has_audio and not is_audio_file(insert_path):
            raise MediaEditError("Insert file has no audio to replace with.")
        if is_video and not base_probe.has_video:
            raise MediaEditError("Base file has no video track.")
        assert_output_duration_ok(base_probe.duration)
        return _replace_audio_track(
            base,
            insert_path,
            dest=dest,
            duration=base_probe.duration,
            on_progress=on_progress,
            cancel_check=cancel_check,
        )

    # Hard insert: left | insert | right
    out_dur = base_probe.duration + insert_probe.duration - (fade if fade > 0 else 0.0)
    # With two fades (left↔insert and insert↔right) subtract 2*fade when both sides exist
    left_ok = at >= MIN_TRIM_SECONDS
    right_ok = (base_probe.duration - at) >= MIN_TRIM_SECONDS
    if fade > 0:
        fades = (1 if left_ok else 0) + (1 if right_ok else 0)
        out_dur = base_probe.duration + insert_probe.duration - fade * fades
    assert_output_duration_ok(out_dur)

    work_dir = Path(tempfile.mkdtemp(prefix="insert_", dir=str(dest.parent)))
    try:
        parts: list[Path] = []
        step = 0
        total_steps = 1 + (1 if left_ok else 0) + (1 if right_ok else 0) + 1

        def _step_prog(local: float) -> None:
            if on_progress:
                on_progress(min(0.9, (step + local) / total_steps))

        if left_ok:
            left = work_dir / f"left{base.suffix}"
            trim_media_file(
                base,
                start_seconds=0,
                end_seconds=at,
                dest=left,
                precise=False,
                on_progress=_step_prog,
                cancel_check=cancel_check,
            )
            parts.append(left)
            step += 1

        mid = work_dir / f"mid{insert_path.suffix}"
        # Normalize insert container toward base suffix when possible via remux/encode
        if insert_path.suffix.lower() != base.suffix.lower():
            mid = work_dir / f"mid{base.suffix}"
            _transcode_match_container(
                insert_path,
                mid,
                is_video=is_video,
                duration=insert_probe.duration,
                on_progress=_step_prog,
                cancel_check=cancel_check,
            )
        else:
            shutil.copy2(insert_path, mid)
            _step_prog(1.0)
        parts.append(mid)
        step += 1

        if right_ok:
            right = work_dir / f"right{base.suffix}"
            trim_media_file(
                base,
                start_seconds=at,
                end_seconds=base_probe.duration,
                dest=right,
                precise=False,
                on_progress=_step_prog,
                cancel_check=cancel_check,
            )
            parts.append(right)
            step += 1

        if fade > 0 and len(parts) >= 2:
            _concat_with_crossfade(
                parts,
                dest,
                is_video=is_video,
                fade=fade,
                output_duration=out_dur,
                on_progress=lambda f: on_progress(min(1.0, 0.9 + 0.1 * f)) if on_progress else None,
                cancel_check=cancel_check,
            )
        else:
            _concat_parts(
                parts,
                dest,
                is_video=is_video,
                output_duration=out_dur,
                on_progress=lambda f: on_progress(min(1.0, 0.9 + 0.1 * f)) if on_progress else None,
                cancel_check=cancel_check,
            )
        if on_progress:
            on_progress(1.0)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    return probe_media(dest)


def _transcode_match_container(
    source: Path,
    dest: Path,
    *,
    is_video: bool,
    duration: float,
    on_progress: ProgressCallback | None,
    cancel_check: CancelCheck | None,
) -> None:
    ext = dest.suffix.lower()
    if is_video:
        vcodec, acodec = _video_audio_encode_args(ext)
        extras = [*vcodec, *acodec, "-movflags", "+faststart"]
        maps = ["-map", "0:v:0?", "-map", "0:a:0?"]
    else:
        extras = _audio_encode_args(ext)
        maps = ["-map", "0:a:0"]
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        *maps,
        *extras,
        str(dest),
    ]
    result = _run_ffmpeg_progress(
        cmd,
        timeout=EDIT_TIMEOUT_INSERT,
        output_duration=max(0.1, duration),
        on_progress=on_progress,
        cancel_check=cancel_check,
    )
    if result.returncode != 0 or not dest.is_file():
        dest.unlink(missing_ok=True)
        raise MediaEditError("Could not convert insert file to match the download format.")


def _replace_audio_track(
    base: Path,
    audio_src: Path,
    *,
    dest: Path,
    duration: float,
    on_progress: ProgressCallback | None,
    cancel_check: CancelCheck | None,
) -> MediaProbe:
    ext = dest.suffix.lower()
    tmp = dest.with_name(f"{dest.stem}.audiotmp{dest.suffix}")
    tmp.unlink(missing_ok=True)
    vcodec, acodec = _video_audio_encode_args(ext)
    # Keep video stream; re-encode audio to match container.
    if is_video_file(base):
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(base),
            "-i",
            str(audio_src),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            *acodec,
            "-shortest",
            "-movflags",
            "+faststart",
            str(tmp),
        ]
    else:
        # Audio-only base: just take insert audio (trimmed to duration)
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(audio_src),
            "-t",
            f"{duration:.3f}",
            *_audio_encode_args(ext),
            str(tmp),
        ]
    try:
        result = _run_ffmpeg_progress(
            cmd,
            timeout=EDIT_TIMEOUT_INSERT,
            output_duration=max(0.1, duration),
            on_progress=on_progress,
            cancel_check=cancel_check,
        )
        if result.returncode != 0 or not tmp.is_file():
            # Video copy may fail if codecs disagree — full re-encode
            tmp.unlink(missing_ok=True)
            if is_video_file(base):
                cmd = [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-i",
                    str(base),
                    "-i",
                    str(audio_src),
                    "-map",
                    "0:v:0",
                    "-map",
                    "1:a:0",
                    *vcodec,
                    *acodec,
                    "-shortest",
                    "-movflags",
                    "+faststart",
                    str(tmp),
                ]
                result = _run_ffmpeg_progress(
                    cmd,
                    timeout=EDIT_TIMEOUT_INSERT,
                    output_duration=max(0.1, duration),
                    on_progress=on_progress,
                    cancel_check=cancel_check,
                )
            if result.returncode != 0 or not tmp.is_file():
                err = (result.stderr or result.stdout or "ffmpeg failed").strip()
                logger.warning("ffmpeg_replace_audio_failed err=%s", err[:400])
                raise MediaEditError("Could not replace audio track.")
        if dest.exists():
            dest.unlink(missing_ok=True)
        shutil.move(str(tmp), str(dest))
    finally:
        tmp.unlink(missing_ok=True)
    if on_progress:
        on_progress(1.0)
    return probe_media(dest)


def _concat_with_crossfade(
    parts: list[Path],
    dest: Path,
    *,
    is_video: bool,
    fade: float,
    output_duration: float,
    on_progress: ProgressCallback | None,
    cancel_check: CancelCheck | None,
) -> None:
    """Pairwise xfade/acrossfade re-encode (slower, optional)."""
    if len(parts) < 2:
        _concat_parts(
            parts,
            dest,
            is_video=is_video,
            output_duration=output_duration,
            on_progress=on_progress,
            cancel_check=cancel_check,
        )
        return

    current = parts[0]
    work = dest.parent
    for i, nxt in enumerate(parts[1:]):
        _raise_if_cancelled(cancel_check)
        out = work / f"{dest.stem}.xfade_{i}{dest.suffix}"
        out.unlink(missing_ok=True)
        d0 = probe_duration_seconds(current)
        # Offset so fade overlaps the end of current
        offset = max(0.0, d0 - fade)
        ext = dest.suffix.lower()
        if is_video:
            vcodec, acodec = _video_audio_encode_args(ext)
            # Prefer xfade + acrossfade when both have streams; fall back to concat
            filter_complex = (
                f"[0:v][1:v]xfade=transition=fade:duration={fade:.3f}:offset={offset:.3f}[v];"
                f"[0:a][1:a]acrossfade=d={fade:.3f}[a]"
            )
            cmd = [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(current),
                "-i",
                str(nxt),
                "-filter_complex",
                filter_complex,
                "-map",
                "[v]",
                "-map",
                "[a]",
                *vcodec,
                *acodec,
                "-movflags",
                "+faststart",
                str(out),
            ]
        else:
            filter_complex = f"[0:a][1:a]acrossfade=d={fade:.3f}[a]"
            cmd = [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(current),
                "-i",
                str(nxt),
                "-filter_complex",
                filter_complex,
                "-map",
                "[a]",
                *_audio_encode_args(ext),
                str(out),
            ]
        result = _run_ffmpeg_progress(
            cmd,
            timeout=EDIT_TIMEOUT_INSERT,
            output_duration=max(0.1, output_duration),
            on_progress=on_progress,
            cancel_check=cancel_check,
        )
        if result.returncode != 0 or not out.is_file():
            # Fall back to hard concat of remaining chain
            logger.warning(
                "crossfade_failed falling_back_to_concat err=%s",
                (result.stderr or "")[:300],
            )
            out.unlink(missing_ok=True)
            rest = [current, nxt, *parts[i + 2 :]]
            _concat_parts(
                rest,
                dest,
                is_video=is_video,
                output_duration=output_duration,
                on_progress=on_progress,
                cancel_check=cancel_check,
            )
            return
        if current != parts[0] and current.exists() and current.parent == work:
            # Clean intermediate
            if current.name.startswith(dest.stem + ".xfade_"):
                current.unlink(missing_ok=True)
        current = out

    if dest.exists():
        dest.unlink(missing_ok=True)
    shutil.move(str(current), str(dest))


def restore_file_copy(source: Path, dest: Path) -> MediaProbe:
    """Copy source over dest (used for restore original / undo last)."""
    if not source.is_file():
        raise MediaEditError("Restore source file not found.")
    tmp = dest.with_name(f"{dest.stem}.restoretmp{dest.suffix}")
    try:
        shutil.copy2(source, tmp)
        if dest.exists():
            dest.unlink(missing_ok=True)
        shutil.move(str(tmp), str(dest))
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise MediaEditError("Could not restore file.") from exc
    return probe_media(dest)