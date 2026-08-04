# Dev transcript catch-up through 2026-08-02

## Result

The cultivated transcript corpus now contains every whole day from 2026-07-20
through 2026-08-02: **14/14 daily Markdown reports**, **14/14 paired JSON caches**,
and **1,779/1,779 source owner turns**. No partial report was generated for
2026-08-03 or 2026-08-04.

The repository uses ISO week labels. The existing convention therefore names
the two sprint weeks `2026-W30` (Jul 20-26) and `2026-W31` (Jul 27-Aug 2), not
W31/W32. The refreshed W31 weekly cache contains 7 days, from Jul 27 through
Aug 2.

## Before and after

Before this task, 13 daily reports existed: Jul 20-31 and Aug 2. Aug 1 was
missing. A source-to-cache comparison also found that Jul 31 contained 116 of
119 source turns.

This task:

- generated Aug 1 from all 122 source turns;
- incrementally classified the 3 missing Jul 31 turns;
- retained the already-complete Aug 2 report (194/194 turns);
- refreshed the affected daily and W31 weekly artifacts;
- regenerated no partial day after Aug 2.

Final source/cache counts:

| Day | Cached turns | Source turns | Delta |
|---|---:|---:|---:|
| 2026-07-20 | 22 | 22 | 0 |
| 2026-07-21 | 48 | 48 | 0 |
| 2026-07-22 | 219 | 219 | 0 |
| 2026-07-23 | 206 | 206 | 0 |
| 2026-07-24 | 153 | 153 | 0 |
| 2026-07-25 | 89 | 89 | 0 |
| 2026-07-26 | 97 | 97 | 0 |
| 2026-07-27 | 184 | 184 | 0 |
| 2026-07-28 | 152 | 152 | 0 |
| 2026-07-29 | 91 | 91 | 0 |
| 2026-07-30 | 83 | 83 | 0 |
| 2026-07-31 | 119 | 119 | 0 |
| 2026-08-01 | 122 | 122 | 0 |
| 2026-08-02 | 194 | 194 | 0 |
| **Total** | **1,779** | **1,779** | **0** |

Bucket distribution is `omit=1,077`, `one_sentence=475`, `paragraph=227`,
`full=0`, `unknown=0` (1,779 total).

## Hostname privacy

The preflight found internal FQDNs in existing ignored artifacts, including
`devbig014.atn7.facebook.com`, `devbig030.atn3.facebook.com`,
`devbig030.facebook.com`, `devvm16873.pnb0.facebook.com`, and
`git.vip.facebook.com`.

Parent commits `00d594a66f0567b705d1cb561fad29a05cabb89d`,
`4484d2f32d3c359f9832b961e0048822f80dbd7b`, and
`154687d55b6120b87af4a275d1ce12d11f3ea3e4` make the generator scrub internal
machine FQDNs before JSON writes and
again at render time for legacy caches. Known machine names become short names;
public single-label sites such as `developers.facebook.com` remain intact.
Mutation tests pass 2/2. The final daily/weekly Markdown and JSON corpus contains
zero internal-FQDN matches, while the public developers.facebook.com link is
preserved.

## Generation integrity and cost

The first Aug 1 attempt inherited an incompatible ORC sandbox flag. Every model
batch failed and the generator fell back to heuristic one-sentence summaries.
That output remained isolated and was not copied into the canonical corpus.

After removing the inherited flag, a minimal model probe succeeded and Aug 1
was force-regenerated. The accepted Aug 1 run classified all 119 nonempty AI
responses plus 3 empty responses and measured:

- wall: **3m24.32s**;
- CPU: **499.37s** (273.41s user + 225.96s system);
- maximum RSS: **603,840 KiB**.

The first 3-turn Jul 31 update returned malformed model JSON and was likewise
discarded. Retrying those three turns as single-item batches succeeded 3/3 and
measured 1m16.20s wall, 133.30s CPU, and 609,616 KiB maximum RSS.

Generated transcript data remains machine-local under `dev_transcripts/daily/`;
this report and the sanitizer implementation are the durable main-reachable
evidence.
