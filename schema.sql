-- Cleartext DDL, mirror of SCHEMA in memory.py

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
