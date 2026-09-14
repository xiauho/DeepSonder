import assert from "node:assert/strict";
import test from "node:test";

import { buildReleaseReadiness } from "../scripts/release-readiness-gate.mjs";

const sourceCommit = "1".repeat(40);

function candidateGate() {
  return {
    schema_version: 1,
    result: "passed",
    package_kind: "electron-only",
    version: "2.1.0-beta",
    source_commit: sourceCommit,
    release_key_id: "a".repeat(64),
    installer_sha256: "b".repeat(64),
    portable_sha256: "c".repeat(64),
    clients: [{ client: "windows-10" }, { client: "windows-11" }],
    gates: {
      signatures: "passed",
      install_and_uninstall: "passed",
      schema_v2_read_only_open: "passed",
      schema_v2_workflow_surface: "passed",
      schema_v2_ai_review_surface: "passed",
      backup_and_portable_recovery: "passed",
    },
  };
}

function aiGate() {
  return {
    schema_version: 1,
    app_version: "2.1.0-beta",
    source_commit: sourceCommit,
    data_scope: "disposable_synthetic_manuscript_only",
    passed: true,
    cases: [
      { kind: "connection", passed: true, status: "succeeded", error_type: "" },
      { kind: "check", passed: true, status: "succeeded", error_type: "", review_first: true, adoption: "discarded" },
      { kind: "expand", passed: true, status: "succeeded", error_type: "", review_first: true, adoption: "discarded" },
      { kind: "continuation", passed: true, status: "succeeded", error_type: "", review_first: true, adoption: "writing_applied_to_disposable_project" },
      { kind: "memory", passed: true, status: "succeeded", error_type: "", review_first: true, adoption: "memory_committed_to_disposable_project", memory_file_created: true },
    ],
  };
}

test("release readiness binds signed candidate and live AI to one source", () => {
  const report = buildReleaseReadiness(candidateGate(), aiGate());
  assert.equal(report.result, "passed");
  assert.equal(report.decision, "eligible_for_manual_release_review");
  assert.equal(report.source_commit, sourceCommit);
  assert.equal(report.evidence.automatic_publication_authorized, false);
});

test("release readiness rejects a different source or bypassed AI review", () => {
  const otherAi = aiGate();
  otherAi.source_commit = "2".repeat(40);
  assert.throws(
    () => buildReleaseReadiness(candidateGate(), otherAi),
    /同一源码提交/u,
  );

  const unreviewedAi = aiGate();
  unreviewedAi.cases.find((item) => item.kind === "continuation").review_first = false;
  assert.throws(
    () => buildReleaseReadiness(candidateGate(), unreviewedAi),
    /审核优先边界/u,
  );
});
