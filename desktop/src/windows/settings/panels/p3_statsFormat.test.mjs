// Run:  node --test desktop/src/windows/settings/panels/p3_statsFormat.test.mjs   (Node >= 22.18, no build step)
import test from "node:test";
import assert from "node:assert/strict";
import {
  axisLabel, barGeometry, barTitle, formatCount, formatDuration, labelEvery, niceMax, streakLine,
} from "./p3_statsFormat.ts";

test("formatDuration", () => {
  assert.equal(formatDuration(0), "0 min");
  assert.equal(formatDuration(-5), "0 min");
  assert.equal(formatDuration(42), "42 s");
  assert.equal(formatDuration(60), "1 min");
  assert.equal(formatDuration(59 * 60), "59 min");
  assert.equal(formatDuration(3600), "1 h");
  assert.equal(formatDuration(3600 + 5 * 60), "1 h 05 min");
  assert.equal(formatDuration(130 * 3600 + 600), "130 h");
});

test("formatCount", () => {
  assert.equal(formatCount(1234.4, "en-US"), "1,234");
  assert.equal(formatCount(0, "en-US"), "0");
});

test("niceMax is a round number at or above the data", () => {
  assert.equal(niceMax(0), 10);
  assert.equal(niceMax(7), 10);
  assert.equal(niceMax(10), 10);
  assert.equal(niceMax(11), 20);
  assert.equal(niceMax(230), 500);
  assert.equal(niceMax(1240), 2000);
  assert.equal(niceMax(1), 2);
  for (const v of [1, 3, 9, 45, 99, 101, 777, 5000, 12345]) assert.ok(niceMax(v) >= v);
});

test("barGeometry scales bars, keeps them inside the plot and leaves zero days empty", () => {
  const pts = [0, 50, 100].map((words, i) => ({ key: `2026-10-0${i + 1}`, words, dictations: words ? 1 : 0 }));
  const { bars, max } = barGeometry(pts, 300, 100, 10);
  assert.equal(max, 100);
  assert.equal(bars[0].h, 0);
  assert.equal(bars[1].h, 50);
  assert.equal(bars[2].h, 100);
  assert.equal(bars[2].y, 10);                       // tallest bar touches the top of the plot
  for (const b of bars) assert.ok(b.x >= 0 && b.x + b.w <= 300);
});

test("tiny values still get a visible bar", () => {
  const { bars } = barGeometry([{ key: "2026-10-01", words: 1, dictations: 1 }, { key: "2026-10-02", words: 1000, dictations: 3 }], 200, 100, 0);
  assert.ok(bars[0].h >= 3);
});

test("an empty series does not crash", () => {
  const { bars, max } = barGeometry([], 300, 100, 0);
  assert.deepEqual(bars, []);
  assert.equal(max, 10);
});

test("labelEvery thins out long ranges", () => {
  assert.equal(labelEvery(7), 1);
  assert.equal(labelEvery(12), 2);
  assert.equal(labelEvery(30), 5);
  assert.ok(labelEvery(90) >= 11);
});

test("axis labels and tooltips", () => {
  assert.equal(axisLabel("2026-10-20", "day", 7, "en-US"), "Tue");
  assert.equal(axisLabel("2026-10-20", "day", 30, "en-US"), "Oct 20");
  assert.equal(axisLabel("2026-10", "month", 7, "en-US"), "Oct");
  assert.equal(barTitle({ key: "2026-10-20", words: 1240, dictations: 9 }, "day", "en-US"), "Tue, Oct 20: 1,240 words in 9 dictations");
  assert.equal(barTitle({ key: "2026-10-20", words: 1, dictations: 1 }, "day", "en-US"), "Tue, Oct 20: 1 word in 1 dictation");
  assert.equal(barTitle({ key: "2026-10-19", words: 0, dictations: 0 }, "day", "en-US"), "Mon, Oct 19: nothing dictated");
});

test("streakLine wording", () => {
  assert.match(streakLine(0, 0), /start a streak/);
  assert.match(streakLine(0, 4), /best streak is 4 days/);
  assert.match(streakLine(1, 1), /1 day so far/);
  assert.match(streakLine(5, 5), /5 days in a row, your longest yet/);
  assert.match(streakLine(2, 9), /2 days in a row. Your best is 9 days/);
});
