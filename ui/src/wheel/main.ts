// Tab wheel window: a small transparent window that Rust (src-tauri/src/wheel.rs) shows at the
// cursor, or in the middle of the screen, while the wheel is up. The island decides when to open
// it and which tabs are on it; this page only draws the wheel and reports the choice.
//
//   island ──wheel_open──▶ Rust ──"wheel-open"──▶ here (draw it)
//   here ──wheel_pick(view)──▶ Rust ──"wheel-pick"──▶ island (go to that tab)
//   Rust ──"wheel-leave"──▶ here (play the closing animation; Rust hides the window a moment later)
//   island ──wheel_cmd("commit" | "stay")──▶ Rust ──"wheel-cmd"──▶ here (shortcut let go / tapped)

import "./wheel.css";
import { Bridge, IS_TAURI, onEvent, type WheelPayload } from "../core/bridge";
import type { IslandViewName } from "../core/layout";
import { buildWheel } from "../views/wheel";
import { installTracer } from "../core/trace";
installTracer();

const wheel = buildWheel({
  pick: (view) => void Bridge.wheelPick(view),
  dismiss: () => void Bridge.wheelClose(),
});
document.getElementById("stage")!.append(wheel.el);

void (async () => {
  await onEvent<WheelPayload>("wheel-open", (p) => {
    wheel.el.dataset.theme = p.theme;
    wheel.open({ hold: p.hold, current: p.current as IslandViewName, views: p.views as IslandViewName[] });
  });
  await onEvent<null>("wheel-leave", () => wheel.leave());
  await onEvent<string>("wheel-cmd", (cmd) => {
    if (cmd === "stay") wheel.keepOpen();
    // The shortcut was let go: open the tab you are pointing at, or close if you are pointing at none.
    else if (cmd === "commit" && wheel.holdMode && !wheel.commit()) void Bridge.wheelClose();
  });
})();

// Plain browser (npm run dev → /wheel.html): no Rust, so just show the wheel to look at it.
if (!IS_TAURI) {
  wheel.open({ current: "overview" });
}
