// Pairs what you asked (typed or spoken) with Lyra's answer, in memory only: nothing is saved, and
// the Chat tab keeps only the conversation of the current opening (see views/chat.ts).

export interface Exchange { id: number; q: string; a: string; ts: number }

let latest: Exchange | null = null;
let pendingQ = "";
const listeners = new Set<() => void>();

export const Chats = {
  /** What the user just asked (spoken or typed). Paired with the next answer. */
  user(text: string): void { pendingQ = text.trim(); },
  /** Lyra's answer: completes the exchange. */
  assistant(text: string): void {
    const a = text.trim();
    if (!a) return;
    latest = { id: Date.now(), q: pendingQ, a, ts: Date.now() };
    pendingQ = "";
    for (const fn of listeners) fn();
  },
  /** What was asked and not answered yet. */
  pending(): string { return pendingQ; },
  /** The newest exchange (null until Lyra has answered something). */
  last(): Exchange | null { return latest; },
  onChange(fn: () => void): () => void { listeners.add(fn); return () => listeners.delete(fn); },
};
