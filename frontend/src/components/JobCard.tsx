"use client";

import { useEffect, useRef, useState } from "react";
import {
  downloadButtonLabel,
  formatCountdown,
  formatDuration,
  formatSizeMb,
  qualityLabel,
  softFailureCopy,
  statusLabel,
  statusMessage,
} from "@/lib/format";
import { isActiveStatus } from "@/lib/types";
import type { TrimJobResponse } from "@/lib/types";
import type { TrackedJob } from "@/hooks/useJobTracker";
import { useJobTracker } from "@/hooks/useJobTracker";
import {
  absoluteDownloadUrl,
  getDownloadLink,
  restoreJobMedia,
  undoJobMedia,
} from "@/lib/api";
import { useAuth } from "@/components/AuthProvider";
import { MediaTrimEditor } from "@/components/MediaTrimEditor";
import { guessMediaKind, isTrimmableMedia } from "@/lib/mediaTrim";

type Props = {
  initial: TrackedJob;
  onDismiss?: (jobId: string) => void;
  onRetry?: (job: TrackedJob) => void;
  onJobUpdate?: (job: TrackedJob) => void;
};

export function JobCard({ initial, onDismiss, onRetry, onJobUpdate }: Props) {
  const { job, setJob, cancelling, cancelError, cancel } = useJobTracker(initial);
  const { recordHistory } = useAuth();
  const [refreshing, setRefreshing] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const [linkOverride, setLinkOverride] = useState<string | null>(null);
  const [originalOverride, setOriginalOverride] = useState<string | null>(null);
  const [trimming, setTrimming] = useState(false);
  const [editBusy, setEditBusy] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);
  const [countdown, setCountdown] = useState<string | null>(
    formatCountdown(job.expires_at),
  );

  const active = isActiveStatus(job.status);
  const pct = Math.max(0, Math.min(100, job.progress || 0));
  const downloadHref = linkOverride || job.absolute_download_url;
  const originalHref =
    originalOverride || job.absolute_original_download_url || null;
  const hasTrim = Boolean(job.has_trim && originalHref);
  const hasPreviousEdit = Boolean(job.has_previous_edit);
  const sizeMb = job.file_size_mb ?? job.estimated_size_mb ?? null;
  const originalSizeMb = job.original_file_size_mb ?? null;
  const trimSourceUrl = originalHref || downloadHref;
  const canTrim =
    job.status === "done" &&
    !!trimSourceUrl &&
    isTrimmableMedia({ quality: job.quality, fileName: job.file_name });
  const trimKind = guessMediaKind({
    quality: job.quality,
    fileName: job.file_name,
  });
  const trimLabel = trimKind === "video" ? "Edit video" : "Edit audio";

  useEffect(() => {
    onJobUpdate?.(job);
  }, [job, onJobUpdate]);

  useEffect(() => {
    if (job.status !== "done" || !job.expires_at) {
      setCountdown(null);
      return;
    }
    const tick = () => setCountdown(formatCountdown(job.expires_at));
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, [job.status, job.expires_at]);

  const historyPushed = useRef(false);

  useEffect(() => {
    if (job.status !== "done" || !job.source_url || historyPushed.current) return;
    historyPushed.current = true;
    void recordHistory({
      source_url: job.source_url,
      title: job.title || "Download",
      thumbnail: job.thumbnail,
      quality: job.quality,
      audio_format: job.audio_format,
      file_name: job.file_name,
      file_size_mb: job.file_size_mb ?? null,
    });
  }, [
    job.status,
    job.source_url,
    job.title,
    job.thumbnail,
    job.quality,
    job.audio_format,
    job.file_name,
    job.file_size_mb,
    recordHistory,
  ]);

  const refreshLink = async () => {
    setRefreshing(true);
    setRefreshError(null);
    try {
      const link = await getDownloadLink(job.job_id);
      setLinkOverride(absoluteDownloadUrl(link.download_url));
    } catch (err) {
      setRefreshError(
        err instanceof Error ? err.message : "Could not refresh link",
      );
    } finally {
      setRefreshing(false);
    }
  };

  const handleRetry = async () => {
    if (!onRetry || !job.source_url) return;
    setRetrying(true);
    try {
      await onRetry(job);
    } finally {
      setRetrying(false);
    }
  };

  const handleTrimmed = (result: TrimJobResponse) => {
    const abs = absoluteDownloadUrl(result.download_url);
    const absOriginal = result.original_download_url
      ? absoluteDownloadUrl(result.original_download_url)
      : null;
    setLinkOverride(abs);
    if (absOriginal) setOriginalOverride(absOriginal);
    setJob((prev) => ({
      ...prev,
      status: "done",
      progress: 100,
      download_url: result.download_url,
      absolute_download_url: abs,
      original_download_url: result.original_download_url ?? null,
      absolute_original_download_url: absOriginal,
      file_size_mb: result.file_size_mb,
      original_file_size_mb: result.original_file_size_mb ?? null,
      has_trim: Boolean(result.has_trim),
      has_previous_edit: Boolean(result.has_previous_edit),
      expires_at: result.expires_at,
      message: null,
      error: null,
    }));
    setTrimming(false);
    setEditError(null);
  };

  const handleRestore = async () => {
    if (editBusy) return;
    setEditBusy(true);
    setEditError(null);
    try {
      const result = await restoreJobMedia(job.job_id);
      handleTrimmed(result);
    } catch (err) {
      setEditError(err instanceof Error ? err.message : "Restore failed");
    } finally {
      setEditBusy(false);
    }
  };

  const handleUndo = async () => {
    if (editBusy) return;
    setEditBusy(true);
    setEditError(null);
    try {
      const result = await undoJobMedia(job.job_id);
      handleTrimmed(result);
    } catch (err) {
      setEditError(err instanceof Error ? err.message : "Undo failed");
    } finally {
      setEditBusy(false);
    }
  };

  const failure = softFailureCopy(job.error, job.error_hint);
  const trimmedLabel = downloadButtonLabel({
    fileName: job.file_name,
    fileSizeMb: sizeMb,
    quality: job.quality,
    audioFormat: job.audio_format,
    prefix: hasTrim ? "Download trimmed" : "Download",
  });
  const originalLabel = downloadButtonLabel({
    fileName: job.file_name,
    fileSizeMb: originalSizeMb,
    quality: job.quality,
    audioFormat: job.audio_format,
    prefix: "Download original",
  });

  return (
    <article className="job-card" aria-live="polite">
      <div className="job-card__media">
        {job.thumbnail ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={job.thumbnail} alt="" />
        ) : (
          <div className="job-card__placeholder" aria-hidden />
        )}
      </div>

      <div className="job-card__body">
        <div className="job-card__top">
          <h3 className="job-card__title">{job.title || "Download"}</h3>
          <div className="job-card__top-end">
            {job.status !== "done" && (
              <span
                className={`job-pill job-pill--${job.status === "retrying" ? "downloading" : job.status}`}
              >
                {statusLabel(job.status)}
              </span>
            )}
            {!active && onDismiss && (
              <button
                type="button"
                className="job-card__close"
                onClick={() => onDismiss(job.job_id)}
                aria-label="Close"
                title="Close"
              >
                <svg
                  width="14"
                  height="14"
                  viewBox="0 0 14 14"
                  fill="none"
                  aria-hidden
                >
                  <path
                    d="M3.5 3.5l7 7M10.5 3.5l-7 7"
                    stroke="currentColor"
                    strokeWidth="1.75"
                    strokeLinecap="round"
                  />
                </svg>
              </button>
            )}
          </div>
        </div>

        {active && (
          <p className="job-card__message">
            {statusMessage(job.status, job.message)}
          </p>
        )}

        {active && (
          <div
            className="progress"
            role="progressbar"
            aria-valuenow={pct}
            aria-valuemin={0}
            aria-valuemax={100}
          >
            <div className="progress__bar" style={{ width: `${pct}%` }} />
            <span className="progress__label">{pct}%</span>
          </div>
        )}

        {job.status === "failed" && (
          <div className="job-card__error" role="alert">
            <strong>{failure.title}</strong>
            {failure.detail && <p>{failure.detail}</p>}
          </div>
        )}

        {job.status === "done" && downloadHref && (
          <div className="job-card__done">
            <a className="btn btn--primary btn--small" href={downloadHref} download>
              {trimmedLabel}
            </a>
            {hasTrim && originalHref && (
              <a
                className="btn btn--ghost btn--small"
                href={originalHref}
                download
              >
                {originalLabel}
              </a>
            )}
            {canTrim && (
              <button
                type="button"
                className="btn btn--ghost btn--small"
                onClick={() => setTrimming(true)}
                disabled={editBusy}
              >
                {trimLabel}
              </button>
            )}
            {hasPreviousEdit && (
              <button
                type="button"
                className="btn btn--ghost btn--small"
                onClick={() => void handleUndo()}
                disabled={editBusy}
              >
                {editBusy ? "Working…" : "Undo last edit"}
              </button>
            )}
            {hasTrim && (
              <button
                type="button"
                className="btn btn--ghost btn--small"
                onClick={() => void handleRestore()}
                disabled={editBusy}
                title="Restore the full original download"
              >
                Restore original
              </button>
            )}
            <button
              type="button"
              className="btn btn--ghost btn--small"
              onClick={() => void refreshLink()}
              disabled={refreshing}
            >
              {refreshing ? "Refreshing…" : "Refresh link"}
            </button>
            {countdown && (
              <span className="job-card__expiry">
                {countdown === "expired"
                  ? "Link expired — refresh to get a new one"
                  : `Expires in ${countdown}`}
              </span>
            )}
          </div>
        )}

        {job.status === "done" && trimSourceUrl && trimming && (
          <MediaTrimEditor
            jobId={job.job_id}
            downloadUrl={trimSourceUrl}
            kind={trimKind}
            fileLabel={
              trimKind === "audio" && job.audio_format
                ? job.audio_format.toUpperCase()
                : job.file_name?.split(".").pop()?.toUpperCase()
            }
            onCancel={() => setTrimming(false)}
            onTrimmed={handleTrimmed}
          />
        )}

        {job.status === "done" && !downloadHref && (
          <div className="job-card__done">
            <button
              type="button"
              className="btn btn--primary btn--small"
              onClick={() => void refreshLink()}
              disabled={refreshing}
            >
              {refreshing ? "Fetching link…" : "Get download link"}
            </button>
          </div>
        )}

        {(refreshError || cancelError || editError) && (
          <p className="form-hint form-hint--error" role="alert">
            {refreshError || cancelError || editError}
          </p>
        )}

        <div className="job-card__actions">
          {active && (
            <button
              type="button"
              className="btn btn--ghost"
              onClick={() => void cancel()}
              disabled={cancelling}
            >
              {cancelling ? "Cancelling…" : "Cancel"}
            </button>
          )}
          {job.status === "failed" && onRetry && job.source_url && (
            <button
              type="button"
              className="btn btn--primary btn--small"
              onClick={() => void handleRetry()}
              disabled={retrying}
            >
              {retrying ? "Retrying…" : "Try again"}
            </button>
          )}
          {(job.quality || sizeMb != null) && (
            <span className="job-card__meta">
              {[
                qualityLabel(job.quality, job.audio_format),
                sizeMb != null ? formatSizeMb(sizeMb) : null,
              ]
                .filter(Boolean)
                .join(" · ")}
            </span>
          )}
        </div>
      </div>
    </article>
  );
}

export function JobMetaLine({
  duration,
  sizeMb,
  site,
}: {
  duration?: number | null;
  sizeMb?: number | null;
  site?: string | null;
}) {
  const parts = [
    site,
    duration != null ? formatDuration(duration) : null,
    sizeMb != null ? formatSizeMb(sizeMb) : null,
  ].filter(Boolean);
  if (!parts.length) return null;
  return <p className="preview__meta">{parts.join(" · ")}</p>;
}
