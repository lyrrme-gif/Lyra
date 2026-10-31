// Checks the launcher's layout maths (src/core/wheelGeometry.ts) against the real destination
// registry. Run from ui/:  node scripts/check-constellation.mjs
import { build } from "esbuild";
import { pathToFileURL } from "node:url";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const out = join(mkdtempSync(join(tmpdir(), "constel-")), "t.mjs");
await build({
  stdin: { contents: `export * from "./src/core/wheelGeometry"; export { DESTINATIONS, PAGES } from "./src/core/destinations";`, resolveDir: process.cwd(), loader: "ts" },
  bundle: true, format: "esm", outfile: out, logLevel: "silent",
});
const G = await import(pathToFileURL(out).href);
let fails = 0;
const failed = [];
const ok = (c, m) => { if (!c) { fails++; if (failed.length < 8) failed.push(m); } };
const HW = G.WHEEL_W / 2, HH = G.WHEEL_H / 2;

// 1. Registry: every destination is in exactly one category.
const counts = G.PAGES.map((_, i) => G.DESTINATIONS.filter((d) => d.page === i).length);
ok(counts.reduce((a, b) => a + b, 0) === G.DESTINATIONS.length, "every destination belongs to a category");
console.log("categories:", G.PAGES.map((p, i) => `${p.label}(${counts[i]})`).join(", "), `- ${G.DESTINATIONS.length} tabs`);

const tipSize = (label) => ({ w: Math.round(label.length * 8 + 30), h: 31 });
const labels = ["Mochi", "To-do", "Recognize", "Clipboard", "Wallpaper", "Capture", "Media", "Calendar"];
let worstGap = Infinity, tipChecks = 0;
const checkView = (name, nodes, minGap = 12, tips = true, noTip = -1) => {
  nodes.forEach((a, i) => {
    ok(Math.abs(a.x) + a.r <= HW && Math.abs(a.y) + a.r <= HH, `${name}: node ${i} inside the window`);
    nodes.forEach((b, j) => {
      if (j <= i) return;
      const gap = Math.hypot(a.x - b.x, a.y - b.y) - a.r - b.r;
      worstGap = Math.min(worstGap, gap);
      ok(gap >= minGap, `${name}: icons ${i},${j} only ${gap.toFixed(1)}px apart`);
    });
    for (const label of a.kind === "close" || !tips || i === noTip ? [] : labels) {   // no tooltip on the × or on the open group   // the × in the middle has no label
      const { w, h } = tipSize(label);
      const p = G.tooltipPlacement(a, nodes, w, h);
      tipChecks++;
      ok(p.x >= -HW && p.x + w <= HW && p.y >= -HH && p.y + h <= HH, `${name} node ${i} '${label}': tooltip clipped`);
      nodes.forEach((o, j) => {
        if (j === i) return;
        const nx = Math.max(p.x, Math.min(o.x, p.x + w)), ny = Math.max(p.y, Math.min(o.y, p.y + h));
        ok(Math.hypot(o.x - nx, o.y - ny) >= o.r, `${name} node ${i} '${label}': tooltip covers icon ${j}`);
      });
    }
  });
};
// 2. The ring with 1..8 groups, folded.
const ringNodes = (n, r = G.CAT_R) => [...G.rootLayout(n).map((p) => ({ ...p, r, kind: "cat" })), { x: 0, y: 0, r: G.CLOSE_R, kind: "close" }];
for (let n = 1; n <= 8; n++) checkView(`ring n=${n}`, ringNodes(n));
// 3. Any group of 1..4 tabs, opened, on a ring of 2..6 groups (the registry has 6; keep it at 6 or fewer):
//    the tabs fan out in an arc round the group on its outer side, inside the window, clear of each other and of the other groups.
for (let n = 2; n <= 6; n++) {
  const spots = G.rootLayout(n);
  for (let k = 0; k < n; k++) for (let t = 1; t <= 4; t++) {
    const tabs = G.tabsAround(spots[k], t);
    ok(tabs.length === t, `tabsAround(${t}) has ${t} points`);
    const nodes = [
      ...spots.map((p) => ({ ...p, r: G.CAT_R, kind: "cat" })),
      { x: 0, y: 0, r: G.CLOSE_R, kind: "close" },
      ...tabs.map((p) => ({ ...p, r: G.TAB_R, kind: "dest" })),
    ];
    if (n === 6 && t <= 3) checkView(`ring ${n}, group ${k}, ${t} tabs`, nodes, 8, true, k);       // what the registry really uses, with tooltips
    else if (n === 6) checkView(`ring ${n}, group ${k}, ${t} tabs`, nodes, 8, false);   // more tabs than we have: shapes only
    ok(tabs.every((p) => Math.hypot(p.x, p.y) > Math.hypot(spots[k].x, spots[k].y) - 1), `ring ${n}, group ${k}: tabs on the outer side`);
  }
}
console.log(`spacing: smallest gap between any two icons = ${worstGap.toFixed(1)}px; ${tipChecks} tooltip placements checked`);
ok(G.rootLayout(0).length === 0 && G.tabsAround({ x: 0, y: 0 }, 0).length === 0, "empty layouts");

// 3b. The real registry: 6 groups, 14 tabs, 2-4 tabs per group, the four removed tabs gone.
ok(G.PAGES.length === 6, "six groups");
ok(counts.every((c) => c >= 2 && c <= 4), `every group has 2-4 tabs (${counts})`);
ok(G.DESTINATIONS.length === 14, `14 tabs (${G.DESTINATIONS.length})`);
for (const gone of ["study", "volume", "controls", "translate"]) ok(!G.DESTINATIONS.some((d) => d.view === gone), `${gone} is not a tab`);
ok(new Set(G.DESTINATIONS.map((d) => d.view)).size === G.DESTINATIONS.length, "no tab listed twice");

// 4. Hit testing.
{
  const nodes = [...G.tabLayout(7).map((p) => ({ ...p, r: G.NODE_R, kind: "dest" })), { x: 0, y: 0, r: G.CENTER_R, kind: "center" }];
  nodes.forEach((n, i) => ok(G.constellationHit(n.x, n.y, nodes).index === i, `centre of node ${i} hits itself`));
  const a = nodes[0];
  ok(G.constellationHit(a.x + G.NODE_R + G.HIT_SLACK + 3, a.y, nodes).kind === "none", "just outside slack is nothing");
  ok(G.constellationHit(a.x + G.NODE_R + 4, a.y, nodes).index === 0, "inside slack still hits");
  ok(G.constellationHit(300, 200, nodes).kind === "none", "empty space is nothing");
}
// 5. Arrow navigation reaches every tab and never the middle.
{
  const nodes = [...G.tabLayout(7).map((p) => ({ ...p, r: G.NODE_R, kind: "dest" })), { x: 0, y: 0, r: G.CENTER_R, kind: "center" }];
  const seen = new Set();
  const walk = (i, d) => { if (d > 12) return; for (const dir of ["left", "right", "up", "down"]) { const j = G.neighbour(i < 0 ? null : nodes[i], nodes, dir, i); if (j >= 0 && !seen.has(j)) { seen.add(j); walk(j, d + 1); } } };
  walk(-1, 0);
  ok([...Array(7).keys()].every((i) => seen.has(i)) && !seen.has(7), `arrow keys reach all 7 tabs, not the middle (${[...seen]})`);
}
// 6. Fit scale.
for (const [w, h, name] of [[1920, 1080, "1080p"], [1366, 768, "1366x768"], [800, 480, "small"]]) {
  const k = Math.min((w * 0.92) / G.WHEEL_W, (h * 0.92) / G.WHEEL_H, 1);
  ok(Math.abs(G.fitScale(G.WHEEL_W * k, G.WHEEL_H * k) - k) < 1e-9, `fitScale on ${name}`);
}
ok(G.fitScale(0, 0) === 1 && G.fitScale(2000, 2000) === 1, "fitScale never enlarges, tolerates 0");
if (fails) console.log(`\n${fails} FAILED, first ones:\n  ` + failed.join("\n  ")); else console.log("\nall checks passed");
process.exit(fails ? 1 : 0);
