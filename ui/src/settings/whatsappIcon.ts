// The WhatsApp mark used by Settings: a speech bubble with a phone handset inside, white on WhatsApp green.
// One definition so the sidebar tab (Apps) and the WhatsApp card show the same icon.

/** Inner SVG markup (24×24 box, white) for the coloured tile. */
export const WHATSAPP_GLYPH =
  '<path fill="none" stroke="#fff" stroke-width="1.9" stroke-linejoin="round" d="M12 3a9 9 0 0 0-7.7 13.6L3 21l4.5-1.2A9 9 0 1 0 12 3z"/>' +
  '<g transform="translate(7.1 7.1) scale(.4)"><path fill="#fff" d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"/></g>';

/** The tile background: the green of the WhatsApp mark. */
export const WHATSAPP_BG = "linear-gradient(#4be08a,#12a64d)";

/** A ready-made SVG element of the glyph at `size` px (for use inside a tile). */
export function whatsappGlyph(size: number): SVGSVGElement {
  const el = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  el.setAttribute("viewBox", "0 0 24 24");
  el.setAttribute("width", String(size));
  el.setAttribute("height", String(size));
  el.setAttribute("aria-hidden", "true");
  el.innerHTML = WHATSAPP_GLYPH;
  return el;
}
