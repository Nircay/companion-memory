# 示例：从零灌一周的记忆

照抄即可。`--db` 可省略，默认当前目录 `whale_memory.db`。

```bash
python3 memory.py init
```

## 1. 群友

```bash
python3 memory.py add person --json '{"platform_id":"10001","nickname":"小王","tags":["技术宅","夜猫子"],"note":"群里的技术担当，说话爱用反问"}'
python3 memory.py add person --json '{"platform_id":"10002","nickname":"阿七","tags":["猫奴"],"note":"经常发猫照"}'

# 记事实：confidence 低于 0.5 的只当谈资，不当已知
python3 memory.py add fact --person 1 --key "口味" --value "怕辣，吃火锅只点清汤" --confidence 0.9
python3 memory.py add fact --person 1 --key "自称籍贯" --value "山东人" --confidence 0.3 --source "群聊玩笑，未证实"
```

## 2. 群

```bash
python3 memory.py add group --group-id 920000 --name "日常群" --vibe "沙雕，爱玩梗，凌晨最活跃" --rules "不讨论政治"
```

## 3. 她经历的事

```bash
python3 memory.py add event --text "今天他带我去海边看了日落，风很大，我假装不怕，其实腿有点抖" --emotion "开心又逞强" --importance 0.9
python3 memory.py add event --text "群里聊到显卡，小王说 1660Ti 是古董，我帮他说了句话" --importance 0.4 --person 1
python3 memory.py add interaction --person 1 --text "他帮我修好了卡住的脚本" --kind help --sentiment 0.8
```

## 4. 她说过的话（防复读 + 保持一致性）

```bash
python3 memory.py add utterance --text "才不是呢，1660Ti 还能再战三年！" --channel "group:920000" --trigger "话题命中" --outcome 0.8
```

`trigger` 记"为什么说"，`outcome` 记"说完效果如何"（有人接话 = 正，冷场 = 负）。这两列攒一个月，就能算出她在哪个群、什么话题下该插嘴。

## 5. 承诺

```bash
python3 memory.py add promise --text "下次带她去看真正的大海" --due-ts 1800000000000
```

## 6. B 站

```bash
python3 memory.py add watch --bvid BV1xx411c7mD --title "深海纪录片：马里亚纳海沟" --up "自然观察站" --duration 1800 --progress 0.6 --reaction "看到发光水母时屏住呼吸" --summary "讲深海生态，重点在生物发光与高压适应"
python3 memory.py add dm --peer "小王" --direction in --text "你昨天说的那个梗我笑了半天"
```

## 7. 媒体（只存索引，不存文件）

```bash
python3 memory.py add media --path ./media/sunset.jpg --kind image --caption "海边的日落，橘色一片"
```

## 8. 日记（同时写 journals/2026-10-27.md）

```bash
python3 memory.py journal --date 2026-10-27 --title "第一次看海" --mood "开心又有点怕" --body "今天他带我去看海了。风比视频里大得多，我嘴上说不怕，其实腿在抖。日落时整片海都是橘色的……"
```

## 9. 日常查询

```bash
python3 memory.py search "海边"
python3 memory.py search "小王"
python3 memory.py recall "她还记得海吗" --budget 600     # 给对话用的上下文片段
python3 memory.py stats
python3 memory.py verify                                 # 完整性 + 索引一致性
```

`recall` 的输出就是往 system prompt 里塞的那一段：

```
关于这些，你记得：
- [promises · 2026-10-27] 下次带她去看真正的大海
- [journals · 2026-10-27] 今天他带我去看海了…
```

## 10. 维护

```bash
python3 memory.py reindex          # 换了分词规则后重建
python3 memory.py forget-person 2  # 忘记某人（连带事实、互动、索引）
python3 memory.py export ./export  # JSON + 日记 markdown
```

## 每日定时任务（伪代码）

```
每晚 23:00
  ├─ 取今天的 utterances / diary / interactions / watch_history
  ├─ 让模型写日记 → memory.py journal --date $(date +%F) ...
  ├─ 让模型抽事件/事实 → memory.py add event / add fact
  └─ 每 30 天跑一次 reindex（可选）
```
