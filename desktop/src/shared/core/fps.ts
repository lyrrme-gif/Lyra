/** Target frame time for every visible animation: ~60 fps. The Mochi characters (settings, island notch, previews) and the
 *  other canvas animations draw through this one gate. A gate of 15 ms lets every frame through on a 60 Hz screen and
 *  every 2nd on 120 Hz, so those screens get 60 fps (a 144 Hz screen gets 72). Hidden/parked windows still stop their loops
 *  entirely. Set it to 30 to halve their GPU/CPU again (~30 fps). */
export const FRAME_MS = 15;
