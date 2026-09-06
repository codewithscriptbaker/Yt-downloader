"use client";

import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from "react";
import { createPortal } from "react-dom";
import {
  formatTrimTime,
  inlineMediaUrl,
  placeholderPeaks,
  waveformUrlFromDownload,
  MIN_TRIM_SECONDS,
  type MediaKind,
} from "@/lib/mediaTrim";
import {
  cancelJobEdit,
  composeJobMedia,
  fetchWaveform,
  getJob,
  insertJobMedia,
  trimJobMedia,
} from "@/lib/api";
import type { TrimJobResponse } from "@/lib/types";

type Segment = { id: string; start: number; end: number };

type Props = {
  jobId: string;
  downloadUrl: string;
  kind: MediaKind;
  fileLabel?: string | null;
  onCancel: () => void;
  onTrimmed: (result: TrimJobResponse) => void;
};

type DragHandle = "start" | "end" | "move" | null;

function newSegId() {
  return `s-${Math.random().toString(36).slice(2, 9)}`;
}

/**
 * Single CapCut-style media editor:
 * drag keep-ranges, split/delete/reorder, insert at playhead — one surface.
 */
export function MediaTrimEditor({
  jobId,
  downloadUrl,
  kind,
  fileLabel,
  onCancel,
  onTrimmed,
}: Props) {
  const titleId = useId();
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const trackRef = useRef<HTMLDivElement | null>(null);
  const mediaRef = useRef<HTMLMediaElement | null>(null);
  const peaksRef = useRef<Float32Array | null>(null);
  const durationReadyRef = useRef(false);
  const rafRef = useRef<number | null>(null);
  const dragRef = useRef<DragHandle>(null);
  const moveLenRef = useRef(0);
  const moveOffsetRef = useRef(0);
  const closeRef = useRef<HTMLButtonElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const [mounted, setMounted] = useState(false);
  const [loading, setLoading] = useState(true);
  const [peaksLoading, setPeaksLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [duration, setDuration] = useState(0);
  const [start, setStart] = useState(0);
  const [end, setEnd] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [playhead, setPlayhead] = useState(0);
  const [applying, setApplying] = useState(false);
  const [applyProgress, setApplyProgress] = useState(0);
  const [applyMessage, setApplyMessage] = useState<string | null>(null);
  const [applyError, setApplyError] = useState<string | null>(null);
  const [resolvedKind, setResolvedKind] = useState<MediaKind>(kind);
  const [precise, setPrecise] = useState(false);
  const [previewUrl, setPreviewUrl] = useState(() => inlineMediaUrl(downloadUrl));
  const [segments, setSegments] = useState<Segment[]>([]);
  const [selectedSegId, setSelectedSegId] = useState<string | null>(null);
  const [insertFile, setInsertFile] = useState<File | null>(null);
  const [replaceAudio, setReplaceAudio] = useState(false);
  const [crossfade, setCrossfade] = useState(0);

  const isVideo = resolvedKind === "video";
  const selectedSeg = segments.find((s) => s.id === selectedSegId) ?? null;
  const keepTotal = segments.reduce((acc, s) => acc + (s.end - s.start), 0);
  const selectionLen = Math.max(0, end - start);
  const atStart = start <= 0.02;
  const atEnd = end >= duration - 0.02;
  const fullSingle =
    segments.length <= 1 && start <= 0.02 && end >= duration - 0.02;

  const applyDuration = useCallback((secs: number) => {
    if (!Number.isFinite(secs) || secs <= 0) return;
    if (durationReadyRef.current) return;
    durationReadyRef.current = true;
    setDuration(secs);
    setStart(0);
    setEnd(secs);
    setPlayhead(0);
    const id = newSegId();
    setSegments([{ id, start: 0, end: secs }]);
    setSelectedSegId(id);
    setLoading(false);
  }, []);

  useEffect(() => {
    setMounted(true);
  }, []);

  useEffect(() => {
    if (!mounted) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    closeRef.current?.focus();
    return () => {
      document.body.style.overflow = prev;
    };
  }, [mounted]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !applying) onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [applying, onCancel]);

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    const peaks = peaksRef.current;
    if (!canvas || !peaks || duration <= 0) return;

    const keepRanges = segments.map((s) => ({ start: s.start, end: s.end }));

    const dpr = window.devicePixelRatio || 1;
    const cssW = canvas.clientWidth || 320;
    const cssH = canvas.clientHeight || (isVideo ? 56 : 72);
    if (canvas.width !== Math.floor(cssW * dpr) || canvas.height !== Math.floor(cssH * dpr)) {
      canvas.width = Math.floor(cssW * dpr);
      canvas.height = Math.floor(cssH * dpr);
    }
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cssW, cssH);

    const styles = getComputedStyle(canvas);
    const accent = styles.getPropertyValue("--accent").trim() || "#00a88a";
    const muted = styles.getPropertyValue("--muted").trim() || "#8a9399";
    const ink = styles.getPropertyValue("--ink").trim() || "#1a1f24";

    const mid = cssH / 2;
    const barGap = 1.5;
    const n = peaks.length;
    const barW = Math.max(1.5, (cssW - barGap * (n - 1)) / n);

    const inKeep = (xMid: number) => {
      const t = (xMid / cssW) * duration;
      return keepRanges.some((r) => t >= r.start && t <= r.end);
    };

    for (let i = 0; i < n; i++) {
      const x = i * (barW + barGap);
      const h = Math.max(2, peaks[i] * (cssH * 0.78));
      const sel = inKeep(x + barW / 2);
      ctx.fillStyle = sel ? accent : muted;
      ctx.globalAlpha = sel ? 0.95 : 0.35;
      ctx.beginPath();
      const r = Math.min(1.5, barW / 2);
      roundRect(ctx, x, mid - h / 2, barW, h, r);
      ctx.fill();
    }
    ctx.globalAlpha = 1;

    ctx.fillStyle = ink;
    ctx.globalAlpha = 0.18;
    const sorted = [...keepRanges].sort((a, b) => a.start - b.start);
    let cursor = 0;
    for (const r of sorted) {
      const x0 = (cursor / duration) * cssW;
      const x1 = (r.start / duration) * cssW;
      if (x1 > x0) ctx.fillRect(x0, 0, x1 - x0, cssH);
      cursor = r.end;
    }
    if (cursor < duration) {
      const x0 = (cursor / duration) * cssW;
      ctx.fillRect(x0, 0, cssW - x0, cssH);
    }
    ctx.globalAlpha = 1;

    const active = selectedSeg ?? { start, end };
    const startX = (active.start / duration) * cssW;
    const endX = (active.end / duration) * cssW;
    ctx.strokeStyle = accent;
    ctx.lineWidth = 2;
    ctx.strokeRect(startX + 1, 1, Math.max(2, endX - startX - 2), cssH - 2);

    const ph = Math.min(duration, Math.max(0, playhead));
    const phX = (ph / duration) * cssW;
    ctx.strokeStyle = ink;
    ctx.globalAlpha = 0.85;
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(phX, 0);
    ctx.lineTo(phX, cssH);
    ctx.stroke();
    ctx.globalAlpha = 1;
  }, [duration, start, end, playhead, isVideo, selectedSeg, segments]);

  useEffect(() => {
    draw();
  }, [draw]);

  useEffect(() => {
    const onResize = () => draw();
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [draw]);

  useEffect(() => {
    const ac = new AbortController();
    durationReadyRef.current = false;
    setLoading(true);
    setPeaksLoading(true);
    setLoadError(null);
    setPrecise(false);
    const bars = isVideo ? 160 : 128;
    peaksRef.current = placeholderPeaks(bars);
    setPreviewUrl(inlineMediaUrl(downloadUrl));

    (async () => {
      try {
        const waveUrl = waveformUrlFromDownload(downloadUrl, bars);
        const wave = await fetchWaveform(waveUrl, ac.signal);
        if (ac.signal.aborted) return;
        peaksRef.current = Float32Array.from(wave.peaks);
        setResolvedKind(wave.kind);
        applyDuration(wave.duration_seconds);
        setPeaksLoading(false);
      } catch {
        if (ac.signal.aborted) return;
        setPeaksLoading(false);
      }
    })();

    return () => ac.abort();
  }, [downloadUrl, isVideo, applyDuration]);

  const onMediaLoaded = () => {
    const media = mediaRef.current;
    if (!media) return;
    applyDuration(media.duration);
  };

  const onMediaError = () => {
    if (durationReadyRef.current) return;
    setLoadError("Could not load media preview");
    setLoading(false);
  };

  useEffect(() => {
    if (!peaksLoading) draw();
  }, [peaksLoading, draw]);

  const stopPlayheadLoop = () => {
    if (rafRef.current) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
  };

  const playBounds = () => {
    if (selectedSeg) return { a: selectedSeg.start, b: selectedSeg.end };
    return { a: start, b: end };
  };

  const tickPlayhead = useCallback(() => {
    const media = mediaRef.current;
    if (!media || media.paused) {
      stopPlayheadLoop();
      setPlaying(false);
      return;
    }
    const { a, b } = playBounds();
    const t = media.currentTime;
    if (t >= b - 0.04) {
      media.pause();
      media.currentTime = a;
      setPlayhead(a);
      setPlaying(false);
      stopPlayheadLoop();
      return;
    }
    setPlayhead(t);
    rafRef.current = requestAnimationFrame(tickPlayhead);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedSeg, start, end, duration]);

  const bindMedia = useCallback((el: HTMLMediaElement | null) => {
    mediaRef.current = el;
  }, []);

  const togglePlay = async () => {
    const media = mediaRef.current;
    if (!media || duration <= 0) return;
    if (playing) {
      media.pause();
      stopPlayheadLoop();
      setPlaying(false);
      return;
    }
    const { a, b } = playBounds();
    try {
      if (media.currentTime < a || media.currentTime >= b - 0.05) {
        media.currentTime = a;
      }
      await media.play();
      setPlaying(true);
      stopPlayheadLoop();
      rafRef.current = requestAnimationFrame(tickPlayhead);
    } catch {
      setApplyError("Could not play preview — try again");
    }
  };

  useEffect(() => {
    const media = mediaRef.current;
    if (!media || playing) return;
    try {
      media.currentTime = selectedSeg ? selectedSeg.start : start;
    } catch {
      /* ignore */
    }
  }, [start, playing, selectedSeg]);

  useEffect(() => {
    return () => {
      stopPlayheadLoop();
      mediaRef.current?.pause();
    };
  }, []);

  // Keep selected segment in sync with drag handles
  useEffect(() => {
    if (!selectedSegId) return;
    setSegments((prev) =>
      prev.map((s) => (s.id === selectedSegId ? { ...s, start, end } : s)),
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [start, end]);

  const selectSegment = (seg: Segment) => {
    setSelectedSegId(seg.id);
    setStart(seg.start);
    setEnd(seg.end);
    setPlayhead(seg.start);
  };

  const splitAtPlayhead = () => {
    const seg = selectedSeg;
    if (!seg) return;
    const t = Math.min(
      Math.max(playhead, seg.start + MIN_TRIM_SECONDS),
      seg.end - MIN_TRIM_SECONDS,
    );
    if (t - seg.start < MIN_TRIM_SECONDS || seg.end - t < MIN_TRIM_SECONDS) {
      setApplyError("Playhead too close to segment edge to split");
      return;
    }
    const left: Segment = { id: seg.id, start: seg.start, end: t };
    const right: Segment = { id: newSegId(), start: t, end: seg.end };
    setSegments((prev) => {
      const idx = prev.findIndex((s) => s.id === seg.id);
      if (idx < 0) return prev;
      const next = [...prev];
      next.splice(idx, 1, left, right);
      return next;
    });
    setSelectedSegId(left.id);
    setStart(left.start);
    setEnd(left.end);
    setApplyError(null);
  };

  const deleteSelectedSegment = () => {
    if (segments.length <= 1 || !selectedSegId) return;
    const idx = segments.findIndex((s) => s.id === selectedSegId);
    const next = segments.filter((s) => s.id !== selectedSegId);
    setSegments(next);
    const pick = next[Math.max(0, idx - 1)] ?? next[0];
    if (pick) selectSegment(pick);
  };

  const moveSegment = (dir: -1 | 1) => {
    if (!selectedSegId) return;
    setSegments((prev) => {
      const idx = prev.findIndex((s) => s.id === selectedSegId);
      const j = idx + dir;
      if (idx < 0 || j < 0 || j >= prev.length) return prev;
      const copy = [...prev];
      const tmp = copy[idx];
      copy[idx] = copy[j];
      copy[j] = tmp;
      return copy;
    });
  };

  const ratioFromClientX = (clientX: number) => {
    const el = trackRef.current;
    if (!el || duration <= 0) return 0;
    const rect = el.getBoundingClientRect();
    const x = Math.min(rect.width, Math.max(0, clientX - rect.left));
    return (x / rect.width) * duration;
  };

  const onHandleDown = (which: "start" | "end") => (e: ReactPointerEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dragRef.current = which;
    (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
  };

  const onMoveDown = (e: ReactPointerEvent) => {
    if (duration <= 0) return;
    e.preventDefault();
    e.stopPropagation();
    const t = ratioFromClientX(e.clientX);
    const len = Math.max(MIN_TRIM_SECONDS, end - start);
    dragRef.current = "move";
    moveLenRef.current = len;
    moveOffsetRef.current = t - start;
    (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
  };

  const moveHandle = (t: number) => {
    const which = dragRef.current;
    if (!which) return;
    if (which === "move") {
      const len = moveLenRef.current;
      const nextStart = Math.min(
        Math.max(0, t - moveOffsetRef.current),
        Math.max(0, duration - len),
      );
      setStart(nextStart);
      setEnd(nextStart + len);
      setPlayhead(nextStart);
      return;
    }
    if (which === "start") {
      const next = Math.min(t, end - MIN_TRIM_SECONDS);
      setStart(Math.max(0, next));
      setPlayhead(Math.max(0, next));
    } else {
      const next = Math.max(t, start + MIN_TRIM_SECONDS);
      setEnd(Math.min(duration, next));
    }
  };

  const onTrackPointerDown = (e: ReactPointerEvent) => {
    if (duration <= 0) return;
    const t = ratioFromClientX(e.clientX);
    setPlayhead(t);
    try {
      if (mediaRef.current) mediaRef.current.currentTime = t;
    } catch {
      /* ignore */
    }

    const hit = segments.find((s) => t >= s.start && t <= s.end);
    if (hit && hit.id !== selectedSegId) selectSegment(hit);

    const len = Math.max(MIN_TRIM_SECONDS, end - start);
    const edgePad = Math.min(0.35, len * 0.12);

    if (t >= start + edgePad && t <= end - edgePad) {
      dragRef.current = "move";
      moveLenRef.current = len;
      moveOffsetRef.current = t - start;
      moveHandle(t);
      trackRef.current?.setPointerCapture?.(e.pointerId);
      return;
    }

    const distStart = Math.abs(t - start);
    const distEnd = Math.abs(t - end);
    dragRef.current = distStart <= distEnd ? "start" : "end";
    moveHandle(t);
    trackRef.current?.setPointerCapture?.(e.pointerId);
  };

  const onPointerMove = (e: ReactPointerEvent) => {
    if (!dragRef.current) return;
    moveHandle(ratioFromClientX(e.clientX));
  };

  const onPointerUp = () => {
    dragRef.current = null;
  };

  const shiftSelection = (anchor: "start" | "end") => {
    const len = Math.max(MIN_TRIM_SECONDS, end - start);
    if (anchor === "start") {
      setStart(0);
      setEnd(Math.min(duration, len));
      setPlayhead(0);
    } else {
      const nextStart = Math.max(0, duration - len);
      setStart(nextStart);
      setEnd(duration);
      setPlayhead(nextStart);
    }
  };

  const resetSelection = () => {
    setStart(0);
    setEnd(duration);
    setPlayhead(0);
    const id = newSegId();
    setSegments([{ id, start: 0, end: duration }]);
    setSelectedSegId(id);
    setInsertFile(null);
    setReplaceAudio(false);
    setCrossfade(0);
    if (fileInputRef.current) fileInputRef.current.value = "";
    mediaRef.current?.pause();
    if (mediaRef.current) mediaRef.current.currentTime = 0;
    setPlaying(false);
    stopPlayheadLoop();
    setApplyError(null);
  };

  const runWithProgress = async (
    work: () => Promise<TrimJobResponse>,
    startMsg: string,
  ) => {
    setApplying(true);
    setApplyProgress(1);
    setApplyMessage(startMsg);
    setApplyError(null);
    mediaRef.current?.pause();
    setPlaying(false);
    stopPlayheadLoop();

    const pollId = window.setInterval(() => {
      void (async () => {
        try {
          const status = await getJob(jobId);
          if (typeof status.progress === "number" && status.progress > 0) {
            setApplyProgress(Math.max(1, Math.min(99, status.progress)));
          }
          if (status.message) setApplyMessage(status.message);
        } catch {
          /* ignore */
        }
      })();
    }, 450);

    try {
      const result = await work();
      setApplyProgress(100);
      setApplyMessage("Done");
      onTrimmed(result);
    } catch (err) {
      setApplyError(err instanceof Error ? err.message : "Edit failed");
      setApplyProgress(0);
      setApplyMessage(null);
    } finally {
      window.clearInterval(pollId);
      setApplying(false);
    }
  };

  const applyTimeline = async () => {
    if (applying || duration <= 0 || keepTotal < MIN_TRIM_SECONDS) return;

    // Sync selected handles into segments before apply
    const ranges = segments.map((s) =>
      s.id === selectedSegId ? { start, end } : { start: s.start, end: s.end },
    );

    if (ranges.length === 1) {
      const r = ranges[0];
      const isFull = r.start <= 0.02 && r.end >= duration - 0.02;
      if (isFull) {
        onCancel();
        return;
      }
      await runWithProgress(
        () =>
          trimJobMedia(jobId, {
            startSeconds: r.start,
            endSeconds: r.end,
            precise: isVideo ? precise : false,
          }),
        isVideo
          ? precise
            ? "Starting exact video cut…"
            : "Starting fast video trim…"
          : "Starting trim…",
      );
      return;
    }

    await runWithProgress(
      () =>
        composeJobMedia(jobId, {
          ranges: ranges.map((r) => ({
            startSeconds: r.start,
            endSeconds: r.end,
          })),
          precise: isVideo ? precise : false,
        }),
      `Stitching ${ranges.length} segment(s)…`,
    );
  };

  const applyInsert = async () => {
    if (applying || !insertFile) {
      setApplyError("Choose a video or audio file to insert");
      return;
    }
    await runWithProgress(
      () =>
        insertJobMedia(jobId, {
          file: insertFile,
          atSeconds: replaceAudio ? 0 : playhead,
          replaceAudio,
          crossfadeSeconds: replaceAudio ? 0 : crossfade,
        }),
      replaceAudio ? "Replacing audio…" : "Inserting media…",
    );
  };

  const requestCancelEdit = async () => {
    try {
      await cancelJobEdit(jobId);
      setApplyMessage("Cancelling…");
    } catch {
      /* ignore */
    }
  };

  const startPct = duration > 0 ? (start / duration) * 100 : 0;
  const endPct = duration > 0 ? (end / duration) * 100 : 100;
  const title = isVideo ? "Edit video" : "Edit audio";
  const hint =
    "Drag to keep · Split to cut · Insert at playhead" +
    (fileLabel ? ` · ${fileLabel}` : "");

  if (!mounted) return null;

  return createPortal(
    <div className="media-trim-overlay" role="presentation">
      <button
        type="button"
        className="media-trim-overlay__backdrop"
        aria-label="Close editor"
        onClick={() => {
          if (!applying) onCancel();
        }}
      />
      <div
        className="media-trim media-trim--modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
      >
        <div className="media-trim__header">
          <div>
            <p className="media-trim__title" id={titleId}>
              {title}
            </p>
            <p className="media-trim__hint">{hint}</p>
          </div>
          <button
            ref={closeRef}
            type="button"
            className="btn btn--ghost btn--small"
            onClick={onCancel}
            disabled={applying}
          >
            Close
          </button>
        </div>

        {loadError && (
          <p className="form-hint form-hint--error" role="alert">
            {loadError}
          </p>
        )}

        {!loadError && (
          <>
            {isVideo && (
              <div className="media-trim__stage">
                <video
                  ref={bindMedia}
                  className="media-trim__video"
                  src={previewUrl}
                  playsInline
                  preload="metadata"
                  crossOrigin="anonymous"
                  onLoadedMetadata={onMediaLoaded}
                  onDurationChange={onMediaLoaded}
                  onError={onMediaError}
                  onPlay={() => setPlaying(true)}
                  onPause={() => setPlaying(false)}
                  onEnded={() => {
                    setPlaying(false);
                    setPlayhead(playBounds().a);
                  }}
                />
              </div>
            )}

            {!isVideo && (
              <audio
                ref={bindMedia}
                src={previewUrl}
                preload="metadata"
                crossOrigin="anonymous"
                className="media-trim__audio-el"
                onLoadedMetadata={onMediaLoaded}
                onDurationChange={onMediaLoaded}
                onError={onMediaError}
              />
            )}

            {loading && (
              <div className="media-trim__loading" aria-live="polite">
                <span className="media-trim__spinner" aria-hidden />
                Opening editor…
              </div>
            )}

            {!loading && (
              <>
                {peaksLoading && (
                  <p className="media-trim__peaks-hint" aria-live="polite">
                    <span className="media-trim__spinner" aria-hidden />
                    Building waveform…
                  </p>
                )}

                <div className="media-trim__row">
                  <button
                    type="button"
                    className="media-trim__play"
                    onClick={() => void togglePlay()}
                    aria-label={playing ? "Pause" : "Play"}
                  >
                    {playing ? <PauseIcon /> : <PlayIcon />}
                  </button>

                  <div
                    ref={trackRef}
                    className={`media-trim__track${isVideo ? " media-trim__track--video" : ""}`}
                    onPointerDown={onTrackPointerDown}
                    onPointerMove={onPointerMove}
                    onPointerUp={onPointerUp}
                    onPointerCancel={onPointerUp}
                  >
                    <canvas ref={canvasRef} className="media-trim__canvas" aria-hidden />
                    <div
                      className="media-trim__sel"
                      style={{
                        left: `${startPct}%`,
                        width: `${Math.max(0, endPct - startPct)}%`,
                      }}
                      onPointerDown={onMoveDown}
                      onPointerMove={onPointerMove}
                      onPointerUp={onPointerUp}
                      onPointerCancel={onPointerUp}
                      aria-hidden
                    />
                    <button
                      type="button"
                      className="media-trim__handle media-trim__handle--start"
                      style={{ left: `${startPct}%` }}
                      aria-label="Range start"
                      onPointerDown={onHandleDown("start")}
                      onPointerMove={onPointerMove}
                      onPointerUp={onPointerUp}
                    />
                    <button
                      type="button"
                      className="media-trim__handle media-trim__handle--end"
                      style={{ left: `${endPct}%` }}
                      aria-label="Range end"
                      onPointerDown={onHandleDown("end")}
                      onPointerMove={onPointerMove}
                      onPointerUp={onPointerUp}
                    />
                  </div>
                </div>

                <div className="media-trim__times" aria-live="polite">
                  <span>{formatTrimTime(start)}</span>
                  <span className="media-trim__times-mid">
                    Keep {formatTrimTime(keepTotal)}
                    {segments.length > 1 ? ` · ${segments.length} parts` : ""}
                    {" · "}
                    Playhead {formatTrimTime(playhead)}
                  </span>
                  <span>{formatTrimTime(end)}</span>
                </div>

                <div className="media-trim__toolbar">
                  <button
                    type="button"
                    className="btn btn--ghost btn--small"
                    onClick={() => shiftSelection("start")}
                    disabled={applying || fullSingle || atStart}
                  >
                    ← To start
                  </button>
                  <button
                    type="button"
                    className="btn btn--ghost btn--small"
                    onClick={() => shiftSelection("end")}
                    disabled={applying || fullSingle || atEnd}
                  >
                    To end →
                  </button>
                  <button
                    type="button"
                    className="btn btn--ghost btn--small"
                    onClick={splitAtPlayhead}
                    disabled={applying || !selectedSeg}
                  >
                    Split
                  </button>
                  <button
                    type="button"
                    className="btn btn--ghost btn--small"
                    onClick={deleteSelectedSegment}
                    disabled={applying || segments.length <= 1}
                  >
                    Delete part
                  </button>
                  <button
                    type="button"
                    className="btn btn--ghost btn--small"
                    onClick={() => moveSegment(-1)}
                    disabled={applying || segments.length < 2}
                  >
                    ← Reorder
                  </button>
                  <button
                    type="button"
                    className="btn btn--ghost btn--small"
                    onClick={() => moveSegment(1)}
                    disabled={applying || segments.length < 2}
                  >
                    Reorder →
                  </button>
                </div>

                {segments.length > 1 && (
                  <ul className="media-trim__seg-list">
                    {segments.map((s, i) => (
                      <li key={s.id}>
                        <button
                          type="button"
                          className={`media-trim__seg-chip${s.id === selectedSegId ? " is-active" : ""}`}
                          onClick={() => selectSegment(s)}
                          disabled={applying}
                        >
                          {i + 1}. {formatTrimTime(s.start)}–{formatTrimTime(s.end)}
                        </button>
                      </li>
                    ))}
                  </ul>
                )}

                <div className="media-trim__insert">
                  <span className="media-trim__insert-label">Insert</span>
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept="audio/*,video/*,.mp3,.m4a,.aac,.wav,.ogg,.opus,.flac,.mp4,.webm,.mkv,.mov,.m4v"
                    hidden
                    onChange={(e) => {
                      const f = e.target.files?.[0] ?? null;
                      setInsertFile(f);
                      setApplyError(null);
                    }}
                  />
                  <button
                    type="button"
                    className="btn btn--ghost btn--small"
                    disabled={applying}
                    onClick={() => fileInputRef.current?.click()}
                  >
                    {insertFile ? insertFile.name : "Choose file…"}
                  </button>
                  <label className="media-trim__precise media-trim__precise--inline">
                    <input
                      type="checkbox"
                      checked={replaceAudio}
                      disabled={applying}
                      onChange={(e) => setReplaceAudio(e.target.checked)}
                    />
                    <span>Replace audio only</span>
                  </label>
                  {!replaceAudio && (
                    <label className="media-trim__crossfade">
                      Crossfade
                      <input
                        type="number"
                        min={0}
                        max={2}
                        step={0.1}
                        value={crossfade}
                        disabled={applying}
                        onChange={(e) =>
                          setCrossfade(
                            Math.min(2, Math.max(0, Number(e.target.value) || 0)),
                          )
                        }
                      />
                    </label>
                  )}
                  <button
                    type="button"
                    className="btn btn--ghost btn--small"
                    onClick={() => void applyInsert()}
                    disabled={applying || !insertFile}
                    title={
                      replaceAudio
                        ? "Replace the audio track"
                        : `Insert at ${formatTrimTime(playhead)}`
                    }
                  >
                    {replaceAudio
                      ? "Replace audio"
                      : `Insert at ${formatTrimTime(playhead)}`}
                  </button>
                </div>

                {isVideo && (
                  <label className="media-trim__precise">
                    <input
                      type="checkbox"
                      checked={precise}
                      disabled={applying}
                      onChange={(e) => setPrecise(e.target.checked)}
                    />
                    <span>
                      Exact cut
                      <span className="media-trim__precise-note">
                        {" "}
                        · slower re-encode, frame-accurate
                      </span>
                    </span>
                  </label>
                )}

                <div className="media-trim__actions">
                  {applying && (
                    <div
                      className="media-trim__progress"
                      role="progressbar"
                      aria-valuemin={0}
                      aria-valuemax={100}
                      aria-valuenow={applyProgress}
                      aria-live="polite"
                    >
                      <div className="media-trim__progress-top">
                        <span>{applyMessage || "Working…"}</span>
                        <span className="media-trim__progress-pct">
                          {applyProgress}%
                        </span>
                      </div>
                      <div className="media-trim__progress-track">
                        <div
                          className="media-trim__progress-bar"
                          style={{ width: `${applyProgress}%` }}
                        />
                      </div>
                    </div>
                  )}
                  {applying ? (
                    <button
                      type="button"
                      className="btn btn--ghost btn--small"
                      onClick={() => void requestCancelEdit()}
                    >
                      Cancel edit
                    </button>
                  ) : (
                    <button
                      type="button"
                      className="btn btn--ghost btn--small"
                      onClick={resetSelection}
                    >
                      Reset
                    </button>
                  )}
                  <button
                    type="button"
                    className="btn btn--primary"
                    onClick={() => void applyTimeline()}
                    disabled={
                      applying || keepTotal < MIN_TRIM_SECONDS || fullSingle
                    }
                  >
                    {applying
                      ? "Working…"
                      : segments.length > 1
                        ? "Apply cuts"
                        : "Apply trim"}
                  </button>
                </div>
              </>
            )}
          </>
        )}

        {applyError && (
          <p className="form-hint form-hint--error" role="alert">
            {applyError}
          </p>
        )}
      </div>
    </div>,
    document.body,
  );
}

/** @deprecated Use MediaTrimEditor — kept so older imports keep working. */
export { MediaTrimEditor as AudioTrimEditor };

function roundRect(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  h: number,
  r: number,
) {
  const rr = Math.min(r, w / 2, h / 2);
  ctx.moveTo(x + rr, y);
  ctx.arcTo(x + w, y, x + w, y + h, rr);
  ctx.arcTo(x + w, y + h, x, y + h, rr);
  ctx.arcTo(x, y + h, x, y, rr);
  ctx.arcTo(x, y, x + w, y, rr);
  ctx.closePath();
}

function PlayIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" fill="currentColor" aria-hidden>
      <path d="M5 3.5v11l10-5.5L5 3.5z" />
    </svg>
  );
}

function PauseIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" fill="currentColor" aria-hidden>
      <rect x="4" y="3.5" width="3.5" height="11" rx="0.5" />
      <rect x="10.5" y="3.5" width="3.5" height="11" rx="0.5" />
    </svg>
  );
}
