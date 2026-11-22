# Bohrium-15- · 阅读15｜文献知识图谱构建

玻尔（Bohrium）Playground 产品分站赛参赛作品。
赛题：**阅读15｜文献知识图谱构建**，Challenge ID `product-arena-10-760d1a0b`
（<https://play.bohrium.com/challenge/product-arena-10-760d1a0b>）。

一个可被 Agent 调用的 Skill：把一批**真实论文**转成**带证据的节点与边**，
每条节点/边都能回到**具体段落与逐字原文**，并且**明确记录自己合并了什么、拒绝合并什么、
回答不了什么**。

> 仓库不含任何官方案例答案、预制结果或评测分数。`examples/minimal/` 是**编造的**示例文本
> （`synthetic: true`），只用于展示接口。`evidence/` 是两篇 CC BY 论文上的真实运行产物，
> 用于证明流程可用——同样不是官方案例的答案。

## 它解决题面点名的四个问题

| 题面判据 | 机制 |
|-|-|
| **抽得准** | 实体必须带逐字 quote + JATS `paragraph_id`；不在原文的引文被拒并计入 `evidence.rejected[]` |
| **连得对** | 关系类型/方向/适用条件由 `references/relations.json` 的主客体签名约束；签名不符的边被拒绝并记入 `graph.issues[]`，而不是悄悄留下 |
| **对得齐** | 概念级合并（带 `basis`）与实例级拒绝（`must_not_merge`，`level = instance / condition / value`）分开记录；指标永不跨篇合并 |
| **查得到回得去** | 4 个真实查询；每个答案带定位；**回答不了的部分写进 `undetermined[]`**，空结果必须解释 |

## 快速开始

```bash
# 无第三方依赖，Python 3.10+
python scripts/run_pipeline.py --corpus examples/minimal/corpus \
    --case examples/minimal/corpus/case.json --out out/
python scripts/validate_result.py --result out/result.json \
    --corpus examples/minimal/corpus --case examples/minimal/corpus/case.json
python tests/run_tests.py
```

退出码即状态：`0` success · `1` partial · `4` uncertain · `5` refused。

## 交付内容

| 路径 | 作用 |
|-|-|
| `SKILL.md` | Skill 本体：用途、触发、流程、draft 约定、输出契约、状态语义、失败处理、限制 |
| `schemas/result.schema.json` | `result.json` 的机器可读契约（含"禁止出现页码字段"） |
| `references/entity-types.json` | 六类实体的定义 + 实例限定词 + 跨篇规则 |
| `references/relations.json` | 七种关系的主客体签名、线索词、拒建条件 |
| `references/field-dictionary.md` | 每个字段的类型与存在理由 |
| `references/quality-rules.md` | QC-01…QC-09 与校验器 V00…V17 的判定标准 |
| `scripts/kg.py` | 文本定位 / 语料加载 / 契约解析 / 本体 / id / 状态 |
| `scripts/pipeline.py` | 抽取 → 绑证 → 对齐 → 建边 → 查询 → QC |
| `scripts/run_pipeline.py` | 端到端 CLI（`skill` / `baseline`） |
| `scripts/validate_result.py` | **独立**校验：重开语料、重定位每条引文 |
| `scripts/compare.py` | 同输入对照实验（含篡改探针与重跑一致性） |
| `scripts/fetch_corpus.py` | 从 PMC JATS 重建本地双论文语料 |
| `examples/minimal/` | 最小调用示例（编造文本，已标明） |
| `tests/run_tests.py` | 9 个自包含场景，含降级路径 |
| `evidence/` | 真实论文上的运行产物与独立校验输出 |
| `docs/` | 方法说明、环境、复现记录、赛题精读 |

## 结果（真实论文，`evidence/`）

| 项 | 值 |
|-|-|
| 状态 | `success`（独立校验 18 项全 PASS） |
| 节点 / 边 | 97 / 21 |
| 逐字定位 | 274 / 274 |
| 被拒引文 | 0 |
| 合并 / 拒绝合并 | 5（全部带依据）/ 3 |
| 查询 | 4（含 1 个明确拒答的跨篇数值比较） |

同输入对照（`evidence/compare.txt`）：

| 指标 | skill | baseline |
|-|-|-|
| 证据逐字校验通过率 | **1.0** | **0.0** |
| 带字符偏移的边 | 21 | 0 |
| 重复概念节点 | 0 | 3 |
| 不得合并记录 | 3 | 0 |
| 声称成功且自洽 | **是** | **否** |

**baseline 的 `result.json` 会被同一套校验器判 FAIL（V12）**——这不是把它做坏，
而是没有校验环节的流程无法知道自己的引文没落地。详见 `docs/reproduce.md`。

## 评分维度（题面摘要）

| 维度 | 分值 |
|-|-|
| 实际效果 | 40（完成度 15 / 正确性 15 / 完整性 5 / 可核验性 5） |
| 使用增益 | 25（质量 15 / 效率 5 / 可验证性 5） |
| 创新增益 | 15（新增能力 5 / 方法改进 5 / 创新证据 5） |
| 可靠体验 | 10（调用与易用 3 / 稳定性 4 / 异常处理 3） |
| 生态贡献 | 10（文档 3 / 复用性 4 / 分享价值 3） |
| GitHub 公开链接 | +5 |

平台以 3 个固定案例 × 3 次重复 × 2 条件（基础 Agent / Skill Agent）对照评分，人工评审。

## 限制

- 规则抽取器保守，图**稀疏**：缺边不等于原文没有该关系。
- `researcher` / `task` 两类实体依赖 agent 写的 `draft.json`。
- 对齐只用语料自己定义的缩写，不引入外部本体——宁可多一个节点，不可错并一次。
- 定位是 JATS 段落 ID + 码点偏移，**不是** PDF 页码；schema 和校验器都禁止页码字段。
- 校验证明引文**存在且被定位**，不证明它**支撑**该关系。

## 链接

- 赛题页面：<https://play.bohrium.com/challenge/product-arena-10-760d1a0b>
- 本仓库：<https://github.com/gw99-wz/Bohrium--15->
