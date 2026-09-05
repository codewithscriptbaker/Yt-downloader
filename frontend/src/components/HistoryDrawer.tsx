"use client";

import { useEffect, useId, useRef } from "react";
import { formatSizeMb, qualityLabel } from "@/lib/format";
import type { HistoryItem } from "@/lib/storage";

type Props = {
  open: boolean;
  onClose: () => void;
  items: HistoryItem[];
  onReuse: (item: HistoryItem) => void;
  onClear: () => void;
  cloud?: boolean;
};

export function HistoryDrawer({
  open,
  onClose,
  items,
  onReuse,
  onClear,
  cloud,
}: Props) {
  const titleId = useId();
  const panelRef = useRef<HTMLElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    closeRef.current?.focus();
    return () => {
      document.body.style.overflow = prev;
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  const handleReuse = (item: HistoryItem) => {
    onReuse(item);
    onClose();
  };

  return (
    <div
      className={`history-drawer${open ? " history-drawer--open" : ""}`}
      aria-hidden={!open}
    >
      <button
        type="button"
        className="history-drawer__backdrop"
        aria-label="Close recent downloads"
        tabIndex={open ? 0 : -1}
        onClick={onClose}
      />
      <aside
        ref={panelRef}
        className="history-drawer__panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        inert={open ? undefined : true}
      >
        <div className="history-drawer__head">
          <div className="history-drawer__titles">
            <h2 id={titleId}>
              Recent
              {cloud ? <span className="history__badge">Saved</span> : null}
            </h2>
            {!cloud && items.length > 0 && (
              <p className="history-drawer__sub muted">
                Stored on this device. Sign up to keep it everywhere.
              </p>
            )}
          </div>
          <div className="history-drawer__actions">
            {items.length > 0 && (
              <button
                type="button"
                className="btn btn--ghost btn--small"
                onClick={onClear}
              >
                Clear
              </button>
            )}
            <button
              ref={closeRef}
              type="button"
              className="history-drawer__close"
              onClick={onClose}
              aria-label="Close"
            >
              ×
            </button>
          </div>
        </div>

        {items.length === 0 ? (
          <p className="history-drawer__empty muted">
            No recent downloads yet. Finished downloads will show up here.
          </p>
        ) : (
          <ul className="history__list history-drawer__list">
            {items.slice(0, 24).map((item) => (
              <li key={item.id}>
                <button
                  type="button"
                  className="history__item"
                  onClick={() => handleReuse(item)}
                >
                  {item.thumbnail ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={item.thumbnail} alt="" />
                  ) : (
                    <span className="history__ph" />
                  )}
                  <span className="history__text">
                    <span className="history__title">{item.title}</span>
                    <span className="history__meta">
                      {[
                        qualityLabel(item.quality, item.audio_format),
                        item.file_size_mb != null
                          ? formatSizeMb(item.file_size_mb)
                          : null,
                      ]
                        .filter(Boolean)
                        .join(" · ")}
                    </span>
                  </span>
                  <span className="history__action">Use</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </aside>
    </div>
  );
}
