#!/usr/bin/env python3
"""Independent replay of a completed result.json. Separate from the builder on purpose.

The builder says the quotes are in the papers. This script goes back to the corpus and
checks that for itself, from the files on disk, without trusting a single recorded
offset, id count, or status word.

    python scripts/validate_result.py --result output/result.json --corpus corpus/ws2-pair
    python scripts/validate_result.py --result output/result.json --corpus ... --case .../case.json

Exit 0 = PASS, 1 = FAIL. A FAIL is a defect in the artifact, not a flaky test.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import kg  # noqa: E402


class Report:
    def __init__(self):
        self.checks: list[dict] = []

    def check(self, code: str, passed: bool, detail: str, critical: bool = True):
        self.checks.append({"code": code, "passed": bool(passed), "detail": detail, "critical": critical})

    @property
    def failures(self) -> list[dict]:
        return [c for c in self.checks if not c["passed"]]

    @property
    def ok(self) -> bool:
        return not any(not c["passed"] and c["critical"] for c in self.checks)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--result", required=True)
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--case", default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    rep = Report()
    result_path = Path(args.result)
    if not result_path.exists():
        print(json.dumps({"verdict": "FAIL", "detail": f"no such file: {result_path}"}))
        return 1

    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
        rep.check("V00-json", True, "result.json parses")
    except json.JSONDecodeError as exc:
        print(json.dumps({"verdict": "FAIL", "detail": f"invalid JSON: {exc}"}))
        return 1

    try:
        case = json.loads(Path(args.case).read_text(encoding="utf-8")) if args.case else {}
    except Exception:  # noqa: BLE001
        case = {}
        rep.check("V00b-case", False, "case.json unreadable", critical=False)

    corpus = None
    try:
        corpus = kg.load_corpus(args.corpus, case)
        rep.check("V01-corpus", True, f"{len(corpus.papers)} papers, {len(corpus.paragraphs)} paragraphs")
    except kg.KgError as exc:
        rep.check("V01-corpus", False, f"corpus unreadable: {exc}")

    # ---- structure -----------------------------------------------------------------
    graph = result.get("graph") or {}
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    rep.check("V02-graph-present", isinstance(nodes, list) and isinstance(edges, list), f"{len(nodes)} nodes, {len(edges)} edges")

    required = ["case_id", "sources", "graph", "queries", "evidence", "run", "uncertainty"]
    missing = [k for k in required if k not in result]
    rep.check("V03-envelope", not missing, f"missing top-level keys: {missing or 'none'}")

    contract = kg.resolve_contract(case) if case else None
    if contract and contract.resolved:
        cc = kg.contract_check(result, contract)
        rep.check("V04-contract", cc["satisfied"], f"contract {cc['source']}: missing {cc['missing'] or 'none'}")
    else:
        rep.check("V04-contract", True, "case.json has no machine-readable contract; envelope checked instead", critical=False)

    # ---- ids -----------------------------------------------------------------------
    ids = [n.get("id") for n in nodes] + [e.get("id") for e in edges]
    rep.check("V05-ids-unique", len(ids) == len(set(ids)) and all(ids), f"{len(ids)} ids, {len(set(ids))} unique")

    node_ids = {n.get("id") for n in nodes}
    dangling = [e.get("id") for e in edges if e.get("subject") not in node_ids or e.get("object") not in node_ids]
    rep.check("V06-edges-resolve", not dangling, f"edges with unknown endpoints: {len(dangling)}")

    # ---- evidence, re-verified from the corpus -------------------------------------
    bad_quotes: list[str] = []
    bad_offsets: list[str] = []
    checked = 0
    if corpus is not None:
        for n in nodes:
            for m in n.get("mentions", []):
                loc = m.get("locator") or {}
                para = corpus.get(f"{loc.get('source_id')}#{loc.get('paragraph_id')}")
                checked += 1
                if para is None:
                    bad_quotes.append(f"{n.get('id')} -> unknown paragraph {loc}")
                    continue
                found = kg.locate_quote(para.text, m.get("quote", ""))
                if not found["found"]:
                    bad_quotes.append(f"{n.get('id')} quote not in {loc}")
                    continue
                if loc.get("char_start") is not None and loc.get("char_end") is not None:
                    if para.text[loc["char_start"] : loc["char_end"]] != loc.get("matched_text", None) and \
                       kg.normalize_ws(para.text[loc["char_start"] : loc["char_end"]]) != kg.normalize_ws(m.get("quote", "")):
                        bad_offsets.append(f"{n.get('id')} offsets disagree with the recorded slice")
        for e in edges:
            ev = (e.get("evidence") or {})
            loc = ev.get("locator") or {}
            para = corpus.get(f"{loc.get('source_id')}#{loc.get('paragraph_id')}")
            checked += 1
            if para is None:
                bad_quotes.append(f"{e.get('id')} -> unknown paragraph {loc}")
                continue
            found = kg.locate_quote(para.text, ev.get("quote", ""))
            if not found["found"]:
                bad_quotes.append(f"{e.get('id')} quote not in {loc}")
                continue
            if loc.get("char_start") is not None and loc.get("char_end") is not None:
                if kg.normalize_ws(para.text[loc["char_start"] : loc["char_end"]]) != kg.normalize_ws(ev.get("quote", "")):
                    bad_offsets.append(f"{e.get('id')} offsets disagree with the recorded slice")
        rep.check("V07-quotes-verbatim", not bad_quotes, f"{checked} spans re-checked; failures: {len(bad_quotes)} {bad_quotes[:3]}")
        rep.check("V08-offsets-consistent", not bad_offsets, f"offset mismatches: {len(bad_offsets)} {bad_offsets[:3]}")

    # ---- no forbidden page claims ---------------------------------------------------
    pageish = []
    for n in nodes:
        for m in n.get("mentions", []):
            if "page" in (m.get("locator") or {}) or "page_number" in m:
                pageish.append(n.get("id"))
    for e in edges:
        loc = (e.get("evidence") or {}).get("locator") or {}
        if "page" in loc or "page_number" in loc:
            pageish.append(e.get("id"))
    rep.check("V09-no-page-claims", not pageish, f"records carrying a page field: {len(pageish)}")

    # ---- counts and status ----------------------------------------------------------
    stats = graph.get("stats") or {}
    rep.check(
        "V10-counts",
        stats.get("nodes") == len(nodes) and stats.get("edges") == len(edges),
        f"stats says {stats.get('nodes')}/{stats.get('edges')}, body has {len(nodes)}/{len(edges)}",
    )

    run = result.get("run") or {}
    status = run.get("status")
    rep.check("V11-status-enum", status in kg.STATUS_EXIT, f"status={status}")
    evidence = result.get("evidence") or {}
    rejected = len(evidence.get("rejected") or [])
    unverified_edges = len([e for e in edges if ((e.get("evidence") or {}).get("locator") or {}).get("match_kind") in (None, "unverified")])
    if status == "success":
        rep.check(
            "V12-success-means-verified",
            rejected == 0 and unverified_edges == 0,
            f"success declared with {rejected} rejected quotes and {unverified_edges} unverified edges",
        )
    else:
        rep.check("V12-success-means-verified", True, f"status={status} carries declared gaps", critical=False)

    claimed = (evidence.get("counts") or {}).get("rejected")
    rep.check("V13-counts-honest", claimed == rejected, f"evidence.counts.rejected={claimed} vs actual {rejected}")

    # ---- queries actually answered ---------------------------------------------------
    queries = result.get("queries") or []
    rep.check("V14-queries", len(queries) >= 3, f"{len(queries)} queries (task requires >= 3)")
    unexecuted = [q.get("id") for q in queries if not q.get("executed")]
    rep.check("V15-queries-executed", not unexecuted, f"unexecuted: {unexecuted or 'none'}")
    bad_q: list[str] = []
    for q in queries:
        qid = q.get("id")
        if "answers" not in q or "evidence" not in q:
            bad_q.append(f"{qid}:missing answers/evidence key")
            continue
        if q.get("answer_count") != len(q.get("answers") or []):
            bad_q.append(f"{qid}:answer_count disagrees with answers")
            continue
        # a query may legitimately return nothing, but then it must say why that is not proof
        if not q.get("answers") and not q.get("undetermined"):
            bad_q.append(f"{qid}:empty answer with no undetermined explanation")
    rep.check("V16-queries-evidenced", not bad_q, f"{len(queries) - len(bad_q)}/{len(queries)} queries answer honestly; problems: {bad_q or 'none'}")

    # ---- provenance ------------------------------------------------------------------
    bad_prov = [
        s.get("source_id")
        for s in (result.get("sources") or [])
        if not s.get("doi") or not s.get("title") or not s.get("licence")
    ]
    rep.check("V17-provenance", not bad_prov, f"sources missing doi/title/licence: {bad_prov or 'none'}", critical=False)

    report = {
        "verdict": "PASS" if rep.ok else "FAIL",
        "checks": rep.checks,
        "failures": rep.failures,
        "counts": {"nodes": len(nodes), "edges": len(edges), "queries": len(queries), "spans_rechecked": checked},
    }
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for c in rep.checks:
            mark = "PASS" if c["passed"] else "FAIL"
            print(f"[{mark}] {c['code']:<28} {c['detail']}")
        print()
        print(f"verdict: {report['verdict']}  ({len(rep.failures)} failing checks)")
    return 0 if rep.ok else 1


if __name__ == "__main__":
    sys.exit(main())
