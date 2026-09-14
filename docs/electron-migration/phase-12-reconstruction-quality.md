# Phase 12: reconstruction quality baseline

Phase 12 makes extraction quality measurable before prompts or heuristics are
changed. The evaluator is offline, deterministic, and read-only: it never opens
a user project, invokes DSH, or writes accepted knowledge.

## Corpus policy

`tests/fixtures/reconstruction_quality/corpus-v1.json` contains six synthetic
Chinese fiction cases covering explicit markers, action speakers, natural
relationships, semantic enhancement, a negative control, and a deliberate
duplicate/conflict case.

The loader accepts a corpus only when it explicitly declares:

- `synthetic: true`;
- `contains_personal_data: false`;
- schema version 1;
- unique case IDs and structurally valid annotations.

Recorded enhanced candidates are deterministic fixtures for testing the merge
and evaluation pipeline. They are not claimed to represent current live DSH
performance. Live model outputs must be reviewed, sanitized, and versioned
before they can become a future benchmark snapshot.

## Metrics

Entities use normalized exact-name identity. Relationships use exact directed
`source`, `target`, and `label` identity. Metrics are micro-aggregated across
the corpus:

| Mode | Subject | Precision | Recall | F1 |
|---|---|---:|---:|---:|
| Local | Entities | 1.0000 | 0.8000 | 0.8889 |
| Local | Relations | 1.0000 | 0.7500 | 0.8571 |
| Combined fixture | Entities | 1.0000 | 1.0000 | 1.0000 |
| Combined fixture | Relations | 0.8000 | 1.0000 | 0.8889 |

The report also calculates confidence buckets and expected calibration error
(ECE). Calibration is diagnostic in this phase; it is not used to auto-accept
candidates. The corpus deliberately shows why: combined relationship recall
improves, but one plausible conflicting relation reduces precision.

## Duplicate and conflict diagnostics

Every runtime proposal records its producer set (`local`, `dsh`, or both).
Exact producer overlap is merged and counted once. Relationships sharing the
same unordered pair but differing in direction or label remain separate pending
candidates and increment the conflict count; the review drawer makes that count
visible instead of silently choosing one.

The synthetic baseline currently verifies three merged overlaps and one
relationship conflict in combined mode.

Phase 14 retains this corpus for historical comparison and makes
`corpus-v2.json` the default gate. The v2 corpus adds world concepts and
character fields while keeping the v1 entity and relationship cases.

Phase 16 introduced schema-v3 event coverage. Phase 24 expands the current v3
corpus from 8 to 14 cases across historical, science-fiction, ensemble,
multi-chapter, pronoun, and negative-control scenarios. Optional
`local_expected` annotations distinguish the deliberately narrow local fallback
contract from complete combined semantic expectations. All prior corpora remain
readable for historical comparison.

## Regression gate

Run the evaluator from the repository root:

```powershell
.venv\Scripts\python.exe scripts\evaluate_reconstruction.py --fail-on-regression
```

Use `--output <path>` to persist the schema-1 JSON report. Exit code 2 means at
least one precision, recall, or F1 threshold failed. CI runs this gate after the
Python test suite on every supported Python version.

Thresholds live with the corpus, below but close to the recorded baseline. A
quality-changing pull request must update implementation, annotations, and
threshold rationale together; lowering a threshold merely to pass CI is not an
acceptable calibration method.

## Exit criteria

- [x] Evaluation data is versioned, synthetic, and explicitly free of personal
  data.
- [x] Local and combined entity/relation precision, recall, and F1 are reported.
- [x] Confidence buckets and ECE are machine-readable.
- [x] Per-case TP/FP/FN makes regressions traceable.
- [x] Duplicate producer output is merged and counted.
- [x] Conflicting relationship directions or labels remain reviewable and are
  counted.
- [x] CI fails when explicit quality floors regress.
- [x] Evaluation never calls live DSH or mutates a project.

## Phase 13 continuation (completed)

Phase 13 adds the evidence-centered review workbench described in
[`phase-13-evidence-review-workbench.md`](phase-13-evidence-review-workbench.md):
duplicate/conflict filters, explicit selection, validated batch decisions,
confidence guidance, and visible audit/reopen semantics.
