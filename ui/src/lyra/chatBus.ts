import { State } from "../core/state";
import { Sound } from "../core/sound";

// The island no longer keeps a conversation: what Lyra heard and her answer are
// shown by the caption window (src/caption), and rich results by the island itself.
// This module only tracks the "waiting for an answer" state of the typing box.
let sending = false;

export function isSending(): boolean {
  return sending;
}

export function setSending(v: boolean): void {
  sending = v;
}

/** A question typed in the island: lock the box until Lyra answers. */
export function pushUser(_text: string): void {
  State.stateOverride = "thinking";
  State.notify();
}

/** `mochi`: the reply is a Mochi sound (client.ts plays it), so the generic "finish" chime stays out of its way. */
export function pushReply(_text: string, mochi = false): void {
  State.stateOverride = null;
  sending = false;
  if (!mochi) Sound.play("finish");
  State.notify();
}

export function pushError(text: string): void {
  State.stateOverride = null;
  sending = false;
  State.noteMessage = text;
  State.view = "note";
  Sound.play("error");
  State.notify();
}
