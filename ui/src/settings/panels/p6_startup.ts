// Phase 6: Settings > Island > "Start-up animation" switch.
// Uses the card / item / toggle helpers of main.ts (passed in) so Phase 4's restyle covers it.
import { PREF_KEY, storeEnabled } from "../../island/startup";
import { getFeatures, postFeatures } from "../../core/featuresCache";

interface Deps {
  card: (title: string, ...children: (Node | string | null | undefined | false)[]) => HTMLElement;
  item: (label: string, hint: string | HTMLElement | null, ...controls: (Node | string | null | undefined | false)[]) => HTMLElement;
  toggle: (on: boolean, onChange: (v: boolean) => void) => HTMLElement;
  flashSaved: (ok?: boolean) => void;
}

async function readPref(): Promise<boolean> {
  try {
    const data = await getFeatures();
    if (data && typeof data[PREF_KEY] === "boolean") return data[PREF_KEY] as boolean;
  } catch {
    /* backend offline: show the default */
  }
  return true;
}

export function p6StartupCard({ card, item, toggle, flashSaved }: Deps): HTMLElement {
  // The switch is built at once and set when the backend answers, so the panel never waits for it.
  const sw = toggle(true, (on) => {
    storeEnabled(on); // the island reads this mirror at launch
    void (async () => {
      try {
        flashSaved(await postFeatures({ [PREF_KEY]: on }));
      } catch {
        flashSaved(false);
      }
    })();
  });
  void readPref().then((on) => {
    sw.classList.toggle("on", on);
    sw.setAttribute("aria-pressed", String(on));
    storeEnabled(on);
  });
  return card(
    "Start-up",
    item(
      "Start-up animation",
      "When Lyra starts, the island opens from the notch, Mochi wakes up and a soft glow fades away (about 1.5 s). Press Esc or click to skip. It is not played when your system asks for reduced motion.",
      sw,
    ),
  );
}
