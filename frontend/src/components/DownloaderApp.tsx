"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from "react";
import { useSearchParams } from "next/navigation";
import { AuthButtons } from "@/components/AuthButtons";
import { BrandLockup } from "@/components/Logo";
import { CaptchaWidget, resetCaptcha } from "@/components/CaptchaWidget";
import { HistoryGate, useHistoryCount } from "@/components/HistoryGate";
import { JobCard, JobMetaLine } from "@/components/JobCard";
import { QualityStrip } from "@/components/QualityStrip";
import { ThemeToggle } from "@/components/ThemeToggle";
import type { TrackedJob } from "@/hooks/useJobTracker";
import {
  ApiError,
  createBatchJobs,
  createJob,
  fetchPreviewWithRetry,
  getHealth,
  getJob,
} from "@/lib/api";
import { captchaEnabled } from "@/lib/config";
import {
  formatDuration,
  hintForUserError,
  looksLikeFacebookUrl,
} from "@/lib/format";
import type { AudioFormat, PreviewResponse } from "@/lib/types";
import { isActiveStatus } from "@/lib/types";
import {
  loadPrefs,
  loadStoredJobs,
  removeStoredJob,
  savePrefs,
  saveStoredJobs,
  upsertStoredJob,
  type HistoryItem,
  type StoredJob,
} from "@/lib/storage";
import { clientUrlError, looksLikeUrl, parseUrlList } from "@/lib/urls";

type Phase = "idle" | "previewing" | "ready" | "starting";

const PASTE_DEBOUNCE_MS = 350;

function toStored(job: TrackedJob): StoredJob {
  return {
    job_id: job.job_id,
    source_url: job.source_url || "",
    title: job.title,
    thumbnail: job.thumbnail,
    quality: job.quality,
    audio_format: job.audio_format,
    estimated_size_mb: job.estimated_size_mb,
    status: job.status,
    created_at: Date.now(),
  };
}

export function DownloaderApp() {
  const searchParams = useSearchParams();
  const prefs = useMemo(() => loadPrefs(), []);

  const [url, setUrl] = useState("");
  const [phase, setPhase] = useState<Phase>("idle");
  const [error, setError] = useState<string | null>(null);
  const [statusHint, setStatusHint] = useState<string | null>(null);
  const [preview, setPreview] = useState<PreviewResponse | null>(null);
  const [quality, setQuality] = useState(prefs.quality);
  const [audioFormat, setAudioFormat] = useState<AudioFormat>(prefs.audioFormat);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [jobs, setJobs] = useState<TrackedJob[]>([]);
  const [captchaToken, setCaptchaToken] = useState<string | null>(null);
  const [multiUrls, setMultiUrls] = useState<string[]>([]);
  const [hydrated, setHydrated] = useState(false);
  const [clipHint, setClipHint] = useState(false);
  const [facebookReady, setFacebookReady] = useState(true);
  const [historyOpen, setHistoryOpen] = useState(false);
  const historyCount = useHistoryCount();

  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastAutoUrl = useRef<string>("");
  const captchaTokenRef = useRef<string | null>(null);
  const phaseRef = useRef<Phase>(phase);
  const deepLinkHandled = useRef(false);

  captchaTokenRef.current = captchaToken;
  phaseRef.current = phase;

  const maxSelect = preview?.max_playlist_select ?? 10;
  const isPlaylist = preview?.kind === "playlist";
  const needsCaptcha = captchaEnabled();
  const busy = phase === "previewing" || phase === "starting";

  const selectedEntries = useMemo(() => {
    if (!preview?.entries?.length) return [];
    return preview.entries.filter((e) => selected.has(e.id));
  }, [preview, selected]);

  const errorHint = useMemo(() => hintForUserError(error), [error]);

  const showFacebookSoftWarn = useMemo(() => {
    if (facebookReady) return false;
    const candidates = multiUrls.length ? multiUrls : url ? [url] : [];
    return candidates.some((u) => looksLikeFacebookUrl(u));
  }, [facebookReady, multiUrls, url]);

  const persistJobs = useCallback((list: TrackedJob[]) => {
    saveStoredJobs(
      list
        .filter((j) => j.source_url)
        .map(toStored)
        .filter((j) => j.source_url),
    );
  }, []);

  const setJobsAndPersist = useCallback(
    (updater: (prev: TrackedJob[]) => TrackedJob[]) => {
      setJobs((prev) => {
        const next = updater(prev);
        persistJobs(next);
        return next;
      });
    },
    [persistJobs],
  );

  const resetPreview = useCallback(() => {
    setPreview(null);
    setPhase("idle");
    setSelected(new Set());
    setStatusHint(null);
    setMultiUrls([]);
  }, []);

  const applyQualityPreference = useCallback(
    (data: PreviewResponse) => {
      const saved = loadPrefs();
      const qualities = data.available_qualities || [];
      const preferred =
        qualities.find((q) => q.id === saved.quality)?.id ||
        qualities.find((q) => q.id === "best")?.id ||
        qualities[0]?.id ||
        "best";
      setQuality(preferred);
      setAudioFormat(saved.audioFormat);
    },
    [],
  );

  const rememberPrefs = useCallback((q: string, audio: AudioFormat) => {
    savePrefs({ quality: q, audioFormat: audio });
  }, []);

  const enqueueTracked = useCallback(
    (job: TrackedJob) => {
      setJobsAndPersist((prev) => [job, ...prev.filter((j) => j.job_id !== job.job_id)]);
      upsertStoredJob(toStored(job));
    },
    [setJobsAndPersist],
  );

  const createTrackedJob = useCallback(
    async (opts: {
      url: string;
      quality: string;
      audioFormat: AudioFormat;
      title?: string | null;
      thumbnail?: string | null;
      estimated_size_mb?: number | null;
      captchaToken?: string | null;
    }) => {
      const { job_id } = await createJob({
        url: opts.url,
        quality: opts.quality,
        audioFormat: opts.audioFormat,
        captchaToken: opts.captchaToken,
      });
      const tracked: TrackedJob = {
        job_id,
        status: "queued",
        progress: 0,
        error: null,
        error_hint: null,
        message: "Waiting for a free worker…",
        download_url: null,
        expires_at: null,
        quality: opts.quality,
        audio_format: opts.audioFormat,
        title: opts.title,
        thumbnail: opts.thumbnail,
        source_url: opts.url,
        estimated_size_mb: opts.estimated_size_mb ?? null,
      };
      enqueueTracked(tracked);
      return tracked;
    },
    [enqueueTracked],
  );

  const runPreview = useCallback(
    async (rawUrl: string) => {
      const trimmed = rawUrl.trim();
      const validation = clientUrlError(trimmed);
      if (validation) {
        setError(validation);
        return;
      }
      if (needsCaptcha && !captchaTokenRef.current) {
        setError("Complete the CAPTCHA to continue.");
        return;
      }
      if (phaseRef.current === "previewing" || phaseRef.current === "starting") {
        return;
      }

      setError(null);
      setMultiUrls([]);
      setStatusHint("Fetching media info…");
      setPhase("previewing");
      lastAutoUrl.current = trimmed;

      try {
        const data = await fetchPreviewWithRetry(
          trimmed,
          captchaTokenRef.current,
          {
            attempts: 3,
            onRetry: (attempt, max) => {
              setStatusHint(`Still working… (${attempt}/${max})`);
            },
          },
        );

        applyQualityPreference(data);
        setPreview(data);

        if (data.kind === "playlist" && data.entries.length) {
          const initial = data.entries.slice(0, Math.min(3, maxSelect));
          setSelected(new Set(initial.map((x) => x.id)));
        } else {
          setSelected(new Set());
        }

        setPhase("ready");
        setStatusHint(null);
        resetCaptcha();
        setCaptchaToken(null);
      } catch (err) {
        setPreview(null);
        setPhase("idle");
        setStatusHint(null);
        setError(
          err instanceof ApiError
            ? err.message
            : "Something went wrong on our side. Please try again.",
        );
        resetCaptcha();
        setCaptchaToken(null);
      }
    },
    [applyQualityPreference, maxSelect, needsCaptcha],
  );

  const scheduleAutoProcess = useCallback(
    (value: string) => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
      const list = parseUrlList(value).filter((u) => !clientUrlError(u));
      if (list.length > 1) {
        debounceRef.current = setTimeout(() => {
          setMultiUrls(list);
          setPreview(null);
          setPhase("ready");
          setError(null);
          setStatusHint(`${list.length} links ready — choose quality and download.`);
          lastAutoUrl.current = value.trim();
        }, PASTE_DEBOUNCE_MS);
        return;
      }
      const trimmed = value.trim();
      if (!looksLikeUrl(trimmed) || clientUrlError(trimmed)) return;
      if (trimmed === lastAutoUrl.current) return;
      if (needsCaptcha && !captchaTokenRef.current) return;

      debounceRef.current = setTimeout(() => {
        void runPreview(trimmed);
      }, PASTE_DEBOUNCE_MS);
    },
    [needsCaptcha, runPreview],
  );

  // Hydrate persisted jobs
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const stored = loadStoredJobs();
      if (!stored.length) {
        setHydrated(true);
        return;
      }
      const restored: TrackedJob[] = [];
      for (const item of stored) {
        try {
          const fresh = await getJob(item.job_id);
          restored.push({
            ...fresh,
            title: item.title,
            thumbnail: item.thumbnail,
            source_url: item.source_url,
            estimated_size_mb: item.estimated_size_mb,
          });
        } catch {
          // Job expired / missing — drop
          removeStoredJob(item.job_id);
        }
      }
      if (!cancelled) {
        setJobs(restored);
        persistJobs(restored);
        setHydrated(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [persistJobs]);

  // Backend capability probe (impersonate / cookies → Facebook readiness)
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const health = await getHealth();
        if (cancelled) return;
        setFacebookReady(health.facebook_ready !== false);
      } catch {
        // Ignore — keep optimistic default
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Deep link ?url=
  useEffect(() => {
    if (deepLinkHandled.current) return;
    const q = searchParams.get("url");
    if (!q) return;
    deepLinkHandled.current = true;
    setUrl(q);
    scheduleAutoProcess(q);
  }, [searchParams, scheduleAutoProcess]);

  // Clipboard hint on load
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        if (!navigator.clipboard?.readText) return;
        const text = (await navigator.clipboard.readText()).trim();
        if (!alive || !text) return;
        const list = parseUrlList(text).filter((u) => !clientUrlError(u));
        if (list.length) setClipHint(true);
      } catch {
        /* permission denied */
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, []);

  const pasteFromClipboard = async () => {
    try {
      const text = (await navigator.clipboard.readText()).trim();
      if (!text) {
        setError("Clipboard is empty.");
        return;
      }
      setUrl(text);
      setClipHint(false);
      setError(null);
      scheduleAutoProcess(text);
    } catch {
      setError("Could not read clipboard. Paste manually into the box.");
    }
  };

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (debounceRef.current) clearTimeout(debounceRef.current);
    const list = parseUrlList(url).filter((u) => !clientUrlError(u));
    if (list.length > 1) {
      setMultiUrls(list);
      setPreview(null);
      setPhase("ready");
      setStatusHint(`${list.length} links ready — choose quality and download.`);
      return;
    }
    void runPreview(url);
  };

  const toggleEntry = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else if (next.size < maxSelect) next.add(id);
      return next;
    });
  };

  const setQuickQuality = (id: string) => {
    setQuality(id);
    rememberPrefs(id, audioFormat);
  };

  const startDownload = async () => {
    setError(null);
    if (needsCaptcha && !captchaToken) {
      setError("Complete the CAPTCHA to start the download.");
      return;
    }

    const audio = quality === "audio" ? audioFormat : "m4a";
    rememberPrefs(quality, audio);
    setPhase("starting");
    setStatusHint("Starting download…");

    try {
      if (multiUrls.length > 1) {
        const { job_ids } = await createBatchJobs({
          urls: multiUrls,
          quality,
          audioFormat: audio,
          captchaToken,
        });
        job_ids.forEach((job_id, i) => {
          enqueueTracked({
            job_id,
            status: "queued",
            progress: 0,
            error: null,
            error_hint: null,
            message: "Waiting for a free worker…",
            download_url: null,
            expires_at: null,
            quality,
            audio_format: audio,
            title: multiUrls[i],
            thumbnail: null,
            source_url: multiUrls[i],
          });
        });
      } else if (preview && isPlaylist) {
        if (selectedEntries.length === 0) {
          setError("Select at least one video from the playlist.");
          setPhase("ready");
          setStatusHint(null);
          return;
        }
        const { job_ids } = await createBatchJobs({
          urls: selectedEntries.map((e) => e.url),
          quality,
          audioFormat: audio,
          captchaToken,
        });
        job_ids.forEach((job_id, i) => {
          const entry = selectedEntries[i];
          enqueueTracked({
            job_id,
            status: "queued",
            progress: 0,
            error: null,
            error_hint: null,
            message: "Waiting for a free worker…",
            download_url: null,
            expires_at: null,
            quality,
            audio_format: audio,
            title: entry?.title || preview.title,
            thumbnail: entry?.thumbnail || preview.thumbnail,
            source_url: entry?.url,
            estimated_size_mb: null,
          });
        });
      } else if (preview) {
        const size =
          preview.available_qualities.find((q) => q.id === quality)
            ?.estimated_size_mb ?? preview.estimated_size_mb;
        await createTrackedJob({
          url: preview.url,
          quality,
          audioFormat: audio,
          title: preview.title,
          thumbnail: preview.thumbnail,
          estimated_size_mb: size,
          captchaToken,
        });
      } else {
        setPhase("idle");
        setStatusHint(null);
        return;
      }

      setUrl("");
      resetPreview();
      resetCaptcha();
      setCaptchaToken(null);
      lastAutoUrl.current = "";
      setPhase("idle");
      setStatusHint(null);
    } catch (err) {
      setPhase("ready");
      setStatusHint(null);
      setError(
        err instanceof ApiError ? err.message : "Could not start download",
      );
      resetCaptcha();
      setCaptchaToken(null);
    }
  };

  const dismissJob = useCallback(
    (jobId: string) => {
      removeStoredJob(jobId);
      setJobsAndPersist((prev) => prev.filter((j) => j.job_id !== jobId));
    },
    [setJobsAndPersist],
  );

  const retryJob = useCallback(
    async (job: TrackedJob) => {
      if (!job.source_url) return;
      const audio =
        job.quality === "audio"
          ? ((job.audio_format as AudioFormat) || "m4a")
          : "m4a";
      await createTrackedJob({
        url: job.source_url,
        quality: job.quality || "best",
        audioFormat: audio,
        title: job.title,
        thumbnail: job.thumbnail,
        estimated_size_mb: job.estimated_size_mb,
      });
      dismissJob(job.job_id);
    },
    [createTrackedJob, dismissJob],
  );

  const onJobUpdate = useCallback(
    (job: TrackedJob) => {
      setJobsAndPersist((prev) =>
        prev.map((j) => (j.job_id === job.job_id ? { ...j, ...job } : j)),
      );
    },
    [setJobsAndPersist],
  );

  const reuseHistory = (item: HistoryItem) => {
    setUrl(item.source_url);
    if (item.quality) setQuality(item.quality);
    if (item.audio_format === "mp3" || item.audio_format === "m4a") {
      setAudioFormat(item.audio_format);
    }
    scheduleAutoProcess(item.source_url);
  };

  const qualityOptions = preview?.available_qualities?.length
    ? preview.available_qualities
    : [
        { id: "best", label: "Best", estimated_size_mb: null, height: null },
        { id: "audio", label: "Audio only", estimated_size_mb: null, height: null },
      ];

  const showReadyPanel =
    phase === "ready" || phase === "starting"
      ? Boolean(preview) || multiUrls.length > 1
      : false;

  const activeCount = jobs.filter((j) => isActiveStatus(j.status)).length;

  return (
    <div className={`app-shell${showReadyPanel ? " app-shell--ready" : ""}`}>
      <div className="topbar">
        <a className="topbar__brand" href="/" aria-label="MediaPort home">
          <BrandLockup size={30} />
        </a>
        <div className="topbar__actions">
          <button
            type="button"
            className="btn btn--ghost btn--small topbar__recent"
            onClick={() => setHistoryOpen(true)}
            aria-expanded={historyOpen}
            aria-haspopup="dialog"
            aria-label={
              historyCount > 0 ? `Recent downloads, ${historyCount}` : "Recent downloads"
            }
          >
            <span className="topbar__recent-icon" aria-hidden>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
                <circle cx="12" cy="12" r="8.25" stroke="currentColor" strokeWidth="1.8" />
                <path
                  d="M12 7.5v5l3.2 1.9"
                  stroke="currentColor"
                  strokeWidth="1.8"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </span>
            <span className="topbar__recent-label">Recent</span>
            {historyCount > 0 ? (
              <span className="topbar__recent-count">{historyCount}</span>
            ) : null}
          </button>
          <AuthButtons />
          <ThemeToggle />
        </div>
      </div>

      <header className="hero">
        <div className="hero__glow" aria-hidden />

        <h1 className="hero__headline">Paste a link. Preview the media.</h1>
        <p className="hero__sub">
          YouTube, TikTok, Instagram, or Facebook — info loads automatically; you
          choose when to download. Files are temporary and auto-deleted later.
        </p>

        <form className="url-form sticky-paste" onSubmit={onSubmit}>
          <label className="sr-only" htmlFor="media-url">
            Media URL
          </label>
          <div className="url-form__row">
            <input
              id="media-url"
              className="url-input"
              type="text"
              inputMode="url"
              autoComplete="off"
              spellCheck={false}
              placeholder="Paste one or more links…"
              value={url}
              onChange={(e) => {
                const next = e.target.value;
                setUrl(next);
                if (preview || multiUrls.length) resetPreview();
                setError(null);
                scheduleAutoProcess(next);
              }}
              onPaste={(e) => {
                const pasted = e.clipboardData.getData("text");
                if (pasted) {
                  setTimeout(() => scheduleAutoProcess(pasted), 0);
                }
              }}
              disabled={busy}
              autoFocus
            />
            <button
              type="button"
              className="btn btn--ghost url-form__clip"
              onClick={() => void pasteFromClipboard()}
              disabled={busy}
              title="Paste from clipboard"
            >
              Paste
            </button>
            <button
              type="submit"
              className="btn btn--primary url-form__submit"
              disabled={busy}
            >
              {phase === "previewing"
                ? "Working…"
                : phase === "starting"
                  ? "Starting…"
                  : "Go"}
            </button>
          </div>

          {clipHint && !url && (
            <p className="form-hint form-hint--info">
              A media link is on your clipboard.{" "}
              <button
                type="button"
                className="text-link"
                onClick={() => void pasteFromClipboard()}
              >
                Paste it
              </button>
            </p>
          )}

          {(phase === "idle" || phase === "previewing") && (
            <CaptchaWidget onToken={setCaptchaToken} />
          )}

          {showFacebookSoftWarn && !error && (
            <p className="form-hint form-hint--warn" role="status">
              Facebook downloads work best for public posts. Private or
              friends-only links usually can’t be fetched.
            </p>
          )}

          {statusHint && !error && (
            <p className="form-hint form-hint--info" role="status">
              {statusHint}
            </p>
          )}

          {error && phase !== "ready" && phase !== "starting" && (
            <div className="form-hint form-hint--error" role="alert">
              <p>{error}</p>
              {errorHint && <p className="form-hint__detail">{errorHint}</p>}
            </div>
          )}
        </form>
      </header>

      {showReadyPanel && (
        <section className="preview" aria-label="Media preview">
          {preview && multiUrls.length <= 1 && (
            <QualityStrip
              options={qualityOptions.map((q) => ({
                id: q.id,
                label: q.label,
                estimated_size_mb: q.estimated_size_mb,
              }))}
              value={quality}
              onChange={setQuickQuality}
              name="quality"
              legend="Format"
            />
          )}

          {multiUrls.length > 1 && (
            <QualityStrip
              options={[
                { id: "best", label: "Best", estimated_size_mb: null },
                { id: "1080", label: "1080p", estimated_size_mb: null },
                { id: "720", label: "720p", estimated_size_mb: null },
                { id: "480", label: "480p", estimated_size_mb: null },
                { id: "360", label: "360p", estimated_size_mb: null },
                { id: "audio", label: "Audio only", estimated_size_mb: null },
              ]}
              value={quality}
              onChange={setQuickQuality}
              name="quality-multi"
              legend="Format for all links"
            />
          )}

          {multiUrls.length > 1 ? (
            <div className="multi-panel">
              <h2 className="preview__title">{multiUrls.length} links</h2>
              <ul className="multi-list">
                {multiUrls.map((u) => (
                  <li key={u}>{u}</li>
                ))}
              </ul>
              {quality === "audio" && (
                <div className="audio-formats" style={{ marginTop: "0.75rem" }}>
                  {(["m4a", "mp3"] as AudioFormat[]).map((fmt) => (
                    <label
                      key={fmt}
                      className={`chip${audioFormat === fmt ? " is-active" : ""}`}
                    >
                      <input
                        type="radio"
                        name="audio-format-multi"
                        value={fmt}
                        checked={audioFormat === fmt}
                        onChange={() => {
                          setAudioFormat(fmt);
                          rememberPrefs(quality, fmt);
                        }}
                      />
                      {fmt.toUpperCase()}
                    </label>
                  ))}
                </div>
              )}
            </div>
          ) : preview ? (
            <div className="preview__layout">
              <div className="preview__thumb">
                {preview.thumbnail ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={preview.thumbnail} alt="" />
                ) : (
                  <div className="preview__thumb-empty" />
                )}
              </div>

              <div className="preview__info">
                <p className="preview__kicker">
                  {isPlaylist ? "Playlist" : preview.site || "Video"}
                  {isPlaylist && preview.playlist_count != null
                    ? ` · ${preview.playlist_count} items`
                    : ""}
                </p>
                <h2 className="preview__title">
                  {preview.title || "Untitled media"}
                </h2>
                <JobMetaLine
                  site={preview.uploader}
                  duration={preview.duration_seconds}
                  sizeMb={
                    preview.available_qualities.find((q) => q.id === quality)
                      ?.estimated_size_mb ?? preview.estimated_size_mb
                  }
                />

                {quality === "audio" && (
                  <fieldset className="quality-field">
                    <legend>Audio format</legend>
                    <div className="audio-formats">
                      {(["m4a", "mp3"] as AudioFormat[]).map((fmt) => (
                        <label
                          key={fmt}
                          className={`chip${audioFormat === fmt ? " is-active" : ""}`}
                        >
                          <input
                            type="radio"
                            name="audio-format"
                            value={fmt}
                            checked={audioFormat === fmt}
                            onChange={() => {
                              setAudioFormat(fmt);
                              rememberPrefs(quality, fmt);
                            }}
                          />
                          {fmt.toUpperCase()}
                        </label>
                      ))}
                    </div>
                  </fieldset>
                )}

                {isPlaylist && (
                  <div className="playlist">
                    <div className="playlist__toolbar">
                      <p>
                        Select up to {maxSelect}{" "}
                        <span className="muted">
                          ({selected.size} selected)
                        </span>
                      </p>
                      <div className="playlist__actions">
                        <button
                          type="button"
                          className="btn btn--ghost btn--small"
                          onClick={() =>
                            setSelected(
                              new Set(
                                preview.entries
                                  .slice(0, maxSelect)
                                  .map((e) => e.id),
                              ),
                            )
                          }
                        >
                          Select max
                        </button>
                        <button
                          type="button"
                          className="btn btn--ghost btn--small"
                          onClick={() => setSelected(new Set())}
                        >
                          Clear
                        </button>
                      </div>
                    </div>
                    <ul className="playlist__list">
                      {preview.entries.map((entry) => {
                        const checked = selected.has(entry.id);
                        const disabled =
                          !checked && selected.size >= maxSelect;
                        return (
                          <li key={entry.id}>
                            <label
                              className={`playlist__item${checked ? " is-active" : ""}${disabled ? " is-disabled" : ""}`}
                            >
                              <input
                                type="checkbox"
                                checked={checked}
                                disabled={disabled}
                                onChange={() => toggleEntry(entry.id)}
                              />
                              {entry.thumbnail ? (
                                // eslint-disable-next-line @next/next/no-img-element
                                <img src={entry.thumbnail} alt="" />
                              ) : (
                                <span className="playlist__ph" />
                              )}
                              <span className="playlist__text">
                                <span className="playlist__name">
                                  {entry.title || "Untitled"}
                                </span>
                                <span className="playlist__dur">
                                  {formatDuration(entry.duration_seconds)}
                                </span>
                              </span>
                            </label>
                          </li>
                        );
                      })}
                    </ul>
                  </div>
                )}
              </div>
            </div>
          ) : null}

          <CaptchaWidget onToken={setCaptchaToken} />

          {error && (
            <div className="form-hint form-hint--error" role="alert">
              <p>{error}</p>
              {errorHint && <p className="form-hint__detail">{errorHint}</p>}
            </div>
          )}

          <div className="preview__cta">
            <button
              type="button"
              className="btn btn--primary"
              onClick={() => void startDownload()}
              disabled={phase === "starting"}
            >
              {phase === "starting"
                ? "Starting…"
                : multiUrls.length > 1
                  ? `Download ${multiUrls.length} links`
                  : isPlaylist
                    ? `Download ${selected.size || 0} selected`
                    : "Start download"}
            </button>
            <button
              type="button"
              className="btn btn--ghost"
              onClick={resetPreview}
              disabled={phase === "starting"}
            >
              Change URL
            </button>
          </div>
        </section>
      )}

      {hydrated && jobs.length > 0 && (
        <section className="jobs" aria-label="Downloads">
          <div className="jobs__head">
            <h2>Your downloads</h2>
            {activeCount > 0 && (
              <p className="muted">
                {activeCount} in progress
                {activeCount > 1 ? " — they’ll finish one after another" : ""}
              </p>
            )}
          </div>
          <div className="jobs__list">
            {jobs.map((j) => (
              <JobCard
                key={j.job_id}
                initial={j}
                onDismiss={dismissJob}
                onRetry={retryJob}
                onJobUpdate={onJobUpdate}
              />
            ))}
          </div>
        </section>
      )}

      <HistoryGate
        open={historyOpen}
        onClose={() => setHistoryOpen(false)}
        onReuse={reuseHistory}
      />
    </div>
  );
}
