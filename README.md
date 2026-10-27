# Companion Memory

中文 | [English](README.en.md)

[![test](https://github.com/Nircay/companion-memory/actions/workflows/test.yml/badge.svg)](https://github.com/Nircay/companion-memory/actions/workflows/test.yml)

单文件、零依赖、跨平台的角色记忆库。给陪伴型 AI 一个"海马体"：记得人、记得事、记得自己说过什么。

```bash
python3 memory.py init
python3 memory.py add event --text "今天带她去海边看了日落" --importance 0.9
python3 memory.py recall "她还记得海吗" --budget 600
```

```
关于这些，你记得：
- [diary · 2026-10-27] 今天带她去看海了，风很大，她嘴上说不怕，其实腿在抖
```

## 为什么做这个

陪伴型 AI 的上下文会被两件事撑爆：聊天记录越滚越长，以及为了"不忘记"而反复回灌旧对话。

常见的解法是"把所有对话丢进向量库"，但那解决不了两个具体问题：

1. **中文检索**：SQLite 自带的 `unicode61` 分词器对中文几乎不可用；换成 `trigram` 后，两字词（"海边"）又匹配不到（实测 `"海边"` 查不到 `"去海边"`）。
2. **记错比记不住更糟**：检索召回一堆无关内容，模型会用笃定的语气说一件没发生的事。这比"我不记得了"更伤体验。

所以这个库的重点不在"存"，在**写入时的判断**和**召回时的精度**。

## 特性

- **零依赖**：只用 Python 标准库（`sqlite3` / `argparse` / `json` / `re`）。不需要 `pip install`，macOS / Windows / Linux 直接跑。
- **单文件**：所有记忆在一个 `.db` 里，备份 = 复制文件。
- **12 个记忆域**：人物、人物事实、互动、群、事件、日记、发言、承诺、媒体、观看记录、私信、行为日志。
- **中文检索**：自建 bigram + unigram 倒排，术语查询与自然句查询走两套判定，避免单字撞车造成的假阳性。
- **日记双写**：数据库 + `journals/YYYY-MM-DD.md`。程序没了，日记还能用记事本打开。
- **可自检**：`verify` 一条命令检查完整性、索引一致性、孤儿索引。
- **可迁移**：拷 `memory.py` + `.db` 到另一台机器，`verify` 通过即完好。
- **日期与 ID 直查**：`search "2026-10-27"` 直接命中那天日记，`search "BV1xx..."` 直接命中视频，不依赖分词。

## 安装

```bash
git clone https://github.com/Nircay/companion-memory.git
cd companion-memory
python3 memory.py init
```

需要 Python 3.9+（用了 `from __future__ import annotations` 与内建泛型注解，3.10+ 更稳）。

## 用法

```bash
# 人物
python3 memory.py add person --json '{"platform_id":"10001","nickname":"小王","tags":["技术宅"],"note":"爱用反问"}'
python3 memory.py add fact --person 1 --key "口味" --value "怕辣" --confidence 0.9

# 事件与互动
python3 memory.py add event --text "今天带她去海边看了日落" --emotion "开心" --importance 0.9
python3 memory.py add interaction --person 1 --text "他帮我修好了脚本" --kind help

# 她说过的话（防复读、保持一致性）
python3 memory.py add utterance --text "才不是呢！" --channel "group:123456" --trigger "话题命中" --outcome 0.8

# 承诺
python3 memory.py add promise --text "下次带她去看真正的大海"

# 媒体索引（只存描述，不存二进制）
python3 memory.py add media --path ./pics/sunset.jpg --kind image --caption "海边的日落"

# 日记（同时写 journals/2026-10-27.md）
python3 memory.py journal --date 2026-10-27 --title "第一次看海" --mood "开心" --body "..."

# 检索
python3 memory.py search "海边"
python3 memory.py recall "她还记得海吗" --budget 600   # 直接可注入上下文的片段

# 维护
python3 memory.py stats
python3 memory.py verify
python3 memory.py reindex
python3 memory.py forget-person 1
python3 memory.py export ./export
```

完整示例见 [examples.md](examples.md)，表结构见 [schema.sql](schema.sql)。

## 12 个记忆域

| 域 | 表 | 装什么 |
|---|---|---|
| 人物 | `people` / `people_facts` / `interactions` | 谁是谁、她知道他什么、发生过什么 |
| 群 | `groups` | 群档案、氛围、群规 |
| 自我 | `diary` / `journals` / `utterances` / `promises` | 经历、日记、说过的话、承诺 |
| 媒体 | `media` | 文件/图片/表情的索引与描述 |
| 内容平台 | `watch_history` / `dm` | 看过的内容、私信 |
| 行为 | `self_actions` | 插话的触发、内容、结果 |

两列值得单独说：

- `utterances.trigger` 记录"为什么说"，`outcome` 记录"说完效果如何"（有人接话 = 正，冷场 = 负）。攒一个月就能算出"她在哪个群、什么话题下该插嘴"。
- `people_facts.confidence` 让"群友说自己是山东人"这种玩梗只能当谈资，不能当已知。

## 检索怎么工作

**索引**：中文切 bigram（精度）+ unigram（召回），英文数字按整词并额外切 2-gram（让 `"1660"` 命中 `"1660Ti"`）。

**查询分两种模式**：

| 查询形态 | 判定 | 规则 |
|---|---|---|
| 术语（`"怕辣"`、`"量子物理"`） | 不含虚词 | 必须命中 bigram，否则丢弃 |
| 自然句（`"她还记得海吗"`） | 含"她/还/吗"等虚词 | 剥离虚词后按实义 token 覆盖率召回 |

这条规则是必要的：早期版本里，单字兜底会让 `"量子物理"` 命中备注含"夜猫子"的人（`子` 撞车）。加判定后：

```
"海边"          → 命中"今天带她去海边看了日落"
"她还记得海吗"   → 命中承诺、日记、事件
"量子物理"       → 无命中
"火星殖民计划"   → 无命中
```

**旁路直查**：query 里出现绝对日期时，直接查 `journals.date`，绕过 FTS——日期 token（`2026`/`10`/`27`）会把覆盖率算糊。`watch_history` 则把 `bvid` 一并纳入索引，按 BV 号可直接搜到。

**打分**：

```
score = 0.35 × bm25 + 0.35 × 覆盖率 + 0.18 × 重要度 + 0.12 × 时间衰减(90 天)
```

## 字符软上限

| 内容 | 上限 |
|---|---|
| 事件 / 互动 / 私信 | 800 字 |
| 日记正文 | 4000 字 |
| 她的发言 / 行为 | 500 字 |
| 事实 | 100 字 |
| 媒体描述 | 200 字 |

超出截断并提示（`--no-truncate` 可保留）。硬上限是 SQLite 的 1GB，但真正的约束是检索质量——单条太长，向量会被稀释、关键词会分散。

## 跨平台迁移

```bash
tar czf memory-backup.tgz memory.py whale_memory.db journals/
# 另一台机器上
python3 memory.py verify
```

注意事项：

- 库用 `journal_mode = DELETE`，拷贝时只需一个 `.db`，不会有 `-wal` 残留
- 时间戳统一存整数毫秒，跨时区安全
- 文本全 UTF-8；Windows 下用 `python memory.py`，不要用 cmd 重定向中文

## 设计取舍

| 决定 | 原因 |
|---|---|
| SQLite 而非向量库 | 十万条以内，FTS5 + 混合打分够用；省掉一个服务 |
| 文件而非 DB 二进制 | 群文件、图片、长字幕只存 `path + 描述`，库保持可检索的纯文本 |
| 日记双写 markdown | 格式寿命优先于存储效率，十年后仍能打开 |
| 无 HTTP 服务 | 这是库不是平台，调用方自己决定怎么用 |
| 无加密/鉴权 | 定位是本地私人数据，别暴露到公网 |

## 已知限制

- 没有向量检索（需要 embedding 模型与扩展，跨平台成本高）。记忆上万条后建议加。
- 分词是规则式的，新词/专名靠 bigram 兜底，极端情况会漏。
- 时间衰减固定 90 天，未按记忆类型区分（承诺类本应更长）。
- 日期直查只认绝对日期（`2026-10-27`、`2026年10月27日`），"去年今天"这类相对说法要调用方先换算。

## 相关

如果你在找"热/冷记忆分层 + 线索唤回"的设计规格（纯文档，不含实现），可以看 [Companion-AI-Memory-Context-Token-Optimization](https://github.com/ARCHEON999/Companion-AI-Memory-Context-Token-Optimization)。其中两条规则值得借鉴：热度只统计用户的有效提及（助手复述不加热），以及冷库召回不叠加时间衰减。

## License

MIT，见 [LICENSE](LICENSE)。
