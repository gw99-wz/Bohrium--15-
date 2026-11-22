# 字段字典

`result.json` 的每个字段，类型、含义、以及**它为什么存在**。字段名与
`schemas/result.schema.json` 一一对应；校验器 `scripts/validate_result.py` 会独立复核。

## 信封

| 字段 | 类型 | 含义 |
|-|-|-|
| `case_id` | string | 输入 `case.json` 的标识回显。审阅者据此把结果对回输入。 |
| `contract.source` | string | `case.json:<路径>` 表示契约是从输入里读到的；`fallback` 表示输入没给，用了本 Skill 文档化的默认字段集。 |
| `contract.required` | string[] | 契约要求的字段路径（支持 `graph.nodes` 这种点号路径）。 |
| `contract.found` / `contract.missing` | string[] | 已满足 / 未满足。 |
| `contract.satisfied` | bool | `missing` 为空。**这一项为 false 会直接把状态压成 `partial`。** |

## 来源 `sources[]`

| 字段 | 类型 | 含义 |
|-|-|-|
| `source_id` | string | 单篇论文的稳定标识。**每条节点/边都必须指向恰好一篇论文。** |
| `role` | string | `primary` / `secondary`，对应输入修订里的第一篇/新增篇。 |
| `doi` `title` `authors` `licence` `origin` | — | 来源元数据，从 `corpus-manifest.json` 读；不猜。 |
| `sha256` | string | 来源文件的哈希，用于后续回溯与去重。 |
| `paragraph_count` | int | 实际参与构建的段落数。 |

> 输入修订明确要求：**根级 DOI/title/authors 属于第一篇，不得套到第二篇**。
> 本 Skill 因此把元数据放在 `sources[]` 逐篇记录，而不是放在根上。

## 图 `graph`

### `graph.nodes[]`

| 字段 | 类型 | 含义 |
|-|-|-|
| `id` | `node:<12 hex>` | 由 `(type, canonical, sources…)` 内容派生的稳定 id。**重跑同一输入得到同一个 id。** |
| `type` | enum | 受控词表：`researcher` `material` `method` `task` `metric` `conclusion`。 |
| `label` | string | 原文里出现过的表面形式（取该组里最长的那个）。 |
| `aliases` | string[] | 被合并进来的其他表面形式。 |
| `canonical` | string | 归一化后的键：大小写/标点/空白折叠、语料内定义过的缩写展开、去掉 `monolayer` 这类形态限定词。 |
| `sources` | string[] | 该节点触及的论文。长度 1 = 单篇；长度 2 = 跨篇概念节点。 |
| `scope` | enum | `single_paper` / `cross_paper_concept`。 |
| `same_instance_across_papers` | bool \| null | **跨篇概念节点恒为 `false`**：同名概念被共享，实例身份未被证实。单篇节点为 `null`。 |
| `mentions[]` | array | 每个表面形式在原文中的一处出现，见下。 |

### `graph.nodes[].mentions[]`

| 字段 | 类型 | 含义 |
|-|-|-|
| `source_id` `paragraph_id` | string | 该出现所在论文与**JATS 段落 ID**。 |
| `locator` | object | 完整定位，见下。 |
| `quote` | string | **逐字**原文片段（短引）。 |
| `rule` | string | 哪个抽取规则产生的，便于复核与追责。 |
| `evidence_strength` | enum | `direct` / `table` / `indirect` / `inferred`。 |

### `locator`

| 字段 | 类型 | 含义 |
|-|-|-|
| `source_id` | string | 论文。 |
| `paragraph_id` | string | 段落 ID。**这是组织方给的 JATS 段落 ID，不是 PDF 页码。** |
| `section` | string | 章节路径（如 `Results / Optical spectroscopy`）。 |
| `char_start` `char_end` | int \| null | 该 quote 在段落文本里的 Unicode 码点区间。 |
| `match_kind` | enum | `exact` / `folded` / `squashed` / `unverified`。前三者说明命中方式，`unverified` 只出现在 baseline 对照结果里。 |
| `matched_text` | string | 实际命中的原文切片。**必须等于 `paragraph_text[char_start:char_end]`。** |
| `paragraph_sha256` | string | 段落文本哈希，证明引文是对着哪一份段落校验的。 |

> `locator` 里**不允许**出现 `page` / `page_number`。schema 用 `not` 明确禁止，
> 校验器 V09 再查一次。

### `graph.edges[]`

| 字段 | 类型 | 含义 |
|-|-|-|
| `id` | `edge:<12 hex>` | 由 `(type, subject, object, source, paragraph)` 派生。 |
| `type` | enum | 受控关系：`uses` `compares` `outperforms` `supports` `contradicts` `measures` `characterizes`。 |
| `subject` `object` | node id | 两端都必须存在（校验器 V06）。 |
| `subject_label` `object_label` | string | 便于人读的冗余。 |
| `direction` | string | 由关系词表给出（如 `subject -> object`），**不是**由抽取顺序决定。 |
| `conditions` | string[] | 这条关系成立的条件（温度、衬底、剂量、比较基准…）。`compares`/`outperforms`/`measures` 缺它会被 QC-05 记为间接证据。 |
| `evidence_strength` | enum | 证据强度。`inferred` 会把整份结果压成 `partial`。 |
| `basis` | string | 间接证据必须写明"还差哪一步推理"。 |
| `evidence.locator` `evidence.quote` | — | 与节点同构的一处逐字证据。 |

### `graph.issues[]`

抽取/建边过程中被拒绝或降级的边：`dangling_edge`、`relation_outside_vocabulary`、
`relation_signature_violation`、`missing_conditions`。**保留而不是丢弃**，因为它们说明了
"为什么这张图里没有某条边"。

### `graph.stats`

`nodes` `edges` `nodes_by_type` `edges_by_type`。`nodes`/`edges` 必须与数组长度一致（校验器 V10）。

## 查询 `queries[]`

| 字段 | 类型 | 含义 |
|-|-|-|
| `id` | string | `Q1-shared-concepts` 等。 |
| `question` | string | 人话问句。 |
| `kind` | string | `cross_paper_overlap` / `relation_traversal` / `refusal_with_evidence` / `conflict_scan`。 |
| `executed` | const true | 确实在图结构上跑过。 |
| `subgraph` | object | 本次查询触及的节点/边 id。 |
| `answers` | array | 结果。允许为空。 |
| `answer_count` | int | **必须等于 `len(answers)`**（校验器 V16）。 |
| `undetermined[]` | array | 图**回答不了**的部分：`question` `status`(`undetermined`/`refused`) `reason`，可选 `rule` `what_would_be_needed` `human_takeover`。答案为空时这一项不能为空。 |
| `evidence` | string[] | 支撑本次查询的定位（`source_id#paragraph_id`）。 |

## 证据 `evidence`

| 字段 | 类型 | 含义 |
|-|-|-|
| `ledger[]` | array | 节点与边的证据总账：`kind`(`entity`/`relation`) `id` `label` `locator` `quote`。导出成 `evidence.csv`。 |
| `rejected[]` | array | **未通过校验的引文，保留不删。** `verification.found=false`、`reason`(`unknown_locator`/`quote_not_in_paragraph`)、`detail`、`paragraph_sha256`。 |
| `counts.ledger` / `counts.rejected` / `counts.verified_edges` | int | 与数组实际长度一致（校验器 V13）。 |

> 一条被拒绝的引文本身就是发现：它说明"模型记得的内容"和"论文写的内容"不一致。

## 运行 `run`

| 字段 | 类型 | 含义 |
|-|-|-|
| `status` | enum | `success` / `partial` / `uncertain` / `refused`。**由检查结果派生，不接受手工指定。** |
| `mode` | enum | `skill` / `baseline`。baseline 只用于同输入对照。 |
| `semantic_layer` | string | 实体与关系从哪来：agent 草稿路径，还是规则抽取器。 |
| `counts` | object | 节点/边/mention/查询/被拒引文/合并/不得合并。 |
| `limits` | string[] | 本次运行**自认**的边界。 |
| `warnings` | object[] | 语料适配警告（布局被改写、缺 manifest、单篇等）。 |
| `elapsed_seconds` `generator` | — | 运行元数据。 |
| `refused_reason` | string | 仅 `refused` 时出现。 |

## 不确定 `uncertainty[]`

| 字段 | 类型 | 含义 |
|-|-|-|
| `kind` | string | `must_not_merge` / `unverified_evidence` / `missing_manifest` / `single_source` / `relation_without_conditions` / `duplicate_source_id` … |
| `description` | string | 人话说明。 |
| `related` | string | 关联的节点/定位。 |
| `human_takeover` | string | **人工从哪一步接管。** |

## 质检 `quality_control`

| 字段 | 类型 | 含义 |
|-|-|-|
| `checks[]` | array | `code` `severity`(`critical`/`warn`) `passed` `detail`。规则见 `quality-rules.md`。 |
| `critical_failures` / `warnings` | int | 统计。 |
| `contract_source` | string | 与 `contract.source` 一致。 |

## 变更 `change_log[]`

`at` `change` `detail`。增量更新时保留既有 id，只追加新条目。
