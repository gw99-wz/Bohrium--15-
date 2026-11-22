# 质检规则与独立校验

两套检查，故意分开：

- **QC-01…QC-09** 在构建时跑（`scripts/pipeline.py::qc`），结果写进 `result.json` 的
  `quality_control.checks`。它们决定 `run.status`。
- **V00…V17** 在构建之后跑（`scripts/validate_result.py`），**重新打开语料**、**重新定位每一条引文**，
  不信构建器记录的任何偏移、计数或状态词。

一套自证不算证明。所以两套都要过。

## 构建期 QC

| 编号 | 级别 | 判据 | 不过会怎样 |
|-|-|-|-|
| QC-01 | critical | 节点 + 边的 id 全局唯一 | `partial` |
| QC-02 | critical | 每个节点至少有一个 mention | `partial` |
| QC-03 | critical | 每条边都有 quote 且 locator 带 `match_kind` | `partial` |
| QC-04 | warn | 没有被拒绝的引文（`quote_not_in_paragraph`） | 提示；被拒条目进 `uncertainty` |
| QC-05 | warn | `compares`/`outperforms`/`measures` 的边都写了 `conditions` | 该边降级为 `indirect`，并进 `uncertainty` |
| QC-06 | critical | `output_contract` 的字段全部满足 | `partial` |
| QC-07 | warn | 没有 (type, canonical) 重复的节点 | 提示 |
| QC-08 | critical | 所有 `type` 都在受控词表里 | 越界类型被记录为 `ontology_extension` |
| QC-09 | warn | 没有短于 15 字符的证据片段 | 提示：太短不足以支撑 |

## 独立校验 V 码

`python scripts/validate_result.py --result output/result.json --corpus <corpus> [--case <case.json>]`
退出码 `0` = PASS，`1` = FAIL。一条 FAIL 就是 artifact 的缺陷，不是偶发。

| 码 | 判据 |
|-|-|
| V00 | `result.json` 能被解析 |
| V01 | 语料能被**重新加载**（论文数、段落数打印出来） |
| V02 | `graph.nodes` / `graph.edges` 都是数组 |
| V03 | 信封字段齐全（`case_id` `sources` `graph` `queries` `evidence` `run` `uncertainty`） |
| V04 | 若 `case.json` 带机器可读契约，则逐字段满足 |
| V05 | 所有 id 唯一 |
| V06 | 每条边的 `subject`/`object` 都能解析到真实节点 |
| V07 | **每个 mention 与每条边的 quote 都能在它声称的段落里重新定位到** |
| V08 | 记录的 `char_start/char_end` 切片与 `quote` 一致 |
| V09 | 没有任何记录携带 `page` / `page_number` 字段 |
| V10 | `stats.nodes`/`stats.edges` 与数组长度一致 |
| V11 | `status` 在四态枚举内 |
| V12 | **`status=success` ⇒ 被拒引文为 0 且未校验边为 0** |
| V13 | `evidence.counts.rejected` 与实际被拒条数一致 |
| V14 | 查询数 ≥ 3 |
| V15 | 每个查询 `executed: true` |
| V16 | 每个查询有 `answers`/`evidence` 键、`answer_count == len(answers)`、空结果必须给出 `undetermined` 解释 |
| V17 | 每个 source 都有 `doi`/`title`/`licence` |

### V12 是这套设计的核心

它只做一件事：**不允许在证据没落地的情况下宣布成功**。

用 baseline 模式跑同一批输入，就会看到这条规则在真实起作用——

```
python scripts/run_pipeline.py --corpus corpus/ws2-pair --out out-baseline --mode baseline
python scripts/validate_result.py --result out-baseline/result.json --corpus corpus/ws2-pair
# → FAIL: V12-success-means-verified（baseline 声称 success，却有 13 条未校验的边）
```

这不是把对照方做坏：一个没有校验环节的流程**无法知道**自己的引文没落地，所以它必然声明成功。
这恰好就是本 Skill 要消除的失败模式。

## 失败时怎么办

| 现象 | 含义 | 处置 |
|-|-|-|
| V07 FAIL | 引文被改写/记忆生成 | 回到段落重读，改成逐字短引；**不要**改原文去迁就引文 |
| V08 FAIL | 偏移与切片不一致 | 重新用 `pipeline.bind()` 生成定位，不要手写偏移 |
| V09 FAIL | 出现了页码 | 换成 `paragraph_id`；本 Skill 不产生页码 |
| V12 FAIL | 状态与证据矛盾 | 修证据，或承认 `partial`；不允许手工把状态改回 success |
| V16 FAIL | 查询是空壳 | 补 `undetermined` 说明，或删掉这个查询 |
| V17 FAIL | 来源元数据缺失 | 从 `corpus-manifest.json` 补 DOI/title/licence；补不到就声明为限制 |
