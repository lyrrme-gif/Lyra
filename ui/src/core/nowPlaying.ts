// What is playing right now, shared between the island and Home's music widget.
export interface NowPlaying { running: boolean; status: string; title: string; artist: string; artUrl: string | null }

export const nowPlaying: NowPlaying = { running: false, status: "stopped", title: "", artist: "", artUrl: null };
const listeners = new Set<() => void>();

export function setNowPlaying(next: Partial<NowPlaying>) {
  Object.assign(nowPlaying, next);
  for (const fn of listeners) fn();
}
export function onNowPlaying(fn: () => void) { listeners.add(fn); return () => listeners.delete(fn); }
