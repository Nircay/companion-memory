#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
whale-memory — 单文件、零依赖、跨平台的角色记忆库。

设计原则
  1. 纯 Python 标准库（sqlite3 / argparse / json / hashlib），不依赖任何第三方包，
     macOS / Windows / Linux 之间可直接搬运。
  2. 数据库只是一个文件（默认 whale_memory.db），拷走即迁移。
  3. 中文检索用 bigram 倒排（SQLite 的 trigram 分词器对两字词失效，见 README）。
  4. 单条记忆有软上限，超长会截断并提示（硬上限是 SQLite 的 1GB，用不到）。
  5. 日记（journals）双写：数据库 + journals/YYYY-MM-DD.md 纯文本，格式永不过期。

用法
  python memory.py init
  python memory.py add person --json '{"platform_id":"10001","nickname":"小王","tags":["技术宅"]}'
  python memory.py add event --text "今天带她去海边看了日落" --importance 0.8
  python memory.py add fact --person 1 --key "口味" --value "怕辣"
  python memory.py journal --date 2026-10-27 --title "第一次看海" --body "..."
  python memory.py search "海边"
  python memory.py recall "她还记得海吗" --budget 600
  python memory.py stats
  python memory.py export ./export
  python memory.py forget-person 1

字符软上限（超出会截断，可用 --no-truncate 关闭）
  event 800 / journal 4000 / utterance 500 / fact 100 / caption 200
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

DEFAULT_DB = "whale_memory.db"
JOURNAL_DIR = "journals"
SCHEMA_VERSION = 1

# ── 软上限 ────────────────────────────────────────────────────────────────
LIMITS = {
    "diary": 800,
    "interactions": 800,
    "journals": 4000,
    "utterances": 500,
    "self_actions": 500,
    "dm": 800,
    "media.caption": 200,
    "people_facts": 100,
    "people.note": 300,
    "groups.vibe": 500,
    "watch_history.summary": 600,
}

SCHEMA = """
PRAGMA journal_mode = DELETE;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meta(
  key TEXT PRIMARY KEY,
  value TEXT
);

CREATE TABLE IF NOT EXISTS people(
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  platform    TEXT NOT NULL DEFAULT 'qq',
  platform_id TEXT NOT NULL,
  nickname    TEXT,
  aliases     TEXT,          -- JSON 数组
  tags        TEXT,          -- JSON 数组
  importance  REAL DEFAULT 0.5,
  first_seen  INTEGER,
  last_seen   INTEGER,
  note        TEXT,
  UNIQUE(platform, platform_id)
);

CREATE TABLE IF NOT EXISTS people_facts(
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  person_id  INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
  key        TEXT NOT NULL,
  value      TEXT NOT NULL,
  confidence REAL DEFAULT 0.8,
  source     TEXT,
  updated_at INTEGER,
  UNIQUE(person_id, key)
);

CREATE TABLE IF NOT EXISTS interactions(
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  person_id  INTEGER REFERENCES people(id) ON DELETE SET NULL,
  ts         INTEGER NOT NULL,
  kind       TEXT,
  summary    TEXT NOT NULL,
  sentiment  REAL,
  importance REAL DEFAULT 0.5,
  source     TEXT
);

CREATE TABLE IF NOT EXISTS groups(
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  platform   TEXT DEFAULT 'qq',
  group_id   TEXT NOT NULL UNIQUE,
  name       TEXT,
  vibe       TEXT,
  rules      TEXT,
  importance REAL DEFAULT 0.6
);

CREATE TABLE IF NOT EXISTS diary(
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  ts         INTEGER NOT NULL,
  kind       TEXT DEFAULT 'event',
  text       TEXT NOT NULL,
  emotion    TEXT,
  importance REAL DEFAULT 0.5,
  source     TEXT,
  person_id  INTEGER REFERENCES people(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS journals(
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  date       TEXT NOT NULL UNIQUE,
  title      TEXT,
  body       TEXT NOT NULL,
  mood       TEXT,
  word_count INTEGER,
  written_at INTEGER,
  revised_at INTEGER,
  file_path  TEXT
);

CREATE TABLE IF NOT EXISTS utterances(
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  ts       INTEGER NOT NULL,
  platform TEXT DEFAULT 'qq',
  channel  TEXT,
  trigger  TEXT,
  text     TEXT NOT NULL,
  outcome  REAL,
  hash     TEXT
);

CREATE TABLE IF NOT EXISTS promises(
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  ts        INTEGER NOT NULL,
  text      TEXT NOT NULL,
  due_ts    INTEGER,
  status    TEXT DEFAULT 'open',
  person_id INTEGER REFERENCES people(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS media(
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  sha256      TEXT,
  kind        TEXT,
  path        TEXT NOT NULL,
  caption     TEXT,
  uploaded_by INTEGER REFERENCES people(id) ON DELETE SET NULL,
  ts          INTEGER,
  tags        TEXT
);

CREATE TABLE IF NOT EXISTS watch_history(
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  bvid     TEXT,
  title    TEXT,
  up_name  TEXT,
  duration INTEGER,
  progress REAL,
  reaction TEXT,
  summary  TEXT,
  ts       INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS dm(
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  platform TEXT DEFAULT 'bilibili',
  peer     TEXT,
  direction TEXT,
  text     TEXT NOT NULL,
  ts       INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS self_actions(
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  ts       INTEGER NOT NULL,
  channel  TEXT,
  trigger  TEXT,
  text     TEXT,
  reactions TEXT,
  outcome  REAL
);

CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5(
  tokens,
  domain UNINDEXED,
  ref_id UNINDEXED,
  tokenize = 'unicode61'
);

CREATE INDEX IF NOT EXISTS idx_diary_ts        ON diary(ts);
CREATE INDEX IF NOT EXISTS idx_diary_imp       ON diary(importance);
CREATE INDEX IF NOT EXISTS idx_inter_person    ON interactions(person_id);
CREATE INDEX IF NOT EXISTS idx_inter_ts        ON interactions(ts);
CREATE INDEX IF NOT EXISTS idx_utt_ts          ON utterances(ts);
CREATE INDEX IF NOT EXISTS idx_facts_person    ON people_facts(person_id);
CREATE INDEX IF NOT EXISTS idx_watch_ts        ON watch_history(ts);
CREATE INDEX IF NOT EXISTS idx_dm_ts           ON dm(ts);
CREATE INDEX IF NOT EXISTS idx_actions_ts      ON self_actions(ts);
"""

# domain -> (table, 用于建索引的文本列, 时间列, 重要度列)
DOMAINS = {
    "people":        ("people",
                      "COALESCE(nickname,'') || ' ' || COALESCE(note,'') || ' ' "
                      "|| COALESCE(aliases,'') || ' ' || COALESCE(tags,'')",
                      "last_seen", "importance"),
    "people_facts":  ("people_facts",  "COALESCE(key,'') || ' ' || COALESCE(value,'')", "updated_at", "confidence"),
    "interactions":  ("interactions",  "summary",   "ts",         "importance"),
    "groups":        ("groups",        "COALESCE(name,'') || ' ' || COALESCE(vibe,'') || ' ' || COALESCE(rules,'')", None, "importance"),
    "diary":         ("diary",         "text",      "ts",         "importance"),
    "journals":      ("journals",      "COALESCE(title,'') || ' ' || COALESCE(body,'')", "written_at", None),
    "utterances":    ("utterances",    "text",      "ts",         None),
    "promises":      ("promises",      "text",      "ts",         None),
    "media":         ("media",         "COALESCE(caption,'') || ' ' || COALESCE(tags,'')", "ts", None),
    "watch_history": ("watch_history", "COALESCE(bvid,'') || ' ' || COALESCE(title,'') || ' ' || COALESCE(summary,'') || ' ' || COALESCE(reaction,'')", "ts", None),
    "dm":            ("dm",            "text",      "ts",         None),
    "self_actions":  ("self_actions",  "text",      "ts",         "outcome"),
}

CJK = re.compile(r"[\u4e00-\u9fff]+")
WORD = re.compile(r"[A-Za-z0-9_]+")


# ── 中文 bigram 分词 ──────────────────────────────────────────────────────
def tokenize(text: str) -> str:
    """把文本切成可检索的 token 串：中文 bigram + unigram，英文数字按整词。

    bigram 保证精度（"海边" 命中 "去海边"），unigram 保证召回
    （"海" 也能命中 "海边"）。检索时用覆盖率把精度补回来。
    """
    if not text:
        return ""
    out: list[str] = []
    for m in re.finditer(r"[\u4e00-\u9fff]+|[A-Za-z0-9_]+", text):
        seg = m.group(0)
        if CJK.fullmatch(seg):
            if len(seg) == 1:
                out.append(seg)
            else:
                out.extend(seg[i:i + 2] for i in range(len(seg) - 1))
                out.extend(seg)          # unigram 兜底
        else:
            s = seg.lower()
            out.append(s)                # 整词
            if len(s) >= 2:
                out.extend(s[i:i + 2] for i in range(len(s) - 1))  # 字母数字 bigram 兜底，让 "1660" 命中 "1660Ti"
    return " ".join(out)


DATE_RE = re.compile(r"(\d{4})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})")

STOP_CHARS = set(
    "的了吗呢吧啊呀嘛哦嗯是在有和就跟对把被让给还都也才又很太更最不没要会能可该"
    "她他它我你您得着过这那个些上下里中来去说想做起从到与及而以于之其此"
    "们您吧呗啦咯嘛么啥咋谁哪怎哪"
)



def clean_display(text: str) -> str:
    """去掉 JSON 残留（aliases / tags 的原文），让输出干净些。"""
    t = re.sub(r'\[\s*"[^\]]*"\s*\]', '', text or '')
    t = t.replace('[]', '')
    return re.sub(r"\s+", " ", t).strip()


def content_chars(text: str) -> set[str]:
    """自然句覆盖率的"实义 token"：非停用单字 + 字母数字整词（小写）。

    故意不算虚词 bigram（"她说 / 说过 / 的事"），否则自然句的覆盖率会被
    停用字组合稀释；英文数字整词必须算，否则 "她说过1660Ti的事吗" 会因为
    句中没有汉字实字而整句失忆。
    """
    s: set[str] = set()
    for ch in text:
        if CJK.fullmatch(ch) and ch not in STOP_CHARS:
            s.add(ch)
    for m in WORD.finditer(text):
        s.add(m.group(0).lower())
    return s


def date_hits(conn, query: str):
    """query 里带绝对日期（2026-10-27 / 2026年10月27日）时，直查那天的日记。

    FTS 索引不含日期（日期 token 会污染覆盖率），所以日期查询走这条旁路。
    """
    m = DATE_RE.search(query or "")
    if not m:
        return []
    y, mo, d = m.groups()
    iso = f"{y}-{int(mo):02d}-{int(d):02d}"
    out = []
    for row in conn.execute(
            "SELECT id, body, written_at FROM journals WHERE date = ?", (iso,)):
        out.append({"domain": "journals", "id": row["id"], "text": row["body"] or "",
                    "ts": row["written_at"], "importance": 0.7,
                    "bm25": None, "coverage": 1.0})
    return out


def now_ms() -> int:
    return int(time.time() * 1000)


def clip(value, limit_key: str, no_truncate: bool) -> str:
    if value is None:
        return ""
    s = str(value)
    limit = LIMITS.get(limit_key)
    if limit and len(s) > limit:
        if no_truncate:
            print(f"  ! {limit_key} 长度 {len(s)} 超过软上限 {limit}（按 --no-truncate 保留）",
                  file=sys.stderr)
        else:
            print(f"  ! {limit_key} 长度 {len(s)} 超过软上限 {limit}，已截断", file=sys.stderr)
            s = s[:limit]
    return s


def connect(db_path: str) -> sqlite3.Connection:
    path = Path(db_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ── 索引写入 ──────────────────────────────────────────────────────────────
def index_row(conn: sqlite3.Connection, domain: str, ref_id: int, text: str) -> None:
    conn.execute("DELETE FROM search_index WHERE domain = ? AND ref_id = ?", (domain, ref_id))
    tokens = tokenize(text)
    if tokens:
        conn.execute(
            "INSERT INTO search_index(tokens, domain, ref_id) VALUES (?, ?, ?)",
            (tokens, domain, ref_id),
        )


# ── 写入 ──────────────────────────────────────────────────────────────────
def index_from_row(conn: sqlite3.Connection, domain: str, row_id: int) -> None:
    """按 DOMAINS 里的表达式重建一行索引。

    写入与 reindex 必须走同一条路径：早先写入侧手写拼接、reindex 侧用表达式，
    结果 watch_history 的写入漏了 bvid，`search "BV..."` 只在 reindex 后才命中。
    """
    table, text_expr, _, _ = DOMAINS[domain]
    row = conn.execute(f"SELECT {text_expr} AS t FROM {table} WHERE id = ?", (row_id,)).fetchone()
    index_row(conn, domain, row_id, (row["t"] if row else "") or "")


def add_row(conn, domain, values, text_for_index=None, no_truncate=False):
    table = DOMAINS[domain][0]
    cols = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    cur = conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(values.values()))
    row_id = cur.lastrowid
    index_from_row(conn, domain, row_id)
    conn.commit()
    return row_id


def cmd_init(conn, args):
    conn.executescript(SCHEMA)
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)",
                 (str(SCHEMA_VERSION),))
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('created_at', ?)",
                 (str(now_ms()),))
    conn.commit()
    print(f"已初始化：{Path(args.db).absolute()}")


def cmd_add(conn, args):
    try:
        return _cmd_add(conn, args)
    except sqlite3.IntegrityError as e:
        conn.rollback()
        msg = str(e)
        if "FOREIGN KEY" in msg:
            print(f"写入失败：关联的 person_id 不存在（{msg}）\n先 `add person` 拿到 id 再写入。", file=sys.stderr)
        else:
            print(f"写入失败：{msg}", file=sys.stderr)
        raise SystemExit(1)


REQUIRED = {
    "person": ["platform_id"], "fact": ["person", "key", "value"],
    "event": ["text"], "interaction": ["text"], "group": ["group_id"],
    "utterance": ["text"], "promise": ["text"], "media": ["path"],
    "watch": ["title"], "dm": ["text"], "action": ["text"],
}


def _cmd_add(conn, args):
    if getattr(args, "json", None):
        blob = json.loads(args.json)
        for k, v in blob.items():
            if getattr(args, k, None) in (None, [], ""):
                setattr(args, k, v)
    d = args.domain
    missing = [k for k in REQUIRED[d] if getattr(args, k, None) in (None, "")]
    if missing:
        flags = ", ".join("--" + k.replace("_", "-") for k in missing)
        print(f"缺少必填参数：{flags}", file=sys.stderr)
        raise SystemExit(2)
    ts = args.ts or now_ms()
    p = args.__dict__

    if d == "person":
        vals = {
            "platform": p.get("platform") or "qq",
            "platform_id": p["platform_id"],
            "nickname": p.get("nickname") or "",
            "aliases": json.dumps(p.get("aliases") or [], ensure_ascii=False),
            "tags": json.dumps(p.get("tags") or [], ensure_ascii=False),
            "importance": p.get("importance") or 0.5,
            "first_seen": ts, "last_seen": ts,
            "note": clip(p.get("note"), "people.note", args.no_truncate),
        }
        cur = conn.execute(
            """INSERT INTO people(platform, platform_id, nickname, aliases, tags,
                                  importance, first_seen, last_seen, note)
               VALUES (:platform, :platform_id, :nickname, :aliases, :tags,
                       :importance, :first_seen, :last_seen, :note)
               ON CONFLICT(platform, platform_id) DO UPDATE SET
                 nickname = excluded.nickname,
                 aliases = CASE WHEN excluded.aliases NOT IN ('[]','') THEN excluded.aliases ELSE people.aliases END,
                 tags    = CASE WHEN excluded.tags    NOT IN ('[]','') THEN excluded.tags    ELSE people.tags    END,
                 last_seen = excluded.last_seen,
                 note = CASE WHEN excluded.note <> '' THEN excluded.note ELSE people.note END""",
            vals)
        row = conn.execute("SELECT id FROM people WHERE platform=? AND platform_id=?",
                           (vals["platform"], vals["platform_id"])).fetchone()
        index_from_row(conn, "people", row["id"])
        conn.commit()
        print(f"person #{row['id']} {vals['nickname'] or vals['platform_id']}")
        return

    if d == "fact":
        vals = {
            "person_id": args.person, "key": args.key,
            "value": clip(args.value, "people_facts", args.no_truncate),
            "confidence": args.confidence if args.confidence is not None else 0.8,
            "source": args.source or "", "updated_at": ts,
        }
        conn.execute(
            """INSERT INTO people_facts(person_id, key, value, confidence, source, updated_at)
               VALUES (:person_id, :key, :value, :confidence, :source, :updated_at)
               ON CONFLICT(person_id, key) DO UPDATE SET
                 value = excluded.value, confidence = excluded.confidence,
                 updated_at = excluded.updated_at""", vals)
        row = conn.execute("SELECT id FROM people_facts WHERE person_id=? AND key=?",
                           (args.person, args.key)).fetchone()
        index_from_row(conn, "people_facts", row["id"])
        conn.commit()
        print(f"fact #{row['id']} {args.key} = {args.value}")
        return

    if d == "event":
        vals = {"ts": ts, "kind": args.kind or "event",
                "text": clip(args.text, "diary", args.no_truncate),
                "emotion": args.emotion or "", "importance": args.importance or 0.5,
                "source": args.source or "", "person_id": args.person}
        rid = add_row(conn, "diary", vals, vals["text"], args.no_truncate)
        print(f"event #{rid}")
        return

    if d == "interaction":
        vals = {"person_id": args.person, "ts": ts, "kind": args.kind or "chat",
                "summary": clip(args.text, "interactions", args.no_truncate),
                "sentiment": args.sentiment, "importance": args.importance or 0.5,
                "source": args.source or ""}
        rid = add_row(conn, "interactions", vals, vals["summary"], args.no_truncate)
        print(f"interaction #{rid}")
        return

    if d == "group":
        conn.execute(
            """INSERT INTO groups(platform, group_id, name, vibe, rules, importance)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(group_id) DO UPDATE SET
                 name=excluded.name, vibe=excluded.vibe, rules=excluded.rules""",
            (args.platform or "qq", args.group_id, args.name or "",
             clip(args.vibe, "groups.vibe", args.no_truncate), args.rules or "",
             args.importance or 0.6))
        row = conn.execute("SELECT id FROM groups WHERE group_id=?", (args.group_id,)).fetchone()
        index_from_row(conn, "groups", row["id"])
        conn.commit()
        print(f"group #{row['id']} {args.name or args.group_id}")
        return

    if d == "utterance":
        text = clip(args.text, "utterances", args.no_truncate)
        vals = {"ts": ts, "platform": args.platform or "qq", "channel": args.channel or "",
                "trigger": args.trigger or "", "text": text,
                "outcome": args.outcome,
                "hash": hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]}
        rid = add_row(conn, "utterances", vals, text, args.no_truncate)
        print(f"utterance #{rid}")
        return

    if d == "promise":
        vals = {"ts": ts, "text": args.text, "due_ts": args.due_ts,
                "status": "open", "person_id": args.person}
        rid = add_row(conn, "promises", vals, args.text)
        print(f"promise #{rid}")
        return

    if d == "media":
        vals = {"sha256": args.sha256, "kind": args.kind or "image", "path": args.path,
                "caption": clip(args.caption, "media.caption", args.no_truncate),
                "uploaded_by": args.person, "ts": ts,
                "tags": json.dumps(args.tags or [], ensure_ascii=False)}
        rid = add_row(conn, "media", vals, f"{args.caption or ''} {' '.join(args.tags or [])}")
        print(f"media #{rid}")
        return

    if d == "watch":
        vals = {"bvid": args.bvid, "title": args.title, "up_name": args.up,
                "duration": args.duration, "progress": args.progress,
                "reaction": args.reaction,
                "summary": clip(args.summary, "watch_history.summary", args.no_truncate),
                "ts": ts}
        rid = add_row(conn, "watch_history", vals,
                      f"{args.title or ''} {args.summary or ''} {args.reaction or ''}")
        print(f"watch #{rid}")
        return

    if d == "dm":
        vals = {"platform": args.platform or "bilibili", "peer": args.peer or "",
                "direction": args.direction or "in",
                "text": clip(args.text, "dm", args.no_truncate), "ts": ts}
        rid = add_row(conn, "dm", vals, vals["text"])
        print(f"dm #{rid}")
        return

    if d == "action":
        vals = {"ts": ts, "channel": args.channel or "", "trigger": args.trigger or "",
                "text": clip(args.text, "self_actions", args.no_truncate),
                "reactions": args.reactions or "", "outcome": args.outcome}
        rid = add_row(conn, "self_actions", vals, vals["text"])
        print(f"action #{rid}")
        return

    raise SystemExit(f"未知类型：{d}")


def cmd_journal(conn, args):
    body = clip(args.body, "journals", args.no_truncate)
    ts = now_ms()
    conn.execute(
        """INSERT INTO journals(date, title, body, mood, word_count, written_at, file_path)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(date) DO UPDATE SET
             title=excluded.title, body=excluded.body, mood=excluded.mood,
             word_count=excluded.word_count, revised_at=excluded.written_at""",
        (args.date, args.title or "", body, args.mood or "", len(body), ts, ""))
    row = conn.execute("SELECT id FROM journals WHERE date=?", (args.date,)).fetchone()
    jdir = Path(args.db).expanduser().parent / JOURNAL_DIR
    jdir.mkdir(parents=True, exist_ok=True)
    fpath = jdir / f"{args.date}.md"
    header = f"# {args.title or args.date}\n\n> {args.date}" + (f" · {args.mood}" if args.mood else "")
    fpath.write_text(header + "\n\n" + body + "\n", encoding="utf-8")
    conn.execute("UPDATE journals SET file_path=? WHERE id=?", (str(fpath), row["id"]))
    index_from_row(conn, "journals", row["id"])
    conn.commit()
    print(f"journal {args.date} ({len(body)} 字) -> {fpath}")


# ── 检索 ──────────────────────────────────────────────────────────────────
def search(conn, query: str, limit: int = 8):
    q_tokens = [t for t in tokenize(query).split() if t]
    if not q_tokens:
        return []
    q_bigrams = {t for t in q_tokens if len(t) >= 2}
    q_uni = {t for t in q_tokens if len(t) == 1 and t not in STOP_CHARS}
    # 查询像自然句（含停用字）时允许单字兜底；像术语时要求 bigram 命中
    loose = any(ch in STOP_CHARS for ch in query)
    denom = len(q_bigrams) * 1.0 + len(q_uni) * 0.35
    expr = " OR ".join(f'"{t}"' for t in dict.fromkeys(q_tokens))
    rows = conn.execute(
        """SELECT domain, ref_id, bm25(search_index) AS rank
             FROM search_index
            WHERE search_index MATCH ?
            ORDER BY rank
            LIMIT 200""", (expr,)).fetchall()

    raw_scores = []
    for r in rows:
        spec = DOMAINS.get(r["domain"])
        if not spec:
            continue
        table, text_expr, ts_col, imp_col = spec
        cols = ["id", f"{text_expr} AS t"] + ([ts_col] if ts_col else []) + ([imp_col] if imp_col else [])
        sel = ", ".join(dict.fromkeys(cols))
        item = conn.execute(f"SELECT {sel} FROM {table} WHERE id=?", (r["ref_id"],)).fetchone()
        if not item:
            continue
        text = item["t"] or ""
        ts = item[ts_col] if ts_col else None
        imp = item[imp_col] if imp_col else None

        if loose:
            # 自然句：按"实义 token"算覆盖率（非停用单字 + 字母数字整词），
            # 不把"她说 / 说过"这类虚词 bigram 计入。
            q_real = content_chars(query)
            d_real = content_chars(text)
            hits = len(q_real & d_real)
            if not q_real or hits == 0:
                continue
            wcov = hits / len(q_real)
            if wcov < 0.30:
                continue
        else:
            d_tokens = set(tokenize(text).split())
            big_hits = len(q_bigrams & d_tokens)
            if q_bigrams and big_hits == 0:
                continue                  # 术语查询只靠单字命中 → 丢弃
            uni_hits = len(q_uni & d_tokens)
            wcov = ((big_hits + 0.35 * uni_hits) / denom) if denom else 0.0
            if wcov < 0.12:
                continue

        raw_scores.append({
            "domain": r["domain"], "id": r["ref_id"], "text": text,
            "ts": ts, "importance": float(imp) if imp is not None else 0.5,
            "bm25": -(r["rank"] or 0.0), "coverage": wcov,
        })

    for h in date_hits(conn, query):
        if any(x["domain"] == h["domain"] and x["id"] == h["id"] for x in raw_scores):
            continue
        raw_scores.append(h)

    if not raw_scores:
        return []
    mx = max((x["bm25"] for x in raw_scores if x["bm25"] is not None), default=1.0) or 1.0
    for x in raw_scores:
        if x["bm25"] is None:
            x["bm25"] = mx
    now = now_ms()
    for s in raw_scores:
        norm_fts = s["bm25"] / mx
        age_days = ((now - s["ts"]) / 86400000.0) if s["ts"] else 365.0
        recency = math.exp(-max(age_days, 0.0) / 90.0)
        s["score"] = (0.35 * norm_fts + 0.35 * s["coverage"]
                      + 0.18 * s["importance"] + 0.12 * recency)
    raw_scores.sort(key=lambda x: x["score"], reverse=True)
    return raw_scores[:limit]


def cmd_search(conn, args):
    hits = search(conn, args.query, args.limit)
    if not hits:
        print("（无命中）")
        return
    for i, h in enumerate(hits, 1):
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(h["ts"] / 1000)) if h["ts"] else "—"
        print(f"[{i}] {h['score']:.3f}  {h['domain']}#{h['id']}  {when}")
        print(f"    {clean_display(h['text'])[:160]}")


def cmd_recall(conn, args):
    hits = search(conn, args.query, args.limit)
    lines, used = [], 0
    for h in hits:
        when = time.strftime("%Y-%m-%d", time.localtime(h["ts"] / 1000)) if h["ts"] else "—"
        line = f"- [{h['domain']} · {when}] {clean_display(h['text'])[:200]}"
        if used + len(line) > args.budget:
            break
        lines.append(line)
        used += len(line)
    if not lines:
        print("（没有相关记忆）")
        return
    print("关于这些，你记得：")
    print("\n".join(lines))


# ── 统计 / 导出 / 遗忘 ────────────────────────────────────────────────────
def cmd_stats(conn, args):
    size = Path(args.db).expanduser().stat().st_size
    print(f"数据库：{Path(args.db).absolute()}  ({size/1024:.1f} KB)")
    total = 0
    for domain, (table, _, _, _) in DOMAINS.items():
        n = conn.execute(f"SELECT count(*) c FROM {table}").fetchone()["c"]
        total += n
        if n:
            print(f"  {domain:14} {n:>6}")
    idx = conn.execute("SELECT count(*) c FROM search_index").fetchone()["c"]
    print(f"  {'索引条目':14} {idx:>6}")
    print(f"  {'合计':14} {total:>6}")


def cmd_export(conn, args):
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    dump = {}
    for domain, (table, _, _, _) in DOMAINS.items():
        rows = conn.execute(f"SELECT * FROM {table}").fetchall()
        dump[domain] = [dict(r) for r in rows]
    (out / "memory_dump.json").write_text(
        json.dumps(dump, ensure_ascii=False, indent=2), encoding="utf-8")
    jdir = out / JOURNAL_DIR
    jdir.mkdir(exist_ok=True)
    n = 0
    for r in conn.execute("SELECT date, title, body, mood FROM journals ORDER BY date"):
        p = jdir / f"{r['date']}.md"
        head = f"# {r['title'] or r['date']}\n\n> {r['date']}" + (f" · {r['mood']}" if r["mood"] else "")
        p.write_text(head + "\n\n" + r["body"] + "\n", encoding="utf-8")
        n += 1
    print(f"已导出到 {out}（JSON + {n} 篇日记）")


def cmd_reindex(conn, args):
    conn.execute("DELETE FROM search_index")
    total = 0
    for domain, (table, text_expr, _, _) in DOMAINS.items():
        for row in conn.execute(f"SELECT id, {text_expr} AS t FROM {table}"):
            index_row(conn, domain, row["id"], row["t"] or "")
            total += 1
    conn.commit()
    print(f"已重建索引：{total} 条")


def cmd_forget_person(conn, args):
    person = conn.execute("SELECT * FROM people WHERE id=?", (args.person_id,)).fetchone()
    if not person:
        print("没找到这个人")
        return
    facts = [r[0] for r in conn.execute("SELECT id FROM people_facts WHERE person_id=?", (args.person_id,))]
    inters = [r[0] for r in conn.execute("SELECT id FROM interactions WHERE person_id=?", (args.person_id,))]
    conn.execute("DELETE FROM people_facts WHERE person_id=?", (args.person_id,))
    conn.execute("DELETE FROM interactions WHERE person_id=?", (args.person_id,))
    conn.execute("UPDATE diary SET person_id=NULL WHERE person_id=?", (args.person_id,))
    conn.execute("UPDATE media SET uploaded_by=NULL WHERE uploaded_by=?", (args.person_id,))
    conn.execute("UPDATE promises SET person_id=NULL WHERE person_id=?", (args.person_id,))
    conn.execute("DELETE FROM people WHERE id=?", (args.person_id,))
    conn.execute("DELETE FROM search_index WHERE domain='people' AND ref_id=?", (args.person_id,))
    for rid in facts:
        conn.execute("DELETE FROM search_index WHERE domain='people_facts' AND ref_id=?", (rid,))
    for rid in inters:
        conn.execute("DELETE FROM search_index WHERE domain='interactions' AND ref_id=?", (rid,))
    conn.commit()
    print(f"已忘记 person #{args.person_id} {person['nickname'] or person['platform_id']} "
          f"（事实 {len(facts)} 条、互动 {len(inters)} 条）")


# ── 自检 ──────────────────────────────────────────────────────────────────
def cmd_verify(conn, args):
    ok = conn.execute("PRAGMA integrity_check").fetchone()[0]
    print(f"integrity_check : {ok}")
    bad = []
    for domain, (table, _, _, _) in DOMAINS.items():
        a = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        b = conn.execute("SELECT count(*) FROM search_index WHERE domain=?", (domain,)).fetchone()[0]
        if a != b:
            bad.append((domain, a, b))
    for domain, a, b in bad:
        print(f"  索引不一致 {domain}: 表={a} 索引={b}")
    conds = " OR ".join(
        f"(s.domain='{d}' AND s.ref_id NOT IN (SELECT id FROM {spec[0]}))"
        for d, spec in DOMAINS.items())
    orphans = conn.execute(f"SELECT count(*) FROM search_index s WHERE {conds}").fetchone()[0]
    print(f"孤儿索引        : {orphans}")
    if ok == "ok" and not bad and orphans == 0:
        print("结论            : OK")
    else:
        print("结论            : 需要修复，运行 `python memory.py reindex`")


# ── CLI ───────────────────────────────────────────────────────────────────
def build_parser():
    p = argparse.ArgumentParser(description="whale-memory：单文件角色记忆库")
    p.add_argument("--db", default=DEFAULT_DB, help="数据库文件路径")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="建表")

    a = sub.add_parser("add", help="写入一条记忆")
    a.add_argument("domain", choices=["person", "fact", "event", "interaction", "group",
                                      "utterance", "promise", "media", "watch", "dm", "action"])
    a.add_argument("--ts", type=int)
    a.add_argument("--no-truncate", action="store_true")
    a.add_argument("--importance", type=float)
    a.add_argument("--person", type=int, help="关联的 person id")
    a.add_argument("--source")
    a.add_argument("--platform")
    a.add_argument("--channel")
    a.add_argument("--trigger")
    a.add_argument("--outcome", type=float)
    a.add_argument("--text")
    a.add_argument("--kind")
    a.add_argument("--emotion")
    a.add_argument("--sentiment", type=float)
    a.add_argument("--confidence", type=float)
    a.add_argument("--key")
    a.add_argument("--value")
    a.add_argument("--platform-id", dest="platform_id")
    a.add_argument("--nickname")
    a.add_argument("--aliases", nargs="*")
    a.add_argument("--tags", nargs="*")
    a.add_argument("--note")
    a.add_argument("--group-id", dest="group_id")
    a.add_argument("--name")
    a.add_argument("--vibe")
    a.add_argument("--rules")
    a.add_argument("--due-ts", dest="due_ts", type=int)
    a.add_argument("--sha256")
    a.add_argument("--path")
    a.add_argument("--caption")
    a.add_argument("--bvid")
    a.add_argument("--title")
    a.add_argument("--up")
    a.add_argument("--duration", type=int)
    a.add_argument("--progress", type=float)
    a.add_argument("--reaction")
    a.add_argument("--summary")
    a.add_argument("--peer")
    a.add_argument("--direction")
    a.add_argument("--reactions")
    a.add_argument("--json", help="整包 JSON（person 用）")

    j = sub.add_parser("journal", help="写日记（同时落 markdown）")
    j.add_argument("--date", required=True)
    j.add_argument("--title")
    j.add_argument("--body", required=True)
    j.add_argument("--mood")
    j.add_argument("--no-truncate", action="store_true")

    s = sub.add_parser("search", help="检索")
    s.add_argument("query")
    s.add_argument("--limit", type=int, default=8)

    r = sub.add_parser("recall", help="生成可注入上下文")
    r.add_argument("query")
    r.add_argument("--limit", type=int, default=8)
    r.add_argument("--budget", type=int, default=600)

    sub.add_parser("stats", help="统计")

    sub.add_parser("reindex", help="重建检索索引")

    sub.add_parser("verify", help="自检：完整性 + 索引一致性")

    e = sub.add_parser("export", help="导出 JSON + 日记 markdown")
    e.add_argument("out")

    f = sub.add_parser("forget-person", help="忘记某人")
    f.add_argument("person_id", type=int)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.cmd == "init":
        conn = connect(args.db)
        cmd_init(conn, args)
        return
    conn = connect(args.db)
    conn.executescript(SCHEMA)  # 幂等，防止未 init
    {"add": cmd_add, "journal": cmd_journal, "search": cmd_search,
     "recall": cmd_recall, "stats": cmd_stats, "export": cmd_export,
     "reindex": cmd_reindex, "verify": cmd_verify,
     "forget-person": cmd_forget_person}[args.cmd](conn, args)
    conn.close()


if __name__ == "__main__":
    main()
