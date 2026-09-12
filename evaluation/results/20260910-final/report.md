# Evaluation

## Ablations

| arm | accuracy | correct | abstention | p50 ms | p95 ms |
|---|---|---|---|---|---|
| full | 80% | 48/60 | 100% | 4609 | 15601 |
| vector_only | 80% | 48/60 | 100% | 4614 | 10340 |
| no_memory | 32% | 19/60 | 100% | 5254 | 11396 |

## By category

| category | full | vector_only | no_memory |
|---|---|---|---|
| entity | 6/8 | 6/8 | 1/8 |
| ignored | 8/8 | 8/8 | 8/8 |
| single_fact | 8/10 | 8/10 | 0/10 |
| superseded | 4/6 | 4/6 | 0/6 |
| synthesis | 8/10 | 8/10 | 0/10 |
| temporal | 4/8 | 4/8 | 0/8 |
| unanswerable | 10/10 | 10/10 | 10/10 |

## Failures in the full system (12)

| id | question | why | trace |
|---|---|---|---|
| fact-03 | who is doing the margin numbers | missing pranav | e8d9270e |
| fact-08 | how should I write to Trellis Health | missing full sentence/formal | d81776d5 |
| synth-03 | what happened with the Pro tier pricing | 1 citations, wanted 2 | 2c719bcf |
| synth-04 | where are we with Lumen Retail | 1 citations, wanted 2 | 20a9dbf0 |
| time-02 | what did I dictate last week | resolved 2026-06-30..2026-07-07, expected 2026-06-29..2026-07-05 | 9d1c4b30 |
| time-03 | what did I work on in June | no window resolved | b96a53c6 |
| time-05 | what did I record on the 23rd of June | no window resolved | d2663808 |
| time-08 | what came up two weeks ago | no window resolved | d2e74195 |
| ent-07 | what did Priya say she would do | named one without flagging: you assigned priya to manage inbox rules,  | 96ff55f0 |
| ent-08 | what does Lumen Retail want | abstained on an answerable question | b2d4d241 |
| sup-02 | when does inbox rules v2 launch | missing 18/november | 693ed712 |
| sup-04 | who is doing the SOC2 audit | missing nomad | 6d3491ca |
