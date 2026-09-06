# Video Editor Roadmap

Expand the existing CapCut-style **trim** (fast stream-copy + exact re-encode) into a production-grade editor. Ship high user value first, keep ops risk low, and let each phase build on the last.

**Current baseline:** single-range trim with waveform timeline, optional exact cut, ffmpeg-backed jobs.

---

## Implementation status

Phases **0–2** are implemented in the product:

| Phase | Status | Notes |
|---|---|---|
| 0 Harden | Done | Edit lock, progress + cancel flag, original + previous snapshots, restore/undo APIs, duration/size guards, audit events, WS trim fields |
| 1 Multi-cut | Done | `POST /edit/compose` + editor Multi-cut tab (split / delete / reorder / stitch) |
| 2 Insert | Done | `POST /edit/insert` multipart + Insert tab (clip at playhead, replace audio, optional crossfade) |

**APIs**

- `POST /api/jobs/{id}/trim` — single-range trim
- `POST /api/jobs/{id}/edit/compose` — multi-range stitch
- `POST /api/jobs/{id}/edit/insert` — insert upload / replace audio
- `POST /api/jobs/{id}/edit/restore` — restore original
- `POST /api/jobs/{id}/edit/undo` — undo last edit
- `POST /api/jobs/{id}/edit/cancel` — best-effort cancel in-flight edit

---

## Phase 0 — Harden what you have ✅

Done in code. Historical checklist:

- Reliable job status / cancel / timeout for long ffmpeg work
- Progress reporting + clear fail messages
- Original vs edited file versioning (undo last edit)
- Size / duration guards so workers stay healthy
- Audit logs for edit jobs

**Why first:** New features without this become support debt.

---

## Phase 1 — Multi-cut timeline ✅

Done in code. Historical checklist:

- Split clip into segments
- Delete ranges / keep multiple ranges
- Reorder segments
- Stitch / export one file

**Why:** Most users want “cut out parts,” not a full NLE. Reuses the waveform UI. Still mostly concat + copy when codecs match.

---

## Phase 2 — Insert media ✅

Done in code. Historical checklist:

- Insert another video at a playhead
- Insert / replace audio track
- Optional crossfade at join points

**Why:** Big “editor” feel with limited UI. Needs upload/library + re-encode when formats differ — ship after Phase 1.

---

## Phase 3 — Audio polish (cheap, feels premium)

- Mute / volume
- Fade in / out
- Duck original under BGM
- Extract audio-only

**Why:** High perceived quality, lower risk than heavy video filters. Fits download + share workflows.

---

## Phase 4 — Format for platforms

- Crop / aspect presets (9:16, 1:1, 16:9)
- Rotate / flip
- Simple speed change (0.5x–2x)

**Why:** Real production need for Shorts / Reels / TikTok. Always re-encodes — keep presets tight and duration/size capped.

---

## Phase 5 — Overlays & captions (high value, more product work)

- Text / title cards
- Auto or manual captions (SRT)
- Logo / watermark
- Basic PiP (optional, later in this phase)

**Why:** Differentiation. Harder UX + rendering; do after the core timeline is stable.

---

## Phase 6 — “Nice editor,” not required for v1

- Transitions beyond fade
- Color / brightness filters
- Keyframed volume / opacity
- Freeze frame
- Full project save / multi-track NLE
- Effects marketplace-style extras

**Why:** Cool, but costly and easy to ship half-broken. Defer until Phases 1–4 are solid in production.

---

## Production priority summary

| Priority | Ship | Skip / defer |
|---|---|---|
| Must | Phase 0 → 1 → 2 | Full CapCut clone |
| Should | Phase 3 → 4 | Fancy transitions, color grading |
| Later | Phase 5 (text/captions first) | Multi-track keyframe NLE |
| Avoid early | Unlimited re-encode, freeform effects | Anything without duration/size caps |

---

## Recommended first milestone

**Trim + multi-cut + stitch + undo last edit** → then **insert clip/audio**.

That is the smallest production-grade “real editor” users will actually finish jobs with.

---

## Feature backlog (reference)

### Timeline / cuts
- Split clip into segments
- Delete / keep ranges (multi-cut)
- Reorder clips
- Merge / stitch clips
- Freeze frame / hold a moment

### Insert / overlay
- Insert video or audio at a point
- Overlay picture-in-picture
- Add background music / voiceover track
- Duck original audio under BGM
- Add image / logo / watermark

### Audio
- Mute / volume keyframes
- Fade in / fade out
- Extract audio only
- Replace audio track

### Visual
- Crop / resize / aspect ratio (9:16, 16:9, 1:1)
- Rotate / flip
- Speed change (0.5x–2x)
- Brightness / contrast / simple filters
- Text / captions / subtitles
- Transitions (cut, fade, dissolve)

### Export / UX
- Format / quality presets
- Preview before export
- Undo / redo
- Save project / re-edit later
