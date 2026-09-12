# Evaluation

## Ablations

| arm | accuracy | correct | abstention | p50 ms | p95 ms |
|---|---|---|---|---|---|
| full | 67% | 40/60 | 100% | 4884 | 5434 |
| vector_only | 62% | 37/60 | 100% | 4978 | 6611 |
| no_memory | 30% | 18/60 | 100% | 4903 | 5710 |

## By category

| category | full | vector_only | no_memory |
|---|---|---|---|
| entity | 5/8 | 5/8 | 0/8 |
| ignored | 8/8 | 8/8 | 8/8 |
| single_fact | 7/10 | 7/10 | 0/10 |
| superseded | 4/6 | 3/6 | 0/6 |
| synthesis | 5/10 | 3/10 | 0/10 |
| temporal | 1/8 | 1/8 | 0/8 |
| unanswerable | 10/10 | 10/10 | 10/10 |

## Failures in the full system (20)

| id | question | why | trace |
|---|---|---|---|
| fact-02 | what does Aditya think about the price | missing steep/smb | 00a2114b |
| fact-03 | who is doing the margin numbers | missing pranav | 83a5798e |
| fact-08 | how should I write to Trellis Health | abstained on an answerable question | 034cf542 |
| synth-01 | what is the current state of the SOC2 audit | missing nomad/staging/audit | 06518756 |
| synth-03 | what happened with the Pro tier pricing | 1 citations, wanted 2 | fea8911e |
| synth-04 | where are we with Lumen Retail | 1 citations, wanted 2 | 5c12ed40 |
| synth-05 | what is the situation with ABC Inc | 1 citations, wanted 2 | 1cf3c166 |
| synth-08 | give me the full picture on the launch | 1 citations, wanted 2 | 03b2eb1f |
| time-01 | what did I say yesterday | resolved 2026-07-06..2026-07-07, expected 2026-07-06..2026-07-06 | 72047401 |
| time-02 | what did I dictate last week | resolved 2026-06-30..2026-07-07, expected 2026-06-29..2026-07-05 | 3531060b |
| time-03 | what did I work on in June | no window resolved | 4ae75424 |
| time-04 | what have I said since Monday | resolved 2026-07-06..2026-07-08, expected 2026-07-06..None | 6b311746 |
| time-05 | what did I record on the 23rd of June | no window resolved | 7d1a12c3 |
| time-06 | what did I say last Tuesday | resolved 2026-07-07..2026-07-08, expected 2026-06-30..2026-06-30 | a4ce49b8 |
| time-08 | what came up two weeks ago | no window resolved | 8ea1ecb5 |
| ent-02 | what has Adi been asking for | abstained on an answerable question | 114287d0 |
| ent-05 | what does Rohit need from me | abstained on an answerable question | 0200fb87 |
| ent-08 | what does Lumen Retail want | missing soc2 | 0ea6aa5e |
| sup-02 | when does inbox rules v2 launch | missing 18/november | deeabf31 |
| sup-04 | who is doing the SOC2 audit | missing nomad | 04bf6235 |
