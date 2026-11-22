# 环境与调用

## 依赖

| 项 | 要求 |
|-|-|
| Python | 3.10+（开发与验证用 3.15.0） |
| 第三方包 | **无**。只用标准库（`json` `hashlib` `re` `unicodedata` `dataclasses` `csv` `argparse`）。 |
| 网络 | 只有 `scripts/fetch_corpus.py` 需要（Europe PMC / NCBI）。构建与校验完全离线。 |

没有依赖是刻意的：评测镜像不保证装了什么，`pip install` 也不该成为提交的一部分。
`jsonschema` 同样没装——`schemas/result.schema.json` 是人读契约，
真正的机器校验是 `validate_result.py` 里手写的检查（它会重开语料，比 schema 更严）。

## 目录约定

```
<input>/  case.json + corpus-manifest.json + material.json
          + primary-material.json + secondary-material.json
<work>/   draft.json（可选，agent 写）
<output>/ result.json + graph.json + graph.jsonl + evidence.csv
          + queries.json + qc.json + run.log
```

## 三条命令

```bash
# 1. 构建（--draft 可选；不给则用规则抽取器）
python scripts/run_pipeline.py --corpus /input --case /input/case.json \
    --out /app/output --draft /app/work/draft.json

# 2. 独立校验（重新打开语料，重定位每条引文）
python scripts/validate_result.py --result /app/output/result.json \
    --corpus /input --case /input/case.json

# 3. 同输入对照（证据用于 docs/reproduce.md 的那张表）
python scripts/compare.py --corpus /input --out /app/output/compare.json
```

退出码：`0` success · `1` partial · `4` uncertain · `5` refused（`validate_result.py`：`0` PASS / `1` FAIL）。

## 本地复现整套材料

```bash
# 重建两篇真实开放获取论文组成的本地语料（CC BY，含 DOI/许可/哈希）
python scripts/fetch_corpus.py --out corpus/ws2-pair

python scripts/run_pipeline.py  --corpus corpus/ws2-pair --case corpus/ws2-pair/case.json --out output
python scripts/validate_result.py --result output/result.json --corpus corpus/ws2-pair --case corpus/ws2-pair/case.json
python scripts/compare.py        --corpus corpus/ws2-pair --case corpus/ws2-pair/case.json --out output/compare.json
python tests/run_tests.py
```

`corpus/` 不进 git（体积 + 许可），但 `evidence/` 里的结果文件进 git，审阅者不必联网就能看到产物；
重跑上面的 fetch 即可把语料恢复到逐字节等价（manifest 里记了 sha256）。

## 常见故障

| 现象 | 原因 | 处理 |
|-|-|-|
| `refused`，`run.refused_reason` 提到 `no papers found` | `<input>` 里既没有 `material.json` 也没有 primary/secondary 文件 | 确认输入目录；本 Skill 不会凭空造图 |
| 所有引文被 `quote_not_in_paragraph` 拒掉 | 引文是转述或从别处粘的 | 回到段落重取逐字短引 |
| `paragraph has no text` | `material.json` 的段落对象缺 `text`/`content` 字段 | 检查输入的字段名；适配失败会写进 `run.warnings` |
| 状态是 `partial` 而看不出原因 | 看 `quality_control.checks` 里 `passed: false` 的项，和 `uncertainty[]` | 一般都是被拒引文或 contract 缺字段 |
| `PermissionError` / `WinError 5` 在临时目录 | 受限沙箱下系统临时目录不可写 | 脚本只在显式 `--out` 下写文件；测试用仓库内 `.testwork/` |
| `compare.py` 报 baseline FAIL | 正常，这是设计要展示的结果 | 见 `references/quality-rules.md` V12 |

## 平台侧

- 提交物是 Skill 本体（`SKILL.md` + `scripts/` + `references/` + `schemas/`），不是预制答案。
- 平台把 Skill 装到 `/skills/weekly-skill`，用官方 Harness 在固定案例上跑。
- 资源上限：≤50 个 UTF-8 文本文件、单文件 ≤500KB、合计 ≤2MB、无 symlink。
  本仓库的 Skill 部分远低于上限（`corpus/` 与 `output/` 不在其中）。
