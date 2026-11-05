/** Download chip / tile helpers used by the island. */

export const DL_C = 2 * Math.PI * 9; // ring circumference (r = 9 in a 24-unit box)

export function dlMiddleEllipsis(name: string, max: number): string {
  const n = name.trim();
  if (n.length <= max) return n;
  const dot = n.lastIndexOf(".");
  const ext = dot > 0 && n.length - dot <= 6 ? n.slice(dot) : "";
  const stem = ext ? n.slice(0, dot) : n;
  const keepEnd = Math.min(6, Math.max(0, stem.length - 8));
  const head = stem.slice(0, Math.max(6, max - ext.length - keepEnd - 1));
  return `${head}…${keepEnd ? stem.slice(-keepEnd) : ""}${ext}`;
}

export function dlTileHtml(phase: string, pct: number | null): string {
  const done = phase === "success";
  const failed = phase === "fail";
  const track = '<circle cx="12" cy="12" r="9" fill="none" stroke="#fff" stroke-opacity=".2" stroke-width="2.4"/>';
  let ring: string;
  if (pct == null && !done && !failed) {
    // Indeterminate: a fixed-length arc that the spinner loop keeps turning.
    ring = `<circle class="download-spinner-ring" cx="12" cy="12" r="9" fill="none" stroke="#fff" stroke-width="2.4" stroke-linecap="round" stroke-dasharray="${(DL_C * 0.29).toFixed(1)} ${(DL_C * 0.71).toFixed(1)}" transform="rotate(-90 12 12)"/>`;
  } else {
    const p = done ? 100 : failed ? 0 : (pct ?? 0);
    ring = `<circle cx="12" cy="12" r="9" fill="none" stroke="#fff" stroke-width="2.4" stroke-linecap="round" stroke-dasharray="${DL_C.toFixed(2)}" stroke-dashoffset="${(DL_C * (1 - p / 100)).toFixed(2)}" transform="rotate(-90 12 12)"/>`;
  }
  const glyph = done
    ? '<path d="M8 12.4l2.8 2.8 5.2-5.6" fill="none" stroke="#fff" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/>'
    : failed
      ? '<path d="M12 7.8v5M12 16.2v.2" fill="none" stroke="#fff" stroke-width="2.6" stroke-linecap="round"/>'
      : '<path d="M12 7.6v7.2m0 0l-3.2-3.2m3.2 3.2l3.2-3.2" fill="none" stroke="#fff" stroke-width="2.3" stroke-linecap="round" stroke-linejoin="round"/>';
  return `<svg class="dl-tile${done ? " done" : ""}" viewBox="0 0 24 24" aria-hidden="true">${track}${ring}${glyph}</svg>`;
}
