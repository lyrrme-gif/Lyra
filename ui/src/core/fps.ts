/** Target frame time for every visible animation: ~60 fps. On a 120/144 Hz display rAF fires faster; a gate of
 *  15 ms (not 16.7) lets every second/third frame through, so 60 Hz screens get all frames and fast screens get
 *  60 fps instead of burning the GPU at 120+. Hidden/parked windows still stop their loops entirely. */
export const FRAME_MS = 15;
