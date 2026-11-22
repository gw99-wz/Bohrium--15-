# 最小调用示例

**这是示意，不是成绩。** 语料文本是编的（`corpus-manifest.json` 里 `synthetic: true`，
DOI 是 `10.0000/...` 这类占位），只为展示接口和完整流程，避免任何人把示例当正式案例结果。

## 输入

| 文件 | 作用 |
|-|-|
| `corpus/case.json` | 案例说明 + `output_contract`（要求 `case_id` `sources` `graph` `queries` `evidence` `run` `uncertainty` `quality_control`） |
| `corpus/corpus-manifest.json` | 两篇论文的 DOI / 作者 / 许可 / 哈希 |
| `corpus/material.json` | 两篇的段落索引（`paragraph_id` + `section` + `text`） |

语料只有 4 段：A 篇用 PL 光谱测 WS2、发射能 2.03 eV（室温）；B 篇同样用 PL、发射能 2.10 eV（4 K）。
两篇都写到了同一个概念（WS2 / 钨的二硫化物），但**条件不同**——这正是对齐逻辑要处理的情形。

## 调用

```bash
python scripts/run_pipeline.py --corpus examples/minimal/corpus --out /tmp/kg-example
python scripts/validate_result.py --result /tmp/kg-example/result.json \
    --corpus examples/minimal/corpus --case examples/minimal/corpus/case.json
```

## 产物（`output/`）

| 文件 | 内容 |
|-|-|
| `result.json` | 完整结果：`case_id` `contract` `sources` `graph` `queries` `evidence` `alignment` `run` `uncertainty` `quality_control` `change_log` |
| `graph.json` / `graph.jsonl` | 图本体。`.jsonl` 一行一条 node/edge，便于直接喂给别的工具 |
| `evidence.csv` | 节点与边的证据总账（含 `char_start` `char_end` `match_kind`），可直接在表格里核对 |
| `queries.json` | 4 个查询的答案与"回答不了的部分" |
| `qc.json` | QC-01…QC-09 |
| `run.log` | 状态、模式、计数、本次自认的限制 |

## 这个示例能看出什么

- **合并**：`WS2` 与 `Tungsten disulfide` 通过语料里自己写的 `Tungsten disulfide (WS2)` 定义合并为
  一个概念节点，`scope = cross_paper_concept`，`same_instance_across_papers = false`。
- **拒绝合并**：`must_not_merge` 里有 `level = value` 的记录——两篇的发射能（2.03 eV / 2.10 eV）
  是各自的测量结果，不能合成一个节点。
- **查询会拒答**：`Q3-cross-paper-metric-comparison` 明确回答"不能比较"，并写出需要什么条件才能比较。
- **通过独立校验**：`validate_result.py` 的 18 项检查全 PASS，因为所有引文都能回到那 4 段原文。

## 换成真实语料

```bash
python scripts/fetch_corpus.py --out corpus/ws2-pair   # 两篇 CC BY 开放获取论文
python scripts/run_pipeline.py --corpus corpus/ws2-pair --case corpus/ws2-pair/case.json --out output
```
