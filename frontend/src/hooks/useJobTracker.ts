"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  absoluteDownloadUrl,
  cancelJob,
  getDownloadLink,
  getJob,
} from "@/lib/api";
import { getWsBase } from "@/lib/config";
import type { JobStatus, JobStatusResponse } from "@/lib/types";
import { isActiveStatus } from "@/lib/types";

export type TrackedJob = JobStatusResponse & {
  title?: string | null;
  thumbnail?: string | null;
  absolute_download_url?: string | null;
  source_url?: string | null;
  estimated_size_mb?: number | null;
};

type WsPayload = Partial<JobStatusResponse> & {
  type?: string;
  error?: string;
};

async function withDownloadLink(job: TrackedJob): Promise<TrackedJob> {
  if (job.status !== "done") {
    return { ...job, absolute_download_url: null };
  }
  try {
    let downloadUrl = job.download_url;
    let merged = job;
    if (!downloadUrl) {
      const fresh = await getJob(job.job_id);
      downloadUrl = fresh.download_url;
      merged = { ...merged, ...fresh };
    }
    if (!downloadUrl) {
      const link = await getDownloadLink(job.job_id);
      downloadUrl = link.download_url;
    }
    return {
      ...merged,
      download_url: downloadUrl,
      absolute_download_url: downloadUrl
        ? absoluteDownloadUrl(downloadUrl)
        : null,
    };
  } catch {
    return {
      ...job,
      absolute_download_url: job.download_url
        ? absoluteDownloadUrl(job.download_url)
        : null,
    };
  }
}

export function useJobTracker(initial: TrackedJob) {
  const [job, setJob] = useState<TrackedJob>(initial);
  const [cancelling, setCancelling] = useState(false);
  const [cancelError, setCancelError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const jobId = initial.job_id;
  const startedActive = isActiveStatus(initial.status);

  const stopWatching = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
    if (wsRef.current) {
      try {
        wsRef.current.close();
      } catch {
        /* ignore */
      }
      wsRef.current = null;
    }
  }, []);

  useEffect(() => {
    let alive = true;
    (async () => {
      if (initial.status === "done") {
        const enriched = await withDownloadLink(initial);
        if (alive) setJob(enriched);
      } else {
        setJob(initial);
      }
    })();
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId]);

  useEffect(() => {
    if (!startedActive) return;

    let cancelled = false;
    let polling = false;

    const startPolling = () => {
      if (polling || pollRef.current) return;
      polling = true;
      pollRef.current = setInterval(async () => {
        try {
          const fresh = await getJob(jobId);
          if (cancelled) return;
          if (fresh.status === "done") {
            const enriched = await withDownloadLink(fresh);
            setJob((prev) => ({ ...prev, ...enriched }));
            stopWatching();
            return;
          }
          setJob((prev) => ({ ...prev, ...fresh }));
          if (!isActiveStatus(fresh.status)) stopWatching();
        } catch {
          /* keep trying */
        }
      }, 2000);
    };

    const base = getWsBase();
    if (!base) {
      startPolling();
      return () => {
        cancelled = true;
        stopWatching();
      };
    }

    let ws: WebSocket;
    try {
      ws = new WebSocket(`${base}/ws/jobs/${encodeURIComponent(jobId)}`);
    } catch {
      startPolling();
      return () => {
        cancelled = true;
        stopWatching();
      };
    }
    wsRef.current = ws;

    ws.onmessage = async (event) => {
      let data: WsPayload;
      try {
        data = JSON.parse(String(event.data)) as WsPayload;
      } catch {
        return;
      }
      if (data.type === "ping" || cancelled) return;

      if (data.error && !data.status) {
        setJob((prev) => ({
          ...prev,
          status: "failed",
          error: data.error || "Job not found",
        }));
        stopWatching();
        return;
      }
      if (!data.status) return;

      if (data.status === "done") {
        const enriched = await withDownloadLink({
          job_id: jobId,
          status: "done",
          progress: data.progress ?? 100,
          error: data.error ?? null,
          error_hint: data.error_hint ?? null,
          message: data.message ?? null,
          download_url: null,
          expires_at: data.expires_at ?? null,
          quality: data.quality ?? null,
          audio_format: data.audio_format ?? null,
        });
        setJob((prev) => ({ ...prev, ...enriched }));
        stopWatching();
        return;
      }

      setJob((prev) => ({
        ...prev,
        status: data.status as JobStatus,
        progress: data.progress ?? prev.progress,
        error: data.error ?? null,
        error_hint: data.error_hint ?? null,
        message: data.message ?? prev.message,
        expires_at: data.expires_at ?? prev.expires_at,
        quality: data.quality ?? prev.quality,
        audio_format: data.audio_format ?? prev.audio_format,
      }));

      if (!isActiveStatus(data.status as JobStatus)) stopWatching();
    };

    ws.onerror = () => {
      try {
        ws.close();
      } catch {
        /* ignore */
      }
    };

    ws.onclose = () => {
      wsRef.current = null;
      if (!cancelled) {
        setJob((prev) => {
          if (isActiveStatus(prev.status)) startPolling();
          return prev;
        });
      }
    };

    return () => {
      cancelled = true;
      stopWatching();
    };
  }, [jobId, startedActive, stopWatching]);

  const cancel = useCallback(async () => {
    setCancelling(true);
    setCancelError(null);
    try {
      await cancelJob(jobId);
      setJob((prev) => ({
        ...prev,
        status: "cancelled",
        error: "Cancelled",
        progress: 0,
      }));
      stopWatching();
    } catch (err) {
      setCancelError(err instanceof Error ? err.message : "Cancel failed");
    } finally {
      setCancelling(false);
    }
  }, [jobId, stopWatching]);

  return { job, cancelling, cancelError, cancel };
}
