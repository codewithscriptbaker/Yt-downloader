"use client";

import { useMemo, useState } from "react";
import { formatSizeMb } from "@/lib/format";

export type QualityOption = {
  id: string;
  label: string;
  estimated_size_mb?: number | null;
};

type Props = {
  options: QualityOption[];
  value: string;
  onChange: (id: string) => void;
  name?: string;
  legend?: string;
};

export function QualityStrip({
  options,
  value,
  onChange,
  name = "quality",
  legend = "Format",
}: Props) {
  const [open, setOpen] = useState(false);

  const selected = useMemo(
    () => options.find((q) => q.id === value) || options[0],
    [options, value],
  );

  // Collapsed: Best + Audio + current (if different), keeps one line compact
  const collapsed = useMemo(() => {
    const pick: QualityOption[] = [];
    const best = options.find((q) => q.id === "best");
    const audio = options.find((q) => q.id === "audio");
    if (best) pick.push(best);
    if (selected && selected.id !== "best" && selected.id !== "audio") {
      pick.push(selected);
    }
    if (audio) pick.push(audio);
    // de-dupe by id
    const seen = new Set<string>();
    return pick.filter((q) => {
      if (seen.has(q.id)) return false;
      seen.add(q.id);
      return true;
    });
  }, [options, selected]);

  const visible = open ? options : collapsed;
  const hiddenCount = Math.max(0, options.length - collapsed.length);

  return (
    <div className="quality-strip">
      <div className="quality-strip__head">
        <span className="quality-strip__legend">{legend}</span>
        {hiddenCount > 0 && (
          <button
            type="button"
            className={`quality-strip__toggle${open ? " is-open" : ""}`}
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            aria-label={open ? "Hide formats" : "Show all formats"}
            title={open ? "Hide formats" : `Show all (${options.length})`}
          >
            <span className="quality-strip__toggle-label">
              {open ? "Less" : `All (${options.length})`}
            </span>
            <span className="quality-strip__arrow" aria-hidden>
              ▾
            </span>
          </button>
        )}
      </div>

      <div
        className={`quality-strip__track${open ? " is-open" : ""}`}
        role="radiogroup"
        aria-label={legend}
      >
        {visible.map((q) => (
          <label
            key={q.id}
            className={`quality-pill${value === q.id ? " is-active" : ""}`}
          >
            <input
              type="radio"
              name={name}
              value={q.id}
              checked={value === q.id}
              onChange={() => onChange(q.id)}
            />
            <span className="quality-pill__label">{q.label}</span>
            {q.estimated_size_mb != null && (
              <span className="quality-pill__size">
                ~{formatSizeMb(q.estimated_size_mb)}
              </span>
            )}
          </label>
        ))}
      </div>
    </div>
  );
}
