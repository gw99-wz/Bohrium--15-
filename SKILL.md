---
name: literature-knowledge-graph
description: "Turn a small corpus of real papers into an evidence-bound knowledge graph: extract researcher / material / method / task / metric / conclusion entities with their verbatim source spans, bind every node and edge to a JATS paragraph id and a character-accurate quote, align aliases across papers while recording which candidates must NOT be merged, and answer at least three real queries — including the ones the graph cannot answer. Use when a task asks to build a literature knowledge graph, to extract entities and relations from papers with provenance, to align concepts across papers, to answer questions over a paper set while citing the exact paragraph, or to audit whether a graph edge is really supported by the text. Triggers on: knowledge graph, entity relation extraction, evidence binding, cross-paper alignment, alias resolution, provenance, 知识图谱, 实体关系抽取, 证据绑定, 跨篇对齐, 图谱查询."
---

# Literature Knowledge Graph

Build a graph whose every node and edge can be walked back to one paragraph of one paper —
and whose **refusals** are as explicit as its answers.

## What this skill does

Input: a corpus bundle (manifest + paragraph material for two or more papers) plus a case
file describing the output contract.

Output: `result.json` with

1. **nodes** — researcher / material / method / task / metric / conclusion, each with the
   verbatim span it was read from;
2. **edges** — `uses` / `compares` / `outperforms` / `supports` / `contradicts` / `measures` /
   `characterizes`, each with direction, conditions and one verbatim span;
3. **alignment** — every merge with its basis, and every **refused** merge with its reason;
4. **queries** — at least three that were actually executed, including what the graph
   cannot settle;
5. **status** — derived from the checks, never declared by hand.

### The division of labour

| Who | Does | Why |
|-|-|-|
| **You (the agent)** | read the papers and author `draft.json`: which spans are entities, which relations the text actually states | semantic judgement cannot be faked by a regex |
| **The scripts** | verify each quote exists verbatim, compute offsets, enforce relation signatures, mint stable ids, run QC, derive status | mechanical facts cannot be argued with |

Nothing here trusts the model's memory. A quote that is not literally in the cited
paragraph is rejected, counted, and reported — never silently deleted.

## When to use

Use it when the answer must be checkable by someone else: literature knowledge graphs,
evidence-bound extraction, cross-paper concept alignment, "which paper says what, and where".

Do not use it to write a narrative review, to rank papers, or when no source text exists —
there is nothing to verify against.

## Pipeline

```
corpus/                  corpus-manifest.json + material.json (+ primary/secondary-material.json)
   │  scripts/run_pipeline.py  (loads the bundle, resolves the case's output_contract)
   ▼
draft.json               ← YOU write this (or omit it and the rule extractor runs)
   │  bind      : locate every quote in its paragraph, exact → folded → squashed
   │  align     : group by (type, canonical); record merges and refusals
   │  build     : resolve endpoints, enforce the relation signature, apply conditions
   │  query     : 4 real traversals
   │  qc        : QC-01..QC-09 → status
   ▼
output/result.json       + graph.json / graph.jsonl / evidence.csv / queries.json / qc.json / run.log
   │  scripts/validate_result.py   ← independent replay, reopens the corpus
   ▼
PASS / FAIL
```

### 1. Read the corpus

`corpus-manifest.json` carries per-paper **DOI, authors, sha256 and licence**.
`primary-material.json` is paper 1 and `secondary-material.json` is the added paper.

Two rules from the task that this skill enforces mechanically:

- **Root-level DOI/title/authors belong to paper 1.** They are never copied onto paper 2.
  Provenance lives in `sources[]`, one entry per paper.
- **A locator is a JATS paragraph id, not a PDF page.** There are no page numbers in this
  corpus and the schema forbids a `page` field. Never call your own render "page 1".

Each paragraph arrives with the publisher's text, so quote what is on the page — not what
you remember about the paper.

### 2. Author the draft (your job)

```json
{
  "entities": [
    {
      "source_id": "PMC12312154",
      "paragraph_id": "PMC12312154.s0.p004",
      "label": "n-BuLi treatment",
      "entity_type": "method",
      "quote": "Through a controlled, short-duration n-butyl-lithium (n-BuLi) treatment on WS2 monolayers"
    }
  ],
  "relations": [
    {
      "rel_type": "measures",
      "subject_label": "n-BuLi treatment",
      "subject_type": "method",
      "object_label": "emission energy = 2.03 eV",
      "object_type": "metric",
      "source_id": "PMC12312154",
      "paragraph_id": "PMC12312154.s1.p005",
      "quote": "the emission energy of the treated film is 2.03 eV",
      "conditions": ["treated film", "room temperature"],
      "evidence_strength": "direct",
      "basis": ""
    }
  ]
}
```

Rules that decide whether this scores:

1. **Quote the minimal span that carries the claim.** One sentence, one clause. Do not paste
   a paragraph, and do not paraphrase — a paraphrase fails V07 and is reported.
2. **One relation per edge.** "A uses B and is better than C" is two edges. `outperforms`
   without a `conditions` entry gets downgraded to `indirect` and flagged.
3. **Entity type from the controlled vocabulary only** (`references/entity-types.json`).
   A type outside it is recorded as `ontology_extension` and does not vanish quietly.
4. **Distinguish the authors' own claim from a restatement of someone else's.** A review
   sentence ("it is well known that…") is not direct evidence for a relation.
5. **A shared term is not a shared instance.** `monolayer WS2` in both papers proves both
   studied *that kind* of object, not the same sample. Record the difference in
   `conditions`; the aligner will emit `must_not_merge` for you.
6. **Metrics stay per-paper.** Never fuse two papers' numbers into one node, and never let a
   query compare them unless both papers state the same sample and setting.

If you skip `draft.json`, `scripts/run_pipeline.py` falls back to a deterministic rule
extractor. That path is real and testable, but its `researcher` and `task` coverage is
lexicon-bound — pass a draft when the classification matters.

### 3. Build

```bash
python scripts/run_pipeline.py \
  --corpus /input/corpus \
  --case /input/case.json \
  --out /app/output \
  --draft /app/work/draft.json
```

Exit code **is** the status: `0` success, `1` partial, `4` uncertain, `5` refused.

What the builder guarantees, and how:

| Guarantee | Mechanism |
|-|-|
| every quote is literally there | three-pass locate: exact → typographic folding → whitespace squashing; a miss is rejected, with the paragraph hash |
| ids are stable across reruns | `node:`/`edge:` ids are SHA-1 over `(type, canonical, sources)` / `(type, subject, object, source, paragraph)` |
| relation type and direction are legal | `references/relations.json` gives subject/object type signatures; violations are rejected and logged |
| merges are justified | every merge carries `basis`; every refusal carries `kind: must_not_merge` and a `level` (`instance` / `condition` / `value`) |
| status is honest | derived from rejected quotes, unverified edges, contract coverage and critical QC |

### 4. Validate independently

```bash
python scripts/validate_result.py --result /app/output/result.json --corpus /input/corpus --case /input/case.json
```

It reopens the corpus and re-locates **every** span. `verdict: PASS` with an empty
`failures` list is the bar. A FAIL is a defect in the artifact: fix the evidence, do not
edit the word "success".

## Output contract

The case's real contract lives in `/input/case.json`. The Skill **resolves it at run time**
and never hardcodes field names:

- looks for `output_contract` / `required_outputs` / `expected_outputs` / `required_fields`
  (also one level down under `task` / `spec` / `requirements` / `meta`);
- accepts a list of names, a mapping of name → description, or objects carrying `name`/`field`;
- reports `contract.source = "case.json:<path>"` and `satisfied: true|false`.

If the case carries no machine-readable contract, the documented fallback set is used and
`contract.source = "fallback"` — visible in the artifact, so nobody mistakes it for the
platform's requirement. Missing contract fields force `partial` (QC-06).

See `references/field-dictionary.md` for every field.

## Status semantics

| status | meaning | exit |
|-|-|-|
| `success` | evidence verified, contract satisfied, no critical QC failure | 0 |
| `partial` | real output with declared gaps: a rejected quote, an unresolved contract field, a critical QC failure | 1 |
| `uncertain` | the material did not settle it | 4 |
| `refused` | nothing survived verification, or the corpus is unreadable | 5 |

Never upgrade a status by hand. `validate_result.py` checks the implication
`success ⇒ (0 rejected quotes AND 0 unverified edges)` and fails the artifact if it breaks.

## Failure modes

| Situation | Detected by | Action |
|-|-|-|
| quote not in the cited paragraph | `rejected[].verification.reason = quote_not_in_paragraph` | re-read the paragraph and quote it again; never edit the source |
| paragraph id unknown | `reason = unknown_locator` | use an id that exists in `material.json` |
| corpus has one paper only | `uncertainty.kind = single_source` | cross-paper queries return zero answers with an explanation; say so |
| corpus unreadable | exit `5`, `run.refused_reason` | report; do not invent a graph |
| relation type outside the vocabulary | `graph.issues[] = relation_outside_vocabulary` | map to the closest controlled type and record why |
| relation needs a basis but has none | `graph.issues[] = missing_conditions` | add `conditions`, or accept `indirect` and keep the warning |
| two papers disagree | `uncertainty.kind = must_not_merge` (+ `change_log`) | quote both, keep both, let a human pick |
| query has no answer | `queries[].undetermined` | say why that is not proof of absence |

## Limits (state these in your answer)

- The rule extractor is deterministic and conservative. It emits no edge without a cue,
  which means the graph is **sparse rather than wrong** — a missing edge is not evidence of
  absence.
- Locators are JATS paragraph ids and Unicode code-point offsets into the paragraph text.
  They are not PDF coordinates and not PDF pages.
- `researcher` and `task` entities depend on the draft: the extractor only recognises a
  small lexicon for them.
- Alignment normalises case, punctuation, whitespace, corpus-defined abbreviations and form
  qualifiers (`monolayer …`). It does not use an external ontology, so a synonym the corpus
  never defines will stay two nodes — deliberately: a duplicate node is recoverable, a wrong
  merge is not.
- The verifier proves a quote **exists** and is **located**. It cannot prove the quote
  **supports** the relation; QC-05 and the `evidence_strength` field only flag the usual ways
  that goes wrong.
- `mode=baseline` exists solely for the same-input comparison report. It is not an answer
  path and its result fails V12 by construction.

## Files

```
SKILL.md                          this file
schemas/result.schema.json        machine-readable output contract
references/entity-types.json      entity vocabulary + instance qualifiers + cross-paper rule
references/relations.json         relation vocabulary with subject/object signatures
references/field-dictionary.md    every field, type and meaning
references/quality-rules.md       QC-01..09 and validator V00..V17, with criteria
scripts/kg.py                     text / corpus / contract / ontology / id / status library
scripts/pipeline.py               extract, bind, align, build, query, QC
scripts/run_pipeline.py           end-to-end CLI (skill | baseline)
scripts/validate_result.py        independent replay validator
scripts/compare.py                same-input skill-vs-baseline experiment
scripts/fetch_corpus.py           rebuild the local two-paper corpus from PMC JATS
tests/run_tests.py                9 self-contained cases, including the failure paths
docs/method.md                    why the design is shaped this way
docs/setup.md                     dependencies, invocation, troubleshooting
```
