#!/usr/bin/env python3
"""End-to-end runner: corpus -> evidence-bound knowledge graph -> result.json.

    python scripts/run_pipeline.py --corpus corpus/ws2-pair --out output \
        [--case corpus/ws2-pair/case.json] [--mode skill|baseline] [--draft draft.json]

Exit codes carry the status: 0 success, 1 partial, 4 uncertain, 5 refused.

Two ways to supply the semantic layer:

  * `--draft draft.json` — the agent read the papers and wrote the entities/relations.
    This is the path the Skill prescribes, and the one the official cases will use.
  * no `--draft` — the deterministic rule extractor in pipeline.py is used. It covers
    material/method/metric/conclusion well and researcher/task only through a small
    lexicon; it exists so the pipeline is runnable and testable without a model.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import kg  # noqa: E402
import pipeline as P  # noqa: E402


def load_draft(path: Path) -> tuple[list[dict], list[dict]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    ents = data.get("entities") or []
    rels = data.get("relations") or []
    out_e = []
    for e in ents:
        out_e.append(
            {
                "source_id": e.get("source_id") or e.get("paper") or "",
                "paragraph_id": str(e.get("paragraph_id") or e.get("pid") or ""),
                "section": e.get("section", ""),
                "label": str(e["label"]),
                "entity_type": str(e.get("entity_type") or e.get("type") or ""),
                "quote": str(e.get("quote") or ""),
                "rule": "agent_draft",
                "evidence_strength": e.get("evidence_strength", "direct"),
            }
        )
    out_r = []
    for r in rels:
        out_r.append(
            {
                "rel_type": str(r.get("rel_type") or r.get("type") or ""),
                "subject_label": str(r["subject_label"]),
                "subject_type": str(r["subject_type"]),
                "object_label": str(r["object_label"]),
                "object_type": str(r["object_type"]),
                "source_id": r.get("source_id") or r.get("paper") or "",
                "paragraph_id": str(r.get("paragraph_id") or r.get("pid") or ""),
                "quote": str(r.get("quote") or ""),
                "rule": "agent_draft",
                "conditions": [str(c) for c in (r.get("conditions") or [])],
                "evidence_strength": r.get("evidence_strength", "direct"),
                "basis": str(r.get("basis") or ""),
            }
        )
    return out_e, out_r


def run(args) -> tuple[dict, int]:
    t0 = time.time()
    warnings: list[dict] = []
    limits: list[str] = []

    case_path = Path(args.case) if args.case else None
    case: dict = {}
    if case_path and case_path.exists():
        case = json.loads(case_path.read_text(encoding="utf-8"))
    else:
        warnings.append(
            {
                "kind": "case_json_missing",
                "detail": f"{case_path} not found",
                "impact": "output_contract falls back to the documented default field set",
            }
        )
        limits.append("case.json was not available, so the output_contract is the Skill's documented fallback")

    contract = kg.resolve_contract(case)
    case_id = kg.resolve_case_id(case, fallback=Path(args.corpus).name)

    try:
        corpus = kg.load_corpus(args.corpus, case)
    except kg.KgError as exc:
        result = {
            "case_id": case_id,
            "contract": {"source": contract.source, "required": contract.fields, "satisfied": False, "missing": contract.fields},
            "sources": [],
            "graph": {"nodes": [], "edges": [], "stats": {"nodes": 0, "edges": 0}},
            "queries": [],
            "evidence": {"ledger": [], "rejected": [], "counts": {}},
            "run": {
                "status": kg.REFUSED,
                "mode": args.mode,
                "counts": {"nodes": 0, "edges": 0},
                "limits": limits + [f"corpus unreadable: {exc}"],
                "refused_reason": str(exc),
                "elapsed_seconds": round(time.time() - t0, 2),
            },
            "uncertainty": [{"kind": "corpus_unreadable", "description": str(exc), "human_takeover": "check the corpus bundle paths"}],
            "quality_control": {"checks": [], "critical_failures": 1, "warnings": 0},
            "change_log": [],
        }
        return result, 5

    warnings.extend(corpus.warnings)
    ontology = kg.load_ontology()
    P.set_entity_types(ontology["types"]["entity_types"])
    alias_map = P.build_alias_map(corpus)

    # ---- semantic layer -------------------------------------------------------------
    if args.draft:
        draft_entities, draft_relations = load_draft(Path(args.draft))
        source_note = f"agent draft {args.draft}"
    else:
        mentions = P.extract_mentions(corpus, alias_map)
        rels = P.extract_relations(corpus, mentions)
        draft_entities = [m.__dict__.copy() for m in mentions]
        draft_relations = [r.__dict__.copy() for r in rels]
        source_note = "deterministic rule extractor (pipeline.extract_mentions/extract_relations)"
        limits.append(
            "entities came from the rule extractor: researcher and task coverage is lexicon-bound, "
            "so a production run should pass --draft authored from the papers"
        )

    # ---- bind evidence ---------------------------------------------------------------
    if args.mode == "baseline":
        # the naive path trusts whatever it was given; offsets are never computed
        verified_entities = [
            {**e, "locator": {"source_id": e["source_id"], "paragraph_id": e["paragraph_id"], "section": e.get("section", ""), "char_start": None, "char_end": None, "match_kind": "unverified", "paragraph_sha256": ""}}
            for e in draft_entities
            if e.get("label")
        ]
        verified_relations = [
            {**r, "locator": {"source_id": r["source_id"], "paragraph_id": r["paragraph_id"], "section": "", "char_start": None, "char_end": None, "match_kind": "unverified", "paragraph_sha256": ""}}
            for r in draft_relations
        ]
        rejected: list[dict] = []
    else:
        verified_entities, rej_e = P.bind(draft_entities, corpus)
        verified_relations, rej_r = P.bind(draft_relations, corpus)
        rejected = rej_e + rej_r

    # ---- align ------------------------------------------------------------------------
    aligned = P.align(verified_entities, verified_relations, alias_map, ontology, mode=args.mode)
    nodes = aligned["nodes"]
    edges, edge_issues = P.build_edges(verified_relations, nodes, ontology, alias_map, mode=args.mode)

    # ---- queries -----------------------------------------------------------------------
    if args.mode == "baseline":
        # a no-skill run has no query workflow at all; leaving it empty keeps the
        # comparison honest instead of scoring the baseline on a layer it does not have
        queries = []
    else:
        queries = P.run_queries(nodes, edges, corpus)

    # ---- contract + QC -----------------------------------------------------------------
    placeholder = {"case_id": case_id, "sources": [], "graph": {}, "queries": queries, "evidence": {}, "run": {}, "uncertainty": [], "quality_control": {}}
    contract_check = kg.contract_check(placeholder, contract)
    qc_report = P.qc(nodes, edges, rejected, contract_check, contract)

    counts = {
        "nodes": len(nodes),
        "edges": len(edges),
        "mentions": sum(len(n["mentions"]) for n in nodes),
        "queries": len(queries),
        "rejected_quotes": len(rejected),
        "merges": len(aligned["merges"]),
        "must_not_merge": len([b for b in aligned["blocked"] if b["kind"] == "must_not_merge"]),
    }
    verified_edges = sum(1 for e in edges if e["evidence"]["locator"].get("match_kind") not in (None, "unverified"))
    checks = {
        "has_content": bool(nodes),
        "readable": True,
        "unverified_evidence": len(rejected) + (len(edges) - verified_edges),
        "contract_missing": [] if contract_check["satisfied"] else contract_check["missing"],
        "critical_failures": qc_report["critical_failures"],
        "refused_reason": None if nodes else "no entity survived evidence verification",
    }
    status = kg.derive_status(checks)
    if args.mode == "baseline":
        # The naive run has no verifier, so it cannot know its evidence is unlocated and
        # reports success anyway. That false confidence is the failure this Skill removes;
        # validate_result.py catches it on the baseline artifact (V12).
        status = kg.SUCCESS
        limits.append(
            "baseline mode declares success without verifying a single quote; running "
            "validate_result.py on its result.json fails V12 by construction"
        )

    uncertainty: list[dict] = []
    for b in aligned["blocked"]:
        uncertainty.append(
            {
                "kind": b["kind"],
                "description": b.get("reason", ""),
                "related": b.get("canonical") or b.get("label", ""),
                "human_takeover": "review the pair before merging or comparing them",
            }
        )
    for r in rejected[:50]:
        uncertainty.append(
            {
                "kind": "unverified_evidence",
                "description": r["verification"]["detail"],
                "related": f"{r.get('source_id')}#{r.get('paragraph_id')}",
                "human_takeover": "re-read the paragraph and correct the quote; do not edit the source text",
            }
        )
    for w in warnings:
        uncertainty.append({"kind": w["kind"], "description": w["detail"], "human_takeover": w.get("human_takeover", "")})
    for issue in edge_issues[:50]:
        if issue["kind"] == "missing_conditions":
            uncertainty.append(
                {
                    "kind": "relation_without_conditions",
                    "description": f"{issue['rel_type']}: {issue['edge']} — {issue['detail']}",
                    "human_takeover": "quote the basis the comparison or measurement was made on, or drop the edge",
                }
            )

    if not contract.resolved:
        limits.append(
            "output_contract was not present in case.json, so the documented fallback field set was used"
        )
    limits.append("locators are corpus JATS paragraph ids; this Skill never emits a PDF page number")

    result = {
        "case_id": case_id,
        "contract": contract_check | {"case_id": case_id},
        "sources": [
            {
                "source_id": p.source_id,
                "role": p.role,
                "doi": p.doi,
                "title": p.title,
                "authors": p.authors,
                "licence": p.licence,
                "origin": p.origin,
                "sha256": p.sha256,
                "paragraph_count": len(p.paragraphs),
            }
            for p in corpus.papers
        ],
        "graph": {
            "nodes": nodes,
            "edges": edges,
            "issues": edge_issues,
            "stats": {
                "nodes_by_type": _count_by(nodes, "type"),
                "edges_by_type": _count_by(edges, "type"),
                "nodes": len(nodes),
                "edges": len(edges),
            },
        },
        "queries": queries,
        "evidence": {
            "ledger": [
                {
                    "kind": "entity",
                    "id": n["id"],
                    "label": n["label"],
                    "locator": m["locator"],
                    "quote": m["quote"],
                }
                for n in nodes
                for m in n["mentions"]
            ]
            + [
                {
                    "kind": "relation",
                    "id": e["id"],
                    "label": f"{e['subject_label']} {e['type']} {e['object_label']}",
                    "locator": e["evidence"]["locator"],
                    "quote": e["evidence"]["quote"],
                }
                for e in edges
            ],
            "rejected": rejected[:200],
            "counts": {
                "ledger": sum(len(n["mentions"]) for n in nodes) + len(edges),
                "rejected": len(rejected),
                "verified_edges": verified_edges,
            },
        },
        "alignment": {"merges": aligned["merges"], "blocked": aligned["blocked"]},
        "run": {
            "status": status,
            "mode": args.mode,
            "semantic_layer": source_note,
            "counts": counts,
            "limits": limits,
            "warnings": warnings,
            "elapsed_seconds": round(time.time() - t0, 2),
            "generator": {"name": "reading15-kg-skill", "version": "1.0.0"},
        },
        "uncertainty": uncertainty,
        "quality_control": qc_report,
        "change_log": [
            {
                "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "change": "initial graph build",
                "detail": f"mode={args.mode}; semantic layer={source_note}",
            }
        ],
    }
    contracts_check = kg.contract_check(result, contract)
    result["contract"] = contracts_check | {"case_id": case_id}
    return result, kg.STATUS_EXIT[status]


def _count_by(items: list[dict], key: str) -> dict:
    out: dict[str, int] = {}
    for it in items:
        out[it.get(key, "?")] = out.get(it.get(key, "?"), 0) + 1
    return dict(sorted(out.items()))


def write_outputs(result: dict, out_dir: Path, corpus: kg.Corpus | None) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    kg.write_json(out_dir / "result.json", result)

    graph = result.get("graph") or {"nodes": [], "edges": [], "stats": {}}
    kg.write_json(out_dir / "graph.json", graph)

    with (out_dir / "graph.jsonl").open("w", encoding="utf-8", newline="\n") as fh:
        for n in graph["nodes"]:
            fh.write(json.dumps({"record": "node", **n}, ensure_ascii=False) + "\n")
        for e in graph["edges"]:
            fh.write(json.dumps({"record": "edge", **e}, ensure_ascii=False) + "\n")

    with (out_dir / "evidence.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["kind", "id", "label", "source_id", "paragraph_id", "char_start", "char_end", "match_kind", "quote"])
        for row in result["evidence"]["ledger"]:
            loc = row["locator"]
            w.writerow(
                [
                    row["kind"],
                    row["id"],
                    row["label"],
                    loc.get("source_id", ""),
                    loc.get("paragraph_id", ""),
                    loc.get("char_start", ""),
                    loc.get("char_end", ""),
                    loc.get("match_kind", ""),
                    row["quote"].replace("\n", " "),
                ]
            )

    kg.write_json(out_dir / "queries.json", result["queries"])
    kg.write_json(out_dir / "qc.json", result["quality_control"])

    with (out_dir / "run.log").open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(f"status={result['run']['status']}\n")
        fh.write(f"mode={result['run']['mode']}\n")
        fh.write(f"counts={json.dumps(result['run']['counts'], ensure_ascii=False)}\n")
        for limit in result["run"]["limits"]:
            fh.write(f"limit: {limit}\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--case", default=None)
    ap.add_argument("--out", required=True)
    ap.add_argument("--mode", choices=["skill", "baseline"], default="skill")
    ap.add_argument("--draft", default=None)
    args = ap.parse_args(argv)

    try:
        result, code = run(args)
    except Exception as exc:  # noqa: BLE001 - a crash must still produce an honest result
        traceback.print_exc()
        result = {
            "case_id": Path(args.corpus).name,
            "graph": {"nodes": [], "edges": []},
            "run": {
                "status": kg.REFUSED,
                "mode": args.mode,
                "refused_reason": f"{type(exc).__name__}: {exc}",
                "limits": ["pipeline crashed before producing a graph"],
            },
            "uncertainty": [{"kind": "internal_error", "description": f"{type(exc).__name__}: {exc}"}],
            "quality_control": {"checks": [], "critical_failures": 1, "warnings": 0},
        }
        code = 5

    write_outputs(result, Path(args.out), None)
    counts = result.get("run", {}).get("counts", {})
    print(json.dumps({"status": result["run"]["status"], "counts": counts, "out": str(args.out)}, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
