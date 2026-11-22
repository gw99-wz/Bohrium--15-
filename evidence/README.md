# evidence/ — 已提交的运行产物

这些文件由仓库内的脚本在**真实开放获取论文**上产出，随仓库提交，便于审阅者不联网、
不重跑就能检查。语料本身不进仓库（体积 + 许可），可用 `scripts/fetch_corpus.py` 恢复。

| 文件 | 是什么 |
|-|-|
| `result.json` | skill 模式的完整结果（envelope + graph + queries + evidence ledger + qc） |
| `evidence.csv` | 节点与边的证据总账，含 `char_start` `char_end` `match_kind`，可直接和原文对照 |
| `queries.json` | 4 个查询的答案与"回答不了的部分" |
| `qc.json` | QC-01…QC-09 的逐条结果 |
| `run.log` | 状态、模式、计数、本次自认的限制 |
| `validate.txt` | `validate_result.py` 的独立校验输出（PASS） |
| `compare.json` / `compare.txt` | 同输入 skill vs baseline 对照的原始数据与表格 |
| `baseline/result.json` | baseline 模式的结果（声称 `success`） |
| `baseline-validate.txt` | 同一套校验器对 baseline 的判定（**FAIL**：V12 与 V14） |

`graph.json` / `graph.jsonl` 没有提交：它们只是 `result.json` 里 `graph` 的另一种落盘形式，
重跑 `scripts/run_pipeline.py --out evidence` 就会生成。

## 怎么核对

1. 打开 `evidence.csv`，随便挑一行，用 `source_id` + `paragraph_id` 找到那一段
   （`corpus/ws2-pair/material.json` 里有全文，或用 DOI 打开原论文），
   确认 `quote` 字面存在、`char_start:char_end` 切片一致。
2. 打开 `result.json` 的 `queries[].undetermined`，看它**拒绝**回答了什么、为什么。
3. 打开 `compare.txt`，看同一份输入在有无校验环节时的差别。
4. 打开 `baseline-validate.txt`，看 baseline 的 `success` 是怎样被 V12 判死的。

## 注意

- `evidence/` 不是"正式案例答案"。官方 3 个固定案例由平台提供，Skill 在运行时读取
  `/input/case.json` 的 `output_contract` 再产出结果。
- 这里的引文来自 CC BY 4.0 论文，`result.json` 的 `sources[]` 保留了 DOI、许可和原文哈希。
- 语料是**本地开发语料**（两篇真实论文），不是赛题的官方案例；`docs/reproduce.md` 写明这一点。
