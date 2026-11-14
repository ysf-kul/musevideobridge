import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { acceptFrame, acceptStatus, normalize } from "../src/lib/sync.ts";
import type { Status } from "../src/lib/types.ts";
import { defaults } from "../src/lib/types.ts";
test("first-launch defaults retain Turkish and use balanced encoding FPS", () => {
  assert.equal(defaults.language, "tr");
  assert.equal(defaults.theme, "system");
  assert.equal(defaults.fps, 25);
  assert.equal(defaults.qscale, 8);
});
test("old seek/session generations cannot advance the preview", () => {
  const state = { playhead_generation: 5, state_revision: 10 } as Status;
  assert.equal(
    acceptFrame(state, {
      playhead_generation: 4,
      state_revision: 11,
      frame_index: 1,
      frame_pts_us: 0,
    }),
    false,
  );
  assert.equal(
    acceptFrame(state, {
      playhead_generation: 5,
      state_revision: 9,
      frame_index: 1,
      frame_pts_us: 0,
    }),
    false,
  );
  assert.equal(
    acceptFrame(state, {
      playhead_generation: 5,
      state_revision: 10,
      frame_index: 1,
      frame_pts_us: 0,
    }),
    true,
  );
});
test("aspect preservation uses rotated source dimensions", () => {
  const t = {
    x: 0,
    y: 0,
    width: 120,
    height: 240,
    rotation_deg: 90,
    lock_aspect: true,
  };
  assert.equal(normalize(t, 320, 240, "width").height, 160);
});
test("catalog keys and meaningful translations match", () => {
  const tr = JSON.parse(readFileSync("localization/tr.json", "utf8"));
  const en = JSON.parse(readFileSync("localization/en.json", "utf8"));
  assert.deepEqual(Object.keys(tr).sort(), Object.keys(en).sort());
  for (const catalog of [tr, en])
    for (const value of Object.values(catalog))
      assert.equal(typeof value === "string" && value.length > 0, true);
});

test("reordered or unconfirmed frame events cannot replace newer pixels", () => {
  const state = {
    playhead_generation: 1,
    state_revision: 2,
    frame_index: 10,
    frame_pts_us: 1_000_000,
  } as Status;
  assert.equal(
    acceptFrame(state, { ...state, frame_index: 9, frame_pts_us: 900_000 }),
    false,
  );
  assert.equal(acceptFrame(state, { ...state, state_revision: 3 }), false);
  assert.equal(
    acceptFrame(state, { ...state, frame_index: 11, frame_pts_us: 1_100_000 }),
    true,
  );
});

test("delayed status cannot restore a previous seek or transform", () => {
  const current = { state_revision: 8, playhead_generation: 3 } as Status;
  assert.equal(
    acceptStatus(current, {
      state_revision: 7,
      playhead_generation: 3,
    } as Status),
    false,
  );
  assert.equal(
    acceptStatus(current, {
      state_revision: 8,
      playhead_generation: 2,
    } as Status),
    false,
  );
  assert.equal(
    acceptStatus(current, {
      state_revision: 9,
      playhead_generation: 4,
    } as Status),
    true,
  );
});
