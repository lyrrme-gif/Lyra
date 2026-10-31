// Symbol palette for the Study tab's answer field.
//
// Each key inserts a LaTeX template. "|" marks where the caret lands; every empty {} left in the template is
// a slot, and Tab jumps to the next one (like a fraction's numerator → denominator). The field accepts LaTeX
// as well as plain typing ("x^2/3", "sqrt(x)"), so the two styles mix freely.

export interface PaletteKey { tex: string; insert: string; title: string }
export interface PaletteGroup { id: string; label: string; keys: PaletteKey[] }

const k = (tex: string, insert: string, title: string): PaletteKey => ({ tex, insert, title });

export const PALETTE: PaletteGroup[] = [
  { id: "basic", label: "Basic", keys: [
    k("\\tfrac{a}{b}", "\\frac{|}{}", "Fraction"),
    k("x^{n}", "^{|}", "Power"),
    k("x_{n}", "_{|}", "Subscript"),
    k("\\sqrt{x}", "\\sqrt{|}", "Square root"),
    k("\\sqrt[n]{x}", "\\sqrt[|]{}", "n-th root"),
    k("|x|", "\\left|\\right|", "Absolute value"),
    k("(\\,)", "\\left(|\\right)", "Brackets"),
    k("\\pm", "\\pm ", "Plus or minus"),
    k("\\cdot", "\\cdot ", "Multiply"),
    k("\\div", "\\div ", "Divide"),
  ] },
  { id: "rel", label: "Relations", keys: [
    k("\\le", "\\le ", "Less than or equal"),
    k("\\ge", "\\ge ", "Greater than or equal"),
    k("\\ne", "\\ne ", "Not equal"),
    k("\\approx", "\\approx ", "Approximately"),
    k("\\to", "\\to ", "Tends to"),
    k("\\infty", "\\infty ", "Infinity"),
    k("\\in", "\\in ", "Element of"),
    k("\\Rightarrow", "\\Rightarrow ", "Implies"),
  ] },
  { id: "calc", label: "Calculus", keys: [
    k("\\int", "\\int_{|}^{} \\,dx", "Integral"),
    k("\\sum", "\\sum_{i=|}^{n}", "Sum"),
    k("\\lim", "\\lim_{x \\to |}", "Limit"),
    k("\\tfrac{d}{dx}", "\\frac{d}{dx}|", "Derivative"),
    k("\\partial", "\\partial ", "Partial"),
    k("\\ln", "\\ln\\left(|\\right)", "Natural log"),
    k("e^{x}", "e^{|}", "Exponential"),
    k("\\log_{b}", "\\log_{|}", "Log base b"),
  ] },
  { id: "trig", label: "Trig", keys: [
    k("\\sin", "\\sin\\left(|\\right)", "Sine"),
    k("\\cos", "\\cos\\left(|\\right)", "Cosine"),
    k("\\tan", "\\tan\\left(|\\right)", "Tangent"),
    k("\\sin^{2}", "\\sin^{2}\\left(|\\right)", "Sine squared"),
    k("\\pi", "\\pi ", "Pi"),
    k("\\theta", "\\theta ", "Theta"),
    k("\\alpha", "\\alpha ", "Alpha"),
    k("\\beta", "\\beta ", "Beta"),
  ] },
  { id: "alg", label: "Algebra", keys: [
    k("\\binom{n}{k}", "\\binom{|}{}", "Choose"),
    k("\\left[\\begin{smallmatrix}a&b\\\\c&d\\end{smallmatrix}\\right]", "\\begin{pmatrix} | & \\\\  &  \\end{pmatrix}", "2×2 matrix"),
    k("\\vec{v}", "\\vec{|}", "Vector"),
    k("\\bar{x}", "\\bar{|}", "Mean"),
    k("\\mathbb{R}", "\\mathbb{R}", "Real numbers"),
    k("\\forall", "\\forall ", "For all"),
    k("\\exists", "\\exists ", "There exists"),
    k("\\emptyset", "\\emptyset ", "Empty set"),
  ] },
];

type Field = HTMLInputElement | HTMLTextAreaElement;

/** Insert a template at the caret (replacing any selection) and put the caret on the "|" marker. */
export function insertTemplate(field: Field, template: string): void {
  const start = field.selectionStart ?? field.value.length;
  const end = field.selectionEnd ?? start;
  const selected = field.value.slice(start, end);
  const mark = template.indexOf("|");
  let text = template.replace("|", "");
  let caret = start + (mark < 0 ? text.length : mark);
  if (selected && mark >= 0) {                                       // wrap the selection: sqrt of what was highlighted
    text = template.replace("|", selected);
    caret = start + text.length;
  }
  field.setRangeText(text, start, end, "end");
  field.setSelectionRange(caret, caret);
  field.dispatchEvent(new Event("input", { bubbles: true }));
  field.focus();
}

/** Tab: jump to the next empty {} / [] slot after the caret. Returns true if it moved. */
export function nextSlot(field: Field): boolean {
  const from = field.selectionEnd ?? 0;
  const rest = field.value.slice(from);
  const m = /\{\}|\[\]/.exec(rest);
  if (!m) return false;
  const at = from + (m.index ?? 0) + 1;
  field.setSelectionRange(at, at);
  return true;
}
