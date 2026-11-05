// "Scroll down to go Home": on an expanded island that isn't on Home, scrolling down
// takes you back to Home. This is only the gesture logic (no DOM) so it can be tested.
//
//  • A gesture is a run of wheel events with gaps shorter than `gapMs`.
//  • It only counts if it STARTED with nothing left to scroll in the view, so a list
//    you were scrolling (chat, clipboard) scrolls first, and trackpad inertia after
//    reaching its end doesn't throw you out of the view.
//  • You have to scroll down a little (`threshold` px) — a light brush does nothing.
//  • Scrolling up inside the gesture cancels it.

export class WheelHomeGesture {
  private lastAt = -1e9;
  private armed = false;
  private sum = 0;

  constructor(private readonly threshold = 50, private readonly gapMs = 280) {}

  /** Feed one wheel event; returns true when the island should go Home. */
  feed(now: number, dx: number, dy: number, canScrollDown: () => boolean): boolean {
    if (now - this.lastAt > this.gapMs) {            // a new gesture begins
      this.armed = !canScrollDown();
      this.sum = 0;
    }
    this.lastAt = now;
    if (!this.armed) return false;
    if (dy < 0) { this.sum = 0; return false; }      // up cancels
    if (dy === 0 || Math.abs(dy) < Math.abs(dx)) return false;   // sideways scrolling
    this.sum += dy;
    if (this.sum >= this.threshold) {
      this.armed = false;
      this.sum = 0;
      return true;
    }
    return false;
  }
}
