// ActivityManager — decides which activity owns the main island and which ones
// become secondary islands. Pure logic (no DOM) so it can be tested on its own.
//
// An activity is anything that can live in the island while the island is compact
// (timer, music, later: clipboard, recognition…). Generic by design: promotion
// works for any activity that can be main, not just Timer and Spotify.

export interface ActivityDef {
  id: string;
  /** Higher wins the main island. Time-sensitive things rank above ambient ones. */
  priority: number;
  canBeMain: boolean;
  canBeSecondary: boolean;
  /** Side of the main island this activity prefers when it is secondary. */
  side: "left" | "right";
  /** Stays alive even when idle (never auto-removed). */
  persistent?: boolean;
  /** Can only become main when the user clicks it (never by priority). */
  promotable?: boolean;
}

export const ACTIVITY_DEFS: ActivityDef[] = [
  { id: "timer", priority: 80, canBeMain: true, canBeSecondary: true, side: "left" },
  { id: "recognition", priority: 70, canBeMain: true, canBeSecondary: false, side: "left" },
  { id: "clipboard", priority: 60, canBeMain: true, canBeSecondary: true, side: "left" },
  // Screen recording: a small red pill beside the main island (like the timer), so you always see it is on.
  { id: "record", priority: 75, canBeMain: false, canBeSecondary: true, side: "left" },
  { id: "download", priority: 45, canBeMain: false, promotable: true, canBeSecondary: true, side: "right" },
  { id: "music", priority: 50, canBeMain: true, canBeSecondary: true, side: "right" },
];

export interface ActivityLayout {
  main: string | null;
  secondary: Array<{ id: string; side: "left" | "right" }>;
}

export class ActivityManager {
  private defs = new Map<string, ActivityDef>();
  private active = new Set<string>();
  /** The activity the user explicitly promoted to the main island, if still alive. */
  private promoted: string | null = null;

  constructor(defs: ActivityDef[] = ACTIVITY_DEFS) {
    for (const d of defs) this.defs.set(d.id, d);
  }

  /** Reports whether an activity is alive. Returns true when the layout may have changed. */
  set(id: string, on: boolean): boolean {
    if (!this.defs.has(id)) return false;
    const was = this.active.has(id);
    if (on === was) return false;
    if (on) {
      this.active.add(id);
      // A new activity starting is new context: priority decides again.
      this.promoted = null;
    } else {
      this.active.delete(id);
      if (this.promoted === id) this.promoted = null;
    }
    return true;
  }

  isActive(id: string): boolean {
    return this.active.has(id);
  }

  /** Makes `id` the main island; the previous main becomes secondary. */
  promote(id: string): boolean {
    const d = this.defs.get(id);
    if (!d || !(d.canBeMain || d.promotable) || !this.active.has(id) || this.main() === id) return false;
    this.promoted = id;
    return true;
  }

  main(): string | null {
    if (this.promoted && this.active.has(this.promoted)) return this.promoted;
    let best: ActivityDef | null = null;
    for (const id of this.active) {
      const d = this.defs.get(id)!;
      if (!d.canBeMain) continue;
      if (!best || d.priority > best.priority) best = d;
    }
    return best ? best.id : null;
  }

  layout(): ActivityLayout {
    const main = this.main();
    const rest = [...this.active]
      .filter((id) => id !== main && this.defs.get(id)!.canBeSecondary)
      .sort((a, b) => this.defs.get(b)!.priority - this.defs.get(a)!.priority);
    // Each side holds one island; extra activities beyond that stay in the background.
    const taken = new Set<string>();
    const secondary: ActivityLayout["secondary"] = [];
    for (const id of rest) {
      const pref = this.defs.get(id)!.side;
      const side = !taken.has(pref) ? pref : pref === "left" ? "right" : "left";
      if (taken.has(side)) continue;
      taken.add(side);
      secondary.push({ id, side });
    }
    return { main, secondary };
  }
}
