# Evaluation

## Ablations

| arm | accuracy | correct | abstention | p50 ms | p95 ms |
|---|---|---|---|---|---|
| full | 68% | 41/60 | 100% | 4912 | 16291 |
| vector_only | 67% | 40/60 | 100% | 5006 | 9835 |
| no_memory | 32% | 19/60 | 100% | 4848 | 17547 |

## By category

| category | full | vector_only | no_memory |
|---|---|---|---|
| entity | 3/8 | 3/8 | 1/8 |
| ignored | 8/8 | 8/8 | 8/8 |
| single_fact | 7/10 | 6/10 | 0/10 |
| superseded | 4/6 | 4/6 | 0/6 |
| synthesis | 4/10 | 4/10 | 0/10 |
| temporal | 5/8 | 5/8 | 0/8 |
| unanswerable | 10/10 | 10/10 | 10/10 |

## Failures in the full system (19)

| id | question | why | trace |
|---|---|---|---|
| fact-02 | what does Aditya think about the price | missing steep/smb | 32203b6d |
| fact-03 | who is doing the margin numbers | missing pranav | 5bdbc57f |
| fact-08 | how should I write to Trellis Health | abstained on an answerable question | 57d754cc |
| synth-01 | what is the current state of the SOC2 audit | abstained on an answerable question | fba3819f |
| synth-04 | where are we with Lumen Retail | 1 citations, wanted 2 | 22ebcb27 |
| synth-05 | what is the situation with ABC Inc | 1 citations, wanted 2 | e827681f |
| synth-08 | give me the full picture on the launch | 1 citations, wanted 2 | 115b4c8d |
| synth-09 | what is going on with Trellis Health | abstained on an answerable question | 6e39f663 |
| synth-10 | what are all my open commitments | 1 citations, wanted 2 | df24ee96 |
| time-02 | what did I dictate last week | resolved 2026-06-30..2026-07-07, expected 2026-06-29..2026-07-05 | 3d936989 |
| time-04 | what have I said since Monday | resolved 2026-07-06..2026-07-06, expected 2026-07-06..2026-07-08 | 208257c7 |
| time-08 | what came up two weeks ago | resolved 2026-06-29..2026-07-13, expected 2026-06-29..2026-07-05 | 15f8d46e |
| ent-02 | what has Adi been asking for | abstained on an answerable question | d495e002 |
| ent-05 | what does Rohit need from me | abstained on an answerable question | 668def82 |
| ent-06 | who owns inbox rules v2 | abstained on an answerable question | b842994d |
| ent-07 | what did Priya say she would do | named one without flagging: you approved priya to ship the feature. | 61fdb814 |
| ent-08 | what does Lumen Retail want | missing soc2 | 81e18d5b |
| sup-02 | when does inbox rules v2 launch | missing 18/november | 7929cf71 |
| sup-04 | who is doing the SOC2 audit | missing nomad | 5e632a71 |
