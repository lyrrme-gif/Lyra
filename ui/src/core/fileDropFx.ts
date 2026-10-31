// A tiny bridge between whoever receives a file drop on the Clipboard tab and Mochi's reaction to it.
//
// Two code paths receive drops and neither should know about the animation:
//   - island.ts      the native Tauri drag stream (paths on disk)
//   - views/clipboard.ts   the page's own HTML5 drop (File objects, uploaded to the backend)
// Both just report what happened here; views/clipboardMochi.ts listens and plays the animation.
// Nothing in here touches the drop itself, so the existing save flows are unchanged.

export interface DropFxFile {
  name: string;
}

/** Where the drop landed, in client (viewport) coordinates, when the source knows it. */
export interface DropFxOrigin {
  x: number;
  y: number;
}

type StartFn = (files: DropFxFile[], from?: DropFxOrigin) => void;
type DoneFn = (ok: boolean, message?: string) => void;

const starts = new Set<StartFn>();
const dones = new Set<DoneFn>();

export const FileDropFx = {
  /** True while Mochi is still eating / reacting. The island waits for it before closing a tab a drag opened. */
  busy: false,

  /** Files were dropped and are being saved. */
  start(files: DropFxFile[], from?: DropFxOrigin): void {
    for (const fn of starts) { try { fn(files, from); } catch { /* an animation must never break a drop */ } }
  },

  /** The save finished (`ok`), or could not happen (`message` says why, shown next to Mochi). */
  done(ok: boolean, message?: string): void {
    for (const fn of dones) { try { fn(ok, message); } catch { /* same */ } }
  },

  onStart(fn: StartFn): () => void {
    starts.add(fn);
    return () => { starts.delete(fn); };
  },

  onDone(fn: DoneFn): () => void {
    dones.add(fn);
    return () => { dones.delete(fn); };
  },
};
