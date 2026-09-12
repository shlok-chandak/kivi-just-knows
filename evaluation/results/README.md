# Recorded evaluation runs

Every number in the project README comes from one of these. Intermediate runs
are not committed; these five are.

| run | what it is |
|---|---|
| `20260910-final/` | **The headline.** 60 questions, three arms: full 48/60, memory-without-filters 48/60, no memory 19/60. |
| `20260910-105757-original/` | The same question set before the marking scheme was revised, kept unedited. The scheme was corrected twice after seeing results — both times about what a date range means, both annotated in `questions.yaml`. This file is what makes that checkable rather than a claim. |
| `tools-20260910-132354/` | The four tools other than answering: 22/23. |
| `systems-20260911-085345/` | Latency, cost, model calls, database growth. |
| `20260911-182921/` | The same corpus rebuilt on a newer model (3.5 Flash-Lite), which scored 41/60 against 3.1's 48/60. Kept because the model choice was measured rather than assumed. |

Re-running any of them writes a new timestamped directory rather than
overwriting these. The `evaluation` screen in the app reads whatever is here.
