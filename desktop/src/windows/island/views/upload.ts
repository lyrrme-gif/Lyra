// Drop flow: reacts to a file approaching, shows quiet progress, then offers the next step.
// Three small states of the same island: drop target · progress · "what next".

import { h, clear, svg } from "../../../shared/views/dom";
import { ICONS } from "../../../shared/views/icons";
import { State } from "../../../shared/core/state";
import type { ViewActions, ViewHost } from "./views";

export function buildUpload(): ViewHost {
  const card = h("div", { class: "up2 up2-drop" },
    h("div", { class: "up2-ring" }),
    h("div", { class: "up2-ic" }, svg(ICONS.doc, 30)),
    h("div", { class: "up2-title", text: "Drop it here" }),
    h("div", { class: "up2-sub", text: "Release to add it to Lyra" }),
  );
  const el = h("div", { class: "view" }, card);
  return {
    el,
    sync() { card.classList.toggle("over", State.fileDragOver); },
  };
}

export function buildUploading(): ViewHost {
  const name = h("div", { class: "up2-title" });
  const pct = h("div", { class: "up2-sub" });
  const fill = h("div", { class: "up2-fill" });
  const card = h("div", { class: "up2 up2-progress" },
    h("div", { class: "up2-ic" }, svg(ICONS.doc, 26)),
    name, pct,
    h("div", { class: "up2-track" }, fill),
  );
  const el = h("div", { class: "view" }, card);
  return {
    el,
    sync() {
      const done = State.uploadProgress >= 0.999;
      name.textContent = State.droppedFile?.name ?? "File";
      pct.textContent = done ? "Ready" : `${Math.round(State.uploadProgress * 100)}%`;
      fill.style.width = `${Math.round(State.uploadProgress * 100)}%`;
      card.classList.toggle("done", done);
    },
  };
}

export function buildChoose(actions: ViewActions): ViewHost {
  const title = h("div", { class: "up2-title" });
  const sub = h("div", { class: "up2-sub", text: "What do you want to do with it?" });
  const card = h("div", { class: "up2 up2-choose" },
    h("div", { class: "up2-ic" }, svg(ICONS.doc, 26)),
    title, sub,
    h("div", { class: "up2-actions" },
      h("button", { class: "up2-btn primary", type: "button", text: "Ask a question", onclick: () => actions.setView("prompt") }),
      h("button", { class: "up2-btn", type: "button", text: "Cancel", onclick: () => actions.setView(State.defaultView()) }),
    ),
  );
  const el = h("div", { class: "view" }, card);
  return {
    el,
    sync() { clear(title); title.textContent = State.droppedFile?.name ?? "File"; },
  };
}
