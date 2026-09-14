import assert from "node:assert/strict";
import test from "node:test";

import {
  chooseGraphLayoutName,
  LARGE_GRAPH_NODE_THRESHOLD,
  usesLargeGraphLayout,
} from "../dist/electron/shared/graph-layout-policy.js";

test("graph layout policy preserves semantic layout for ordinary projects", () => {
  assert.equal(chooseGraphLayoutName(0), "circle");
  assert.equal(chooseGraphLayoutName(2), "circle");
  assert.equal(chooseGraphLayoutName(3), "cose");
  assert.equal(chooseGraphLayoutName(LARGE_GRAPH_NODE_THRESHOLD), "cose");
});

test("graph layout policy bounds large-project layout cost", () => {
  assert.equal(chooseGraphLayoutName(LARGE_GRAPH_NODE_THRESHOLD + 1), "grid");
  assert.equal(usesLargeGraphLayout(500), true);
  assert.throws(() => chooseGraphLayoutName(-1), /non-negative/u);
});
