// Run:  node --test ui/src/study/math.test.mjs   (Node >= 22.18, no build step)
import test from "node:test";
import assert from "node:assert/strict";
import { answerText, hasMath, splitMath, toLatex } from "./math.ts";

test("toLatex: powers, roots, fractions", () => {
  assert.equal(toLatex("x^2"), "x^{2}");
  assert.equal(toLatex("x^2+2x+1"), "x^{2}+2x+1");
  assert.equal(toLatex("sqrt(x)"), "\\sqrt{x}");
  assert.equal(toLatex("sqrt(x^2+1)"), "\\sqrt{x^{2}+1}");
  assert.equal(toLatex("1/2"), "\\frac{1}{2}");
  assert.equal(toLatex("(x+1)/(x-1)"), "\\frac{x+1}{x-1}");
  assert.equal(toLatex("x^2/3"), "\\frac{x^{2}}{3}");
  assert.equal(toLatex("e^-x"), "e^{-x}");
  assert.equal(toLatex("x_1 + x_2"), "x_{1}+x_{2}");
});

test("toLatex: symbols, relations, functions", () => {
  assert.equal(toLatex("x <= pi"), "x\\le \\pi ");
  assert.equal(toLatex("a != b"), "a\\ne b");
  assert.equal(toLatex("x -> inf"), "x\\to \\infty ");
  assert.equal(toLatex("sin(x)"), "\\sin \\left(x\\right)");
  assert.equal(toLatex("sin^2 x"), "\\sin ^{2}x");
  assert.equal(toLatex("2*3"), "2\\cdot 3");
  assert.equal(toLatex("|x-1|"), "\\left|x-1\\right|");
  assert.equal(toLatex("x=2, x=3"), "x=2,\\;x=3");
  assert.equal(toLatex("theta"), "\\theta ");
});

test("toLatex: raw LaTeX and junk pass through, never throw", () => {
  assert.equal(toLatex("\\frac{1}{2}"), "\\frac{1}{2}");
  assert.equal(toLatex(""), "");
  assert.doesNotThrow(() => toLatex("((("));
  assert.doesNotThrow(() => toLatex("x^"));
  assert.equal(answerText("  1/2 "), "\\frac{1}{2}");
});

test("splitMath finds formulas and ignores prices", () => {
  const s = splitMath("Solve $x^2=4$ then $$\\int_0^1 x\\,dx$$ done");
  assert.deepEqual(s.map((x) => x.kind), ["text", "inline", "text", "display", "text"]);
  assert.equal(s[1].value, "x^2=4");
  assert.equal(hasMath("It costs $5 and $6 today"), false);
  assert.equal(hasMath("Use \\(a+b\\)"), true);
  assert.equal(hasMath("plain words"), false);
});
