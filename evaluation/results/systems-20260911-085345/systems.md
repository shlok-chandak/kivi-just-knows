# Systems metrics

Model(s): gemini-3.1-flash-lite
Corpus: 484 events, 196 memories

## Cost and calls

| | ingest | query |
|---|---|---|
| traces | 152 | 131 |
| model calls | 135 | 236 |
| input tokens | 148174 | 125271 |
| output tokens | 40238 | 16736 |
| cost USD | 0.0974 | 0.0564 |

Per event ingested: 0.279 model calls, $0.000201
Per query: 1.8 model calls, $0.000431

## Latency (ms)

| | n | p50 | p95 |
|---|---|---|---|
| ingest end to end | 152 | 4894.0 | 5932.0 |
| query end to end | 131 | 4920.0 | 6112.0 |
| retrieval only (no model) | 60 | 41.1 | 75.4 |
| stage: draft | 2 | 1060.0 | 1060.0 |
| stage: episode_consolidate | 135 | 1932.0 | 3288.0 |
| stage: parse | 131 | 1127.0 | 2803.0 |
| stage: recall | 103 | 1063.0 | 2226.0 |

## Storage

8312 kB total, 17586 bytes per event

| table | bytes | per event |
|---|---|---|
| embeddings | 5005312 | 10341.6 |
| events | 1228800 | 2538.8 |
| trace_steps | 557056 | 1150.9 |
| episodes | 319488 | 660.1 |
| memory_evidence | 319488 | 660.1 |
| jobs | 286720 | 592.4 |
| traces | 286720 | 592.4 |
| memories | 237568 | 490.8 |

## Throughput

- episode_consolidate: 146 jobs, 684.0s of work across 3177.2s (rate not meaningful, the jobs did not fill the span)
- embed: 4 jobs, 11.8s of work across 3185.8s (rate not meaningful, the jobs did not fill the span)
- episode_assign: 1 jobs, 0.4s of work across 0.4s (2.272/s)
- end to end: 0.15 events/second through consolidation
