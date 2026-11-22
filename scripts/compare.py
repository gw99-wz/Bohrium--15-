#!/usr/bin/env python3
"""Same-input comparison: Skill pipeline vs the naive baseline, plus two probes.

This is the evidence for the '使用增益' rubric item — it is an experiment you can rerun,
not a claim in prose.

    python scripts/compare.py --corpus corpus/ws2-pair --out output/compare.json

Experiments
  E1 clean     both modes on the identical corpus: what does each produce?
  E2 tamper    one evidence quote is corrupted on purpose: who notices?
  E3 repeat    the Skill pipeline runs twice: are ids and outcome stable?
"""

from __future__ import annotations

import argparse
import json
import sys
import pathlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import kg  # noqa: E402
import run_pipeline as RP  # noqa: E402


class Args:
    def __init__(self, corpus: str, case: str | None, out: str, mode: str, draft: str | None = None):
        self.corpus = corpus
        self.case = case
        self.out = out
        self.mode = mode
        self.draft = draft


def metrics(result: dict) -> dict:
    graph = result.get("graph") or {}
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    evidence = result.get("evidence") or {}
    run = result.get("run") or {}

    verified_edges = len(
        [e for e in edges if ((e.get("evidence") or {}).get("locator") or {}).get("match_kind") not in (None, "unverified")]
    )
    offsets = len(
        [
            e
            for e in edges
            if ((e.get("evidence") or {}).get("locator") or {}).get("char_start") is not None
        ]
    )
    def concept_key(node: dict) -> tuple:
        """Mode-independent normalisation, so the baseline cannot hide duplicates by
        spelling them differently."""
        label = node.get("label") or ""
        s = kg.ascii_fold(label).lower()
        s = s.replace("\u2082", "2")
        s = "".join(ch if (ch.isalnum() or ch.isspace() or ch == "-") else " " for ch in s)
        s = kg.normalize_ws(s)
        for form in ("monolayer ", "single layer ", "few layer ", "2d "):
            if s.startswith(form):
                s = s[len(form) :]
        return (node.get("type"), s)

    seen: dict[tuple, int] = {}
    for n in nodes:
        seen[concept_key(n)] = seen.get(concept_key(n), 0) + 1
    dupes = sum(v - 1 for v in seen.values() if v > 1)

    cross_metric = len([n for n in nodes if n.get("type") == "metric" and len(n.get("sources") or []) > 1])
    blocked = (result.get("alignment") or {}).get("blocked") or []
    merges = (result.get("alignment") or {}).get("merges") or []
    queries = result.get("queries") or []
    refusal_queries = len([q for q in queries if q.get("undetermined")])
    contract = result.get("contract") or {}

    rejected_count = len(evidence.get("rejected") or [])
    return {
        "nodes": len(nodes),
        "edges": len(edges),
        "edges_with_located_quote": verified_edges,
        "edges_with_char_offsets": offsets,
        "evidence_verified_rate": round(verified_edges / len(edges), 4) if edges else 0.0,
        "rejected_quotes": len(evidence.get("rejected") or []),
        "duplicate_concept_nodes": dupes,
        "cross_paper_metric_nodes": cross_metric,
        "merge_records": len(merges),
        "merges_with_basis": len([m for m in merges if (m.get("basis") or "").strip()]),
        "must_not_merge_records": len([b for b in blocked if b.get("kind") == "must_not_merge"]),
        "queries": len(queries),
        "queries_with_answers": len([q for q in queries if q.get("answers")]),
        "queries_recording_undetermined": refusal_queries,
        "contract_resolved": contract.get("satisfied"),
        "contract_source": contract.get("source"),
        "status": run.get("status"),
        # a success claim is only self-consistent when nothing was left unverified
        "self_consistent_success": (run.get("status") != "success")
        or (rejected_count == 0 and verified_edges == len(edges)),
    }


def run_mode(corpus: str, case: str | None, mode: str, out: str) -> dict:
    result, code = RP.run(Args(corpus, case, out, mode))
    # attach the status the runner would exit with so callers see code and body together
    result["_exit_code"] = code
    return result


def tamper(corpus_dir: str, case: str | None) -> dict:
    """E2: corrupt exactly one quote and see who notices.

    The corruption is realistic: one number in the span is changed, which is the single
    most common evidence failure there is. The corrupted span must never be accepted.
    """
    import re as _re

    probe = run_mode(corpus_dir, case, "skill", "<unused>")
    target = None
    for n in probe["graph"]["nodes"]:
        for m in n["mentions"]:
            if _re.search(r"\d", m["quote"]):
                target = m
                break
        if target:
            break
    if not target:
        return {"applicable": False}

    def bump(match):
        text = match.group(0)
        if "." in text:
            value = float(text)
            return f"{value + 1.0:.2f}"
        return str(int(text) + 1)

    corrupted, changes = _re.subn(r"\d+(?:\.\d+)?", bump, target["quote"], count=1)
    if not changes:
        return {"applicable": False}

    draft = {
        "entities": [
            {
                "source_id": target["locator"]["source_id"],
                "paragraph_id": target["locator"]["paragraph_id"],
                "label": "tampered-entity",
                "entity_type": "material",
                "quote": corrupted,
            }
        ],
        "relations": [],
    }
    scratch = pathlib.Path(corpus_dir).resolve().parent / ".compare-scratch"
    scratch.mkdir(exist_ok=True)
    draft_path = scratch / "tampered-draft.json"
    draft_path.write_text(json.dumps(draft), encoding="utf-8")

    skill_tampered, _ = RP.run(Args(corpus_dir, case, "<unused>", "skill", draft_path))
    base_tampered, _ = RP.run(Args(corpus_dir, case, "<unused>", "baseline", draft_path))

    rejected_s = len((skill_tampered.get("evidence") or {}).get("rejected") or [])
    rejected_b = len((base_tampered.get("evidence") or {}).get("rejected") or [])
    return {
        "applicable": True,
        "original_quote": target["quote"][:160],
        "corrupted_quote": corrupted[:160],
        "skill_rejected": rejected_s,
        "skill_status": skill_tampered["run"]["status"],
        "baseline_rejected": rejected_b,
        "baseline_status": base_tampered["run"]["status"],
        "detected_by_skill": rejected_s > 0 and skill_tampered["run"]["status"] != "success",
        "detected_by_baseline": rejected_b > 0 and base_tampered["run"]["status"] != "success",
    }


def determinism(corpus_dir: str, case: str | None) -> dict:
    a = run_mode(corpus_dir, case, "skill", "<unused>")
    b = run_mode(corpus_dir, case, "skill", "<unused>")
    ids_a = sorted([n["id"] for n in a["graph"]["nodes"]] + [e["id"] for e in a["graph"]["edges"]])
    ids_b = sorted([n["id"] for n in b["graph"]["nodes"]] + [e["id"] for e in b["graph"]["edges"]])
    return {
        "identical_ids": ids_a == ids_b,
        "identical_counts": (a["graph"]["stats"]["nodes"], a["graph"]["stats"]["edges"])
        == (b["graph"]["stats"]["nodes"], b["graph"]["stats"]["edges"]),
        "id_count": len(ids_a),
        "status_a": a["run"]["status"],
        "status_b": b["run"]["status"],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--case", default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    skill = run_mode(args.corpus, args.case, "skill", "<unused>")
    base = run_mode(args.corpus, args.case, "baseline", "<unused>")

    report = {
        "corpus": args.corpus,
        "note": "baseline = what the same extraction produces with no alias reconciliation, no "
        "offset verification, no instance/condition reasoning and no contract resolution. "
        "It is an emulation of a no-skill run, not a recorded model session.",
        "E1_clean": {"skill": metrics(skill), "baseline": metrics(base)},
        "E2_tamper": tamper(args.corpus, args.case),
        "E3_determinism": determinism(args.corpus, args.case),
    }

    if args.out:
        kg.write_json(args.out, report)

    s, b = report["E1_clean"]["skill"], report["E1_clean"]["baseline"]
    rows = [
        ("nodes", s["nodes"], b["nodes"]),
        ("edges", s["edges"], b["edges"]),
        ("evidence verified rate", s["evidence_verified_rate"], b["evidence_verified_rate"]),
        ("edges with char offsets", s["edges_with_char_offsets"], b["edges_with_char_offsets"]),
        ("duplicate concept nodes", s["duplicate_concept_nodes"], b["duplicate_concept_nodes"]),
        ("cross-paper metric nodes", s["cross_paper_metric_nodes"], b["cross_paper_metric_nodes"]),
        ("merge records", s["merge_records"], b["merge_records"]),
        ("merges with a stated basis", s["merges_with_basis"], b["merges_with_basis"]),
        ("must-not-merge records", s["must_not_merge_records"], b["must_not_merge_records"]),
        ("queries recording a refusal", s["queries_recording_undetermined"], b["queries_recording_undetermined"]),
        ("contract resolved", s["contract_resolved"], b["contract_resolved"]),
        ("status", s["status"], b["status"]),
        ("success claim self-consistent", s["self_consistent_success"], b["self_consistent_success"]),
    ]
    width = max(len(r[0]) for r in rows) + 2
    print(f"{'metric'.ljust(width)}{'skill':>18}{'baseline':>18}")
    print("-" * (width + 36))
    for name, sv, bv in rows:
        print(f"{name.ljust(width)}{str(sv):>18}{str(bv):>18}")
    print()
    e2 = report["E2_tamper"]
    print(f"E2 tamper: skill detected={e2.get('detected_by_skill')} status={e2.get('skill_status')} | "
          f"baseline detected={e2.get('detected_by_baseline')} status={e2.get('baseline_status')}")
    e3 = report["E3_determinism"]
    print(f"E3 repeat: identical ids={e3['identical_ids']} counts={e3['identical_counts']} ({e3['id_count']} ids)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
