# Companion Memory

[中文](README.md) | English

[![test](https://github.com/Nircay/companion-memory/actions/workflows/test.yml/badge.svg)](https://github.com/Nircay/companion-memory/actions/workflows/test.yml)

A single-file, zero-dependency memory store for companion AI. Gives your character a hippocampus: it remembers people, events, and what it said itself.

```bash
python3 memory.py init
python3 memory.py add event --text "Took her to see the sunset by the sea" --importance 0.9
python3 memory.py recall "Does she remember the sea?" --budget 600
```

The CLI speaks Chinese; recall output shown as-is.

```
关于这些，你记得：
- [diary · 2026-10-27] Took her to the sea, the wind was strong, she said she wasn't scared
```

## Why

Companion AI context blows up for two reasons: chat logs grow without bound, and old dialogue gets re-fed every turn just to avoid forgetting.

The usual answer — dump everything into a vector store — misses two concrete problems:

1. **Chinese retrieval.** SQLite's built-in `unicode61` tokenizer is nearly unusable for Chinese, and the `trigram` tokenizer fails on two-character words (`"海边"` does not match `"去海边"`).
2. **Misremembering is worse than forgetting.** A loose recall injects unrelated memories, and the model states something that never happened with full confidence.

So the emphasis here is on **write-time judgement** and **recall precision**, not storage.

## Features

- **Zero dependencies.** Python standard library only (`sqlite3`, `argparse`, `json`, `re`). No `pip install`; runs on macOS, Windows, and Linux as-is.
- **One file.** All memory lives in a single `.db`. Backup is a file copy.
- **12 memory domains.** People, person facts, interactions, groups, events, journals, utterances, promises, media index, watch history, DMs, self actions.
- **Chinese-aware search.** Self-built bigram + unigram inverted index, with separate handling for term queries and natural-sentence queries to avoid single-character false positives.
- **Journals dual-written.** SQLite plus `journals/YYYY-MM-DD.md`. If the program disappears, the diary still opens in any text editor.
- **Self-check.** `verify` reports integrity, index consistency, and orphan index rows.
- **Portable.** Copy `memory.py` and the `.db` to another machine; `verify` passing means it survived.
- **Direct date/ID lookup.** `search "2026-10-27"` hits that day's journal; `search "BV1xx..."` hits the video, bypassing tokenization.

## Install

```bash
git clone https://github.com/Nircay/companion-memory.git
cd companion-memory
python3 memory.py init
```

Python 3.9+ (3.10+ recommended).

## Usage

```bash
# People
python3 memory.py add person --json '{"platform_id":"10001","nickname":"Wang","tags":["techie"],"note":"answers with rhetorical questions"}'
python3 memory.py add fact --person 1 --key "taste" --value "cannot eat spicy" --confidence 0.9

# Events and interactions
python3 memory.py add event --text "Took her to see the sunset by the sea" --emotion "happy" --importance 0.9
python3 memory.py add interaction --person 1 --text "He fixed my script" --kind help

# What she said (prevents repetition, keeps persona consistent)
python3 memory.py add utterance --text "No way!" --channel "group:123456" --trigger "topic hit" --outcome 0.8

# Promises
python3 memory.py add promise --text "Show her the real ocean next time"

# Media index (descriptions only, never binaries)
python3 memory.py add media --path ./pics/sunset.jpg --kind image --caption "sunset over the sea"

# Journal (also writes journals/2026-10-27.md)
python3 memory.py journal --date 2026-10-27 --title "First time seeing the sea" --mood "happy" --body "..."

# Retrieval
python3 memory.py search "sunset"
python3 memory.py recall "Does she remember the sea?" --budget 600   # context-ready snippet

# Maintenance
python3 memory.py stats
python3 memory.py verify
python3 memory.py reindex
python3 memory.py forget-person 1
python3 memory.py export ./export
```

Full walkthrough in [examples.md](examples.md) (Chinese), schema in [schema.sql](schema.sql).

## The 12 domains

| Domain | Tables | Holds |
|---|---|---|
| People | `people` / `people_facts` / `interactions` | who is who, what she knows about them, what happened |
| Groups | `groups` | group profile, atmosphere, rules |
| Self | `diary` / `journals` / `utterances` / `promises` | experiences, diaries, own words, promises |
| Media | `media` | index and description of files/images (no binaries) |
| Content platforms | `watch_history` / `dm` | watched content, direct messages |
| Behaviour | `self_actions` | why she spoke up, what she said, how it landed |

Two columns deserve a note:

- `utterances.trigger` records *why* she spoke; `outcome` records *how it landed* (someone replied = positive, silence = negative). A month of this data tells you when she should interject.
- `people_facts.confidence` keeps a joke ("I'm from Shandong") as banter rather than established fact.

## How retrieval works

**Indexing.** Chinese is split into bigrams (precision) and unigrams (recall). Alphanumeric runs are kept whole and also split into 2-grams, so `"1660"` matches `"1660Ti"`.

**Two query modes:**

| Query shape | Detection | Rule |
|---|---|---|
| Term (`"spicy"`, `"quantum physics"`) | no function words | must hit a bigram, otherwise dropped |
| Natural sentence (`"does she still remember the sea"`) | contains function words | strip them, rank by content-token coverage |

This rule is necessary: with naive unigram fallback, `"quantum physics"` matched a person whose note contained the character for "child". With the rule:

```
"海边"            → hits the diary entry about the sea
"她还记得海吗"     → hits the promise, journal, and event
"量子物理"         → no hits
"火星殖民计划"     → no hits
```

**Side channel.** An absolute date in the query (`2026-10-27`, `2026年10月27日`) queries `journals.date` directly, bypassing FTS. `watch_history` indexes `bvid` alongside text, so video IDs are searchable.

**Scoring:**

```
score = 0.35 × bm25 + 0.35 × coverage + 0.18 × importance + 0.12 × recency(90d)
```

## Soft length limits

| Content | Limit |
|---|---|
| event / interaction / dm | 800 chars |
| journal body | 4000 chars |
| utterance / self action | 500 chars |
| fact | 100 chars |
| media caption | 200 chars |

Exceeding truncates with a warning (`--no-truncate` keeps it). The hard limit is SQLite's 1 GB, but the real constraint is retrieval quality.

## Portability

```bash
tar czf memory-backup.tgz memory.py whale_memory.db journals/
python3 memory.py verify     # on the other machine
```

- `journal_mode = DELETE`, so a single `.db` is complete — no dangling `-wal`
- timestamps are integer milliseconds, timezone-safe
- everything is UTF-8

## Design trade-offs

| Choice | Reason |
|---|---|
| SQLite over a vector DB | Under ~100k rows, FTS5 + hybrid scoring is enough; one less service |
| Files over DB blobs | Group files, images, long transcripts store `path + description`; the DB stays searchable text |
| Journals dual-written | Format longevity beats storage efficiency |
| No HTTP server | It is a library, not a platform |
| No encryption/auth | Local private data; do not expose it publicly |

## Known limitations

- No vector search (needs an embedding model and extensions; costly to keep cross-platform). Add it past ~10k memories.
- Rule-based tokenization; novel words and proper nouns rely on bigram fallback and can be missed in edge cases.
- Recency decay is a flat 90 days, not per memory type (promises should decay slower).
- Date lookup only understands absolute dates; relative phrasing ("this time last year") must be converted by the caller.

## Related

For a pure design spec (no implementation) of hot/cold memory tiering with cue-gated recall, see [Companion-AI-Memory-Context-Token-Optimization](https://github.com/ARCHEON999/Companion-AI-Memory-Context-Token-Optimization). Two ideas worth borrowing: only user-initiated mentions raise heat, and cold recall should not apply recency decay.

## License

MIT — see [LICENSE](LICENSE).
