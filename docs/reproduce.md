# 复现记录：本仓库的证据是怎么来的

本文件里的每个数字都由仓库内的脚本产出，可用文末三条命令重跑。
语料是**两篇真实、开放获取（CC BY）的论文**，由 `scripts/fetch_corpus.py` 从 PMC JATS 全文拉取；
它**不是**赛题的官方案例（官方案例由平台在评测时提供）。

## 语料

| source_id | 论文 | DOI | 许可 | 段落 |
|-|-|-|-|-|
| `PMC12312154` | Vibronically Coherent Exciton Trapping in Monolayer WS2 | 10.1021/acsnano.5c08533 | CC BY 4.0 | 33 |
| `PMC13495497` | Evidence for Thermodynamically Stable "On-Top" Sulfur Divacancy in Monolayer WS2 | 10.1021/acsnanoscienceau.5c00165 | CC BY 4.0 | 11 |

两篇都研究单层 WS2，但一篇用超快振动光谱看激子俘获，另一篇用第一性原理看硫空位稳定性。
这正是跨篇对齐要处理的形态：**术语重叠、样品与条件不同**。

## 结果（`evidence/result.json`）

| 项 | 值 |
|-|-|
| 状态 | `success`（exit 0） |
| 节点 | 97（material 30 / method 27 / conclusion 27 / metric 7 / task 6） |
| 边 | 21（uses 13 / measures 3 / characterizes 2 / supports 2 / compares 1） |
| 证据片段 | 253（节点）+ 21（边）= 274 处，**逐字定位成功率 274/274** |
| 被拒引文 | 0 |
| 合并记录 | 5（全部带 `basis`） |
| 不得合并记录 | 3（`level=instance`） |
| 查询 | 4 个，全部实际执行 |
| 契约 | `case.json:output_contract`，`satisfied: true` |
| 独立校验 | **PASS，0 项失败**（`evidence/validate.txt`） |

## 同输入对照（`evidence/compare.txt`）

同一份语料、同一套抽取规则，只切换 `--mode`：

| 指标 | skill | baseline |
|-|-|-|
| 边 | 21 | 13 |
| 证据逐字校验通过率 | **1.0** | **0.0** |
| 带字符偏移的边 | 21 | 0 |
| 重复概念节点 | 0 | 3 |
| 不得合并记录 | 3 | 0 |
| 记录"回答不了"的查询 | 4 | 0 |
| 声称成功且自洽 | **是** | **否** |

两个探针：

- **E2 篡改**：把一条引文里的一个数字改掉 → skill 检测到并降到 `refused`；baseline 原样接受、报告 `success`。
- **E3 重跑**：同输入跑两次，118 个 id 与计数完全一致。

## baseline 为什么会被自己的产物打脸

`evidence/baseline-validate.txt`：

```
[FAIL] V12-success-means-verified  success declared with 0 rejected quotes and 13 unverified edges
[FAIL] V14-queries                 0 queries (task requires >= 3)
verdict: FAIL (2 failing checks)
```

baseline 不是被"做坏"的：一个没有校验环节的流程**无法知道**自己的引文没落地，所以必然声明成功。
V12 就是为这件事写的规则。这也是"使用增益"里可验证的那部分——
不是"我更好"，而是"同一份输入，加上这个 Skill 之后，产物能通过一套它自己通不过的检查"。

## 回归测试（`tests/run_tests.py`）

9 个自包含场景，离线、无第三方包，全部通过：

| 场景 | 断言 |
|-|-|
| `clean` | 双论文语料 → `success`，独立校验 PASS |
| `tampered_quote` | 改一个数字 → 引文被拒，状态不是 `success` |
| `unknown_locator` | 段落 id 不存在 → `reason=unknown_locator`，不产生幽灵节点 |
| `single_paper` | 只有一篇 → 记录 `single_source`，跨篇查询零答案并解释 |
| `unreadable_corpus` | 空语料 → exit 5，`refused`，不造图 |
| `baseline_fails_validation` | baseline 声称成功，校验器 FAIL 在 V12 |
| `contract_fallback` | 案例无契约 → `contract.source=fallback` 且写进 `limits` |
| `determinism` | 两次运行 id 完全一致 |
| `must_not_merge` | 同名指标跨篇 → `level=value` 拒绝合并，且不存在跨篇 metric 节点 |

## 重跑

```bash
python scripts/fetch_corpus.py --out corpus/ws2-pair
python scripts/run_pipeline.py --corpus corpus/ws2-pair --case corpus/ws2-pair/case.json --out evidence
python scripts/validate_result.py --result evidence/result.json --corpus corpus/ws2-pair --case corpus/ws2-pair/case.json
python scripts/compare.py --corpus corpus/ws2-pair --case corpus/ws2-pair/case.json --out evidence/compare.json
python tests/run_tests.py
```

## 已知限制（会写进 `run.limits`）

- 规则抽取器只在有线索时建边，图**稀疏**；缺边不代表原文没有该关系。
- `researcher` / `task` 两类实体依赖 `draft.json`；规则抽取器只有小词表。
- 对齐不使用外部本体，语料未定义的近义词会保留为两个节点——宁可重复，不可错并。
- 定位是 JATS 段落 ID + 码点偏移，不是 PDF 页码。
- 校验证明引文**存在且被定位**，不证明引文**支撑**该关系。
