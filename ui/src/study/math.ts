// Math for the Study tab.
//
//   renderRich(el, text)   put text that may contain $…$ / $$…$$ / \(…\) / \[…\] into `el`, formulas drawn by KaTeX
//   renderTex(el, tex)     draw one formula (display or inline) and never throw
//   toLatex(typed)         "x^2/3 + sqrt(x) <= pi"  ->  "\frac{x^{2}}{3}+\sqrt{x}\le\pi"   (raw LaTeX passes through)
//
// KaTeX (~270 KB with its fonts) is loaded the first time a formula is drawn, not at start-up, so the
// island opens as fast as before. Until it arrives the plain text is shown, then it is replaced in place.

// @types/katex describes named exports, but the ESM build only has a default export holding them.
type Katex = { render(tex: string, element: HTMLElement, options?: import("katex").KatexOptions): void };

let katexP: Promise<Katex> | null = null;
let katex: Katex | null = null;

export function loadKatex(): Promise<Katex> {
  if (!katexP) {
    katexP = Promise.all([import("katex"), import("katex/dist/katex.min.css")])
      .then(([m]) => (katex = (m as unknown as { default: Katex }).default))
      .catch((e) => { katexP = null; throw e; });
  }
  return katexP;
}

// ── splitting text into text / formula segments ──────────────────────────────────────────────

export interface Segment { kind: "text" | "inline" | "display"; value: string }

// $$…$$, \[…\], \(…\), and $…$. A single $ only opens a formula when a non-space follows and only closes it
// when a non-space precedes and no digit follows ("it costs $5 and $6" stays text).
const MATH_RE = /\$\$([\s\S]+?)\$\$|\\\[([\s\S]+?)\\\]|\\\(([\s\S]+?)\\\)|\$(?=[^\s$])([^$\n]*?[^\s$\\]|[^\s$\\])\$(?!\d)/g;

export function splitMath(text: string): Segment[] {
  const out: Segment[] = [];
  let last = 0;
  for (const m of text.matchAll(MATH_RE)) {
    const at = m.index ?? 0;
    if (at > last) out.push({ kind: "text", value: text.slice(last, at) });
    const display = m[1] ?? m[2];
    out.push(display != null ? { kind: "display", value: display.trim() } : { kind: "inline", value: (m[3] ?? m[4] ?? "").trim() });
    last = at + m[0].length;
  }
  if (last < text.length) out.push({ kind: "text", value: text.slice(last) });
  return out;
}

export function hasMath(text: string): boolean {
  MATH_RE.lastIndex = 0;
  return splitMath(text).some((s) => s.kind !== "text");
}

// ── drawing ──────────────────────────────────────────────────────────────────────────────────

const OPTS = { throwOnError: false, strict: "ignore", trust: false, output: "html", maxSize: 20, maxExpand: 200 } as const;

export function renderTex(el: HTMLElement, tex: string, display = false): void {
  if (!katex) { el.textContent = tex; return; }
  try { katex.render(tex, el, { ...OPTS, displayMode: display }); }
  catch { el.textContent = tex; }
}

function paintRich(el: HTMLElement, segs: Segment[]): void {
  const nodes: Node[] = [];
  for (const s of segs) {
    if (s.kind === "text") { nodes.push(document.createTextNode(s.value)); continue; }
    const span = document.createElement(s.kind === "display" ? "div" : "span");
    span.className = s.kind === "display" ? "sm-display" : "sm-inline";
    renderTex(span, s.value, s.kind === "display");
    nodes.push(span);
  }
  el.replaceChildren(...nodes);
}

/** Text first (instant), formulas as soon as KaTeX is ready. Safe: text goes in as text nodes, never as HTML. */
export function renderRich(el: HTMLElement, text: string): void {
  const segs = splitMath(text || "");
  if (!segs.some((s) => s.kind !== "text")) { el.textContent = text || ""; return; }
  if (katex) { paintRich(el, segs); return; }
  el.textContent = segs.map((s) => s.value).join("");
  loadKatex().then(() => paintRich(el, segs)).catch(() => { /* plain text stays */ });
}

// ── typed notation -> LaTeX ──────────────────────────────────────────────────────────────────

const GREEK = new Set(("alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi rho sigma tau upsilon phi chi psi omega " +
  "Gamma Delta Theta Lambda Xi Pi Sigma Phi Psi Omega").split(" "));
const FUNCS = new Set("sin cos tan cot sec csc arcsin arccos arctan sinh cosh tanh ln log exp min max lim det gcd".split(" "));
// Commands end with a space so "a \ne b" can never run together into the unknown command \neb.
const RELATIONS: Array<[string, string]> = [
  ["<=>", "\\Leftrightarrow "], ["=>", "\\Rightarrow "], ["<=", "\\le "], [">=", "\\ge "], ["!=", "\\ne "], ["~=", "\\approx "],
  ["->", "\\to "], ["<-", "\\leftarrow "], ["=", "="], ["<", "<"], [">", ">"],
];

interface Node_ { tex: string; inner?: string }   // `inner` = the contents when the node was a ( … ) group

class Parser {
  private i = 0;
  private inAbs = 0;                 // inside |…| the closing bar must not start a new factor
  private s: string;
  constructor(s: string) { this.s = s; }

  parse(): string {
    const out = this.relations();
    this.ws();
    if (this.i < this.s.length) throw new Error("unexpected " + this.s[this.i]);
    return out;
  }

  private ws() { while (this.i < this.s.length && /\s/.test(this.s[this.i])) this.i++; }
  private peek(n = 1) { return this.s.slice(this.i, this.i + n); }

  /** a + b = c, d  — split on relations and commas, each piece is a sum. */
  private relations(): string {
    let out = this.sum();
    for (;;) {
      this.ws();
      if (this.peek() === ",") { this.i++; out += ",\\;" + this.sum(); continue; }
      const rel = RELATIONS.find(([k]) => this.peek(k.length) === k);
      if (!rel) return out;
      this.i += rel[0].length;
      out += rel[1] + this.sum();
    }
  }

  private sum(): string {
    let out = "";
    this.ws();
    for (;;) {
      this.ws();
      const c = this.peek();
      if (c === "-" && this.peek(2) === "->") return out;          // an arrow, not a minus
      if (c === "+" || c === "-" || c === "−") {
        this.i++;
        out += c === "+" ? "+" : "-";
        this.ws();
        out += this.product().tex;
      } else if (c === "±") {
        this.i++; out += "\\pm " + this.product().tex;
      } else if (!out) {
        out += this.product().tex;
      } else {
        return out;
      }
    }
  }

  /** factors joined by * or / or just written next to each other (2x, x(y+1), 3 sin x). */
  private product(): Node_ {
    let left = this.power();
    for (;;) {
      this.ws();
      const c = this.peek();
      if (c === "/" || c === "÷") {
        this.i++; this.ws();
        const right = this.power();
        left = { tex: `\\frac{${left.inner ?? left.tex}}{${right.inner ?? right.tex}}` };
      } else if (c === "*" || c === "·" || c === "×") {
        this.i++; this.ws();
        left = { tex: `${left.tex}\\cdot ${this.power().tex}` };
      } else if (this.startsFactor()) {
        const right = this.power();
        const glue = /\d$/.test(left.tex) && /^\d/.test(right.tex) ? "\\cdot " : "";   // "2 3" is 2·3, not 23
        left = { tex: left.tex + glue + right.tex };
      } else {
        return left;
      }
    }
  }

  private startsFactor(): boolean {
    const c = this.peek();
    if (!c) return false;
    if (c === "|") return this.inAbs === 0;
    if (c === "(" || c === "√" || c === "π") return true;
    return /[A-Za-z0-9.]/.test(c) && !/^(?:or|and)\b/i.test(this.s.slice(this.i));
  }

  private power(): Node_ {
    const base = this.atom();
    let tex = base.tex;
    let inner = base.inner;
    for (;;) {
      const c = this.peek();
      if (c === "^") {
        this.i++;
        tex += `^{${this.exponent()}}`; inner = undefined;
      } else if (c === "_") {
        this.i++;
        tex += `_{${this.exponent()}}`; inner = undefined;
      } else if (c === "²" || c === "³") {
        this.i++; tex += `^{${c === "²" ? 2 : 3}}`; inner = undefined;
      } else if (c === "!") {
        if (this.peek(2) === "!=") break;
        this.i++; tex += "!"; inner = undefined;
      } else break;
    }
    return { tex, inner };
  }

  private exponent(): string {
    this.ws();
    const c = this.peek();
    if (c === "-" || c === "+" || c === "−") { this.i++; return (c === "+" ? "+" : "-") + this.exponentAtom(); }
    return this.exponentAtom();
  }

  private exponentAtom(): string {
    this.ws();
    const c = this.peek();
    if (c === "(") { const g = this.group(); return g.inner ?? g.tex; }
    if (c === "{") { this.i++; const t = this.relations(); this.expect("}"); return t; }
    const num = /^\d+(?:\.\d+)?/.exec(this.s.slice(this.i));
    if (num) { this.i += num[0].length; return num[0]; }
    return this.atom().tex;                                   // x^n, x^pi, x^sqrt(2)
  }

  private expect(ch: string) {
    this.ws();
    if (this.peek() !== ch) throw new Error("expected " + ch);
    this.i++;
  }

  private group(): Node_ {
    this.expect("(");
    const inner = this.relations();
    this.expect(")");
    return { tex: `\\left(${inner}\\right)`, inner };
  }

  private atom(): Node_ {
    this.ws();
    const rest = this.s.slice(this.i);
    const c = rest[0];
    if (!c) throw new Error("unexpected end");
    if (c === "(") return this.group();
    if (c === "{") { this.i++; const t = this.relations(); this.expect("}"); return { tex: `{${t}}`, inner: t }; }
    if (c === "|") {
      this.i++; this.inAbs++;
      let t: string;
      try { t = this.sum(); } finally { this.inAbs--; }
      this.expect("|");
      return { tex: `\\left|${t}\\right|` };
    }
    if (c === "√") { this.i++; return { tex: `\\sqrt{${this.sqrtArg()}}` }; }
    if (c === "π") { this.i++; return { tex: "\\pi " }; }
    const num = /^\d+(?:[.,]\d+)?/.exec(rest);
    if (num) { this.i += num[0].length; return { tex: num[0] }; }
    const word = /^[A-Za-z]+/.exec(rest);
    if (word) return this.word(word[0]);
    this.i++;
    return { tex: c === "%" || c === "&" || c === "#" || c === "_" ? "\\" + c : c };
  }

  private sqrtArg(): string {
    this.ws();
    if (this.peek() === "(") { const g = this.group(); return g.inner ?? g.tex; }
    return this.power().tex;
  }

  private word(w: string): Node_ {
    const lower = w.toLowerCase();
    // sqrt(…), abs(…), root(n, x), frac(a, b), exp(…), functions
    const next = this.s[this.i + w.length];
    if (lower === "sqrt") {
      this.i += w.length;
      return { tex: `\\sqrt{${this.sqrtArg()}}` };
    }
    if (lower === "cbrt") { this.i += w.length; return { tex: `\\sqrt[3]{${this.sqrtArg()}}` }; }
    if ((lower === "abs") && next === "(") {
      this.i += w.length; const g = this.group();
      return { tex: `\\left|${g.inner}\\right|` };
    }
    if ((lower === "frac" || lower === "root") && next === "(") {
      this.i += w.length + 1;
      const a = this.sum(); this.expect(","); const b = this.sum(); this.expect(")");
      return { tex: lower === "frac" ? `\\frac{${a}}{${b}}` : `\\sqrt[${a}]{${b}}` };
    }
    if (lower === "inf" || lower === "infinity" || lower === "oo") { this.i += w.length; return { tex: "\\infty " }; }
    if (lower === "pi") { this.i += w.length; return { tex: "\\pi " }; }
    if (GREEK.has(w)) { this.i += w.length; return { tex: `\\${w} ` }; }
    if (FUNCS.has(lower)) {
      this.i += w.length;
      let tex = `\\${lower} `;
      if (this.peek() === "^") { this.i++; tex += `^{${this.exponent()}}`; }       // sin^2 x
      this.ws();
      if (this.peek() === "(") { const g = this.group(); return { tex: tex + g.tex }; }
      if (this.startsFactor()) tex += this.power().tex;
      return { tex };
    }
    if (lower === "e" && w.length === 1) { this.i += 1; return { tex: "e" }; }
    // an unknown word: single letters are variables; longer ones are written as text so "dx" stays "d x"
    if (w.length === 1 || w.length <= 2) {
      this.i += 1;
      return { tex: w[0] };
    }
    this.i += 1;                                                // a run of letters is a product of variables (xyz)
    return { tex: w[0] };
  }
}

/** Friendly keyboard notation -> LaTeX. Raw LaTeX (anything with a backslash) is returned as it is. Never throws. */
export function toLatex(typed: string): string {
  const src = (typed || "").trim();
  if (!src) return "";
  if (src.includes("\\")) return src;
  try { return new Parser(src).parse(); }
  catch { return src; }
}

/** The text sent to the backend as the answer: always LaTeX when it is maths, else the text as typed. */
export function answerText(typed: string): string {
  const t = (typed || "").trim();
  if (!t) return "";
  return t.includes("\\") ? t : toLatex(t);
}
