# Phase 23B: large-workspace performance

## Objective

Measure the current schema-v2 manuscript and relationship-graph paths at a
scale above normal fixtures, then change behavior only where the measurements
show a user-visible risk. This slice does not change the project schema, Sidecar
protocol, or graph truth; all nodes and edges remain available to the renderer.

## Reproducible harness

Run from `electron/`:

```powershell
npm run benchmark:workspace
npm run benchmark:renderer
```

The command builds the application and then:

1. starts the real Python Sidecar;
2. creates a disposable schema-v2 project from the migration fixture;
3. saves and reopens a 1,000,000-character chapter, verifying the canonical
   trailing newline and exact content;
4. constructs 500 nodes and 2,000 directed edges with the installed Cytoscape
   runtime;
5. runs the production graph-layout policy and a complete-collection filter
   mutation, recording timings and Node heap growth;
6. writes `build/phase-23b-performance.json` and deletes the disposable project.

The report records its Git base and whether the working tree was dirty. It is a
development regression baseline, not signed release evidence.

The second command starts the real Electron renderer in its isolated self-test
profile, switches away from and back to the million-character chapter, waits
for the CodeMirror instance and exact document-length marker, and records the
renderer process memory through Electron's current `app.getAppMetrics()` API.

## Measured bottleneck and policy

On the development Windows 11 machine, the previous `cose` policy needed
9,287.53 ms to lay out 500 nodes and 2,000 edges. A 300-node/900-edge sample
needed 630.18 ms, demonstrating a material scale cliff rather than a general
small-project problem.

The renderer now uses:

- `circle` below three nodes;
- `cose` from three through 200 nodes, preserving the relationship-oriented
  presentation for ordinary projects;
- `grid` above 200 nodes, with an explicit “大型图谱使用快速布局” notice.

No graph data is dropped. Search, type filters, selection, evidence navigation,
zoom, and fit continue to operate over the complete snapshot.

## Baseline result

The first passing optimized run recorded:

| Workload | Measurement |
| --- | ---: |
| Sidecar start | 181.72 ms |
| schema-v2 project creation/import | 32.07 ms |
| 1,000,000-character save | 68.50 ms |
| 1,000,001-character normalized reopen | 31.20 ms |
| manuscript client heap growth | 19,334,168 bytes |
| 500-node/2,000-edge Cytoscape construction | 28.94 ms |
| large-graph grid layout | 6.69 ms |
| complete-collection filter mutation | 10.64 ms |
| graph heap growth | 9,236,024 bytes |
| million-character CodeMirror readiness | 109.31 ms |
| renderer private-memory growth | 9,520 KiB |
| renderer working-set growth | 9,044 KiB |

The measured large-graph layout fell by approximately 99.93% from the previous
9.29-second result while retaining all 2,500 graph elements.

## Regression budgets

The explicit harness budgets are deliberately wider than the first measurement
to tolerate CI and workstation variance:

- Sidecar start: 5,000 ms;
- schema-v2 project creation/import: 5,000 ms;
- million-character save: 5,000 ms;
- million-character reopen: 3,000 ms;
- large-graph construction: 1,000 ms;
- large-graph layout: 1,500 ms;
- complete-collection graph filter mutation: 1,000 ms;
- graph heap growth: 256 MiB;
- million-character CodeMirror readiness: 3,000 ms;
- renderer private-memory growth: 256 MiB.

The benchmark exits non-zero when any budget is exceeded. It remains an explicit
maintainer command rather than part of every unit-test run because it creates a
large temporary project and performance timing is machine-sensitive.

## Verification and remaining boundary

- `npm test`: 18 of 18 Electron tests passed, including both layout-policy
  boundaries;
- renderer build and 480 KiB chunk budget passed;
- large-workspace benchmark passed and cleaned its temporary project;
- the real Electron renderer probe passed and cleaned its temporary schema-v2
  project.

The current measurements do not justify manuscript virtualization: the real
CodeMirror probe has substantial time and memory headroom, while virtualization
would complicate selection, history, and revision-safe saving. Browser/GPU graph
paint latency can still vary by machine, so it should be sampled during the
Windows 10/11 clean-client release gate rather than inferred from this headless
layout benchmark.
