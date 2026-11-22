#!/usr/bin/env python3
"""Core library for the evidence-bound literature knowledge graph Skill.

Standard library only — the evaluation image is not guaranteed to carry jsonschema,
networkx, pandas, or anything else, so nothing here may import a third-party package.

Layers
------
text        quote location with a whitespace-tolerant fallback, sentence splitting
corpus      tolerant loader for corpus-manifest / material / primary/secondary material
contract    dynamic resolution of the case's `output_contract` into field paths
ontology    controlled entity types and relation signatures (loads references/*.json)
ids         content-derived, rerun-stable identifiers
status      success / partial / uncertain / refused, derived from checks only
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

# --------------------------------------------------------------------------------------
# errors
# --------------------------------------------------------------------------------------


class KgError(Exception):
    """Fatal, user-visible failure. The CLI turns this into status=refused."""


# --------------------------------------------------------------------------------------
# text
# --------------------------------------------------------------------------------------

_WS = re.compile(r"\s+")
_SENT_BREAK = re.compile(r"(?<=[.!?])[\"')\]]?\s+(?=[A-Z0-9(\"'])")


def normalize_ws(s: str) -> str:
    return _WS.sub(" ", s).strip()


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _fold_quotes(s: str) -> str:
    """Fold typographic quotes/dashes so a quote copied from rendered text still matches."""
    table = {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2212": "-",
        "\u00a0": " ",
    }
    return "".join(table.get(ch, ch) for ch in s)


def _squash(s: str) -> tuple[str, list[int]]:
    """Collapse all whitespace, remembering the original index of every kept character."""
    out: list[str] = []
    idx: list[int] = []
    pending = False
    for i, ch in enumerate(s):
        if ch.isspace():
            pending = True
            continue
        if pending and out:
            out.append(" ")
            idx.append(i)
        pending = False
        out.append(ch)
        idx.append(i)
    return "".join(out), idx


def locate_quote(paragraph_text: str, quote: str) -> dict:
    """Locate `quote` inside `paragraph_text` and report exactly how it was found.

    Three passes, each weaker than the last, each reported honestly:
      exact   — byte-for-byte substring
      folded  — typographic quotes/dashes folded to ASCII
      squashed— whitespace collapsed (line wraps, double spaces, NBSP)

    A miss is a miss. This function never returns a guessed offset.
    """
    q = quote.strip()
    if not q:
        return {"found": False, "match_kind": "empty", "char_start": None, "char_end": None}
    if not paragraph_text:
        return {"found": False, "match_kind": "empty_paragraph", "char_start": None, "char_end": None}

    pos = paragraph_text.find(q)
    if pos >= 0:
        return {
            "found": True,
            "match_kind": "exact",
            "char_start": pos,
            "char_end": pos + len(q),
            "matched_text": paragraph_text[pos : pos + len(q)],
        }

    folded_text, folded_index = _fold_quotes(paragraph_text), None
    folded_q = _fold_quotes(q)
    pos = folded_text.find(folded_q)
    if pos >= 0:
        # folding is character-for-character (no length change), so offsets are directly reusable
        return {
            "found": True,
            "match_kind": "folded",
            "char_start": pos,
            "char_end": pos + len(folded_q),
            "matched_text": paragraph_text[pos : pos + len(folded_q)],
        }

    sq_text, sq_idx = _squash(paragraph_text)
    sq_q, _ = _squash(q)
    pos = sq_text.find(sq_q)
    if pos >= 0:
        start = sq_idx[pos]
        end = sq_idx[pos + len(sq_q) - 1] + 1
        return {
            "found": True,
            "match_kind": "squashed",
            "char_start": start,
            "char_end": end,
            "matched_text": paragraph_text[start:end],
        }

    return {"found": False, "match_kind": "not_found", "char_start": None, "char_end": None}


_ABBREV = re.compile(r"(?:\b(?:et al|e\.g|i\.e|Fig|Figs|Eq|Eqs|Ref|Refs|vs|cf|approx|ca|No|Dr|Prof|Sect|Tab)\.)$")


def split_sentences(text: str) -> list[tuple[int, int]]:
    """Conservative sentence spans (start, end) as Unicode offsets into `text`."""
    spans: list[tuple[int, int]] = []
    start = 0
    for m in _SENT_BREAK.finditer(text):
        candidate = text[start : m.start()].strip()
        head = text[start : m.start()]
        if not candidate:
            start = m.end()
            continue
        if _ABBREV.search(head.strip()):
            continue
        end = start + len(head.rstrip())
        if end > start:
            spans.append((start, end))
        start = m.end()
    tail = text[start:].strip()
    if tail:
        end = start + len(text[start:].rstrip())
        if end > start:
            spans.append((start, end))
    return spans


def ascii_fold(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")


# --------------------------------------------------------------------------------------
# ids — content derived, so a rerun over identical input mints identical ids
# --------------------------------------------------------------------------------------


def mint_id(prefix: str, *parts: Any) -> str:
    payload = "\u001f".join(str(p) for p in parts)
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}:{digest}"


# --------------------------------------------------------------------------------------
# corpus
# --------------------------------------------------------------------------------------

PARAGRAPH_ID_KEYS = ("paragraph_id", "paragraphId", "pid", "para_id", "id")
PARAGRAPH_TEXT_KEYS = ("text", "content", "body", "paragraph", "value")
SECTION_KEYS = ("section", "section_title", "sectionTitle", "heading", "sec")


@dataclass
class Paragraph:
    source_id: str
    paragraph_id: str
    text: str
    section: str = ""
    kind: str = "p"
    label: str = ""

    @property
    def locator(self) -> str:
        """Human citation form. NEVER a PDF page number — the corpus supplies JATS ids only."""
        return f"{self.source_id}#{self.paragraph_id}"


@dataclass
class Paper:
    source_id: str
    role: str = "unknown"
    doi: str = ""
    title: str = ""
    authors: list[str] = field(default_factory=list)
    licence: str = ""
    origin: str = ""
    sha256: str = ""
    paragraphs: list[Paragraph] = field(default_factory=list)


@dataclass
class Corpus:
    papers: list[Paper]
    manifest: dict
    case: dict
    warnings: list[dict]

    @property
    def sources(self) -> dict[str, Paper]:
        return {p.source_id: p for p in self.papers}

    @property
    def paragraphs(self) -> dict[str, Paragraph]:
        out: dict[str, Paragraph] = {}
        for p in self.papers:
            for para in p.paragraphs:
                out[f"{p.source_id}#{para.paragraph_id}"] = para
        return out

    def get(self, locator: str) -> Paragraph | None:
        return self.paragraphs.get(locator)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as exc:
        raise KgError(f"{path.name}: not valid JSON ({exc})") from exc


def _first_key(obj: dict, keys: Iterable[str]) -> Any:
    for k in keys:
        if k in obj and obj[k] not in (None, ""):
            return obj[k]
    return None


def _coerce_paragraphs(source_id: str, raw: Any) -> list[Paragraph]:
    """Accept a list of paragraph dicts, or a dict paragraph_id -> text/dict."""
    out: list[Paragraph] = []
    if isinstance(raw, dict):
        items = []
        for pid, val in raw.items():
            if isinstance(val, dict):
                d = dict(val)
                d.setdefault("paragraph_id", pid)
                items.append(d)
            elif isinstance(val, str):
                items.append({"paragraph_id": pid, "text": val})
        raw = items
    if not isinstance(raw, list):
        raise KgError(f"{source_id}: paragraphs must be a list or an id->text mapping")
    for i, item in enumerate(raw):
        if isinstance(item, str):
            item = {"paragraph_id": f"p{i + 1:03d}", "text": item}
        if not isinstance(item, dict):
            raise KgError(f"{source_id}: paragraph #{i + 1} is not an object")
        pid = _first_key(item, PARAGRAPH_ID_KEYS) or f"p{i + 1:03d}"
        text = _first_key(item, PARAGRAPH_TEXT_KEYS)
        if not isinstance(text, str) or not text.strip():
            raise KgError(f"{source_id}#{pid}: paragraph has no text")
        out.append(
            Paragraph(
                source_id=source_id,
                paragraph_id=str(pid),
                text=text,
                section=str(_first_key(item, SECTION_KEYS) or ""),
                kind=str(item.get("kind") or "p"),
                label=str(item.get("label") or ""),
            )
        )
    return out


def _paper_from_dict(d: dict, default_role: str = "unknown") -> Paper:
    source_id = str(
        _first_key(d, ("source_id", "sourceId", "paper_id", "paperId", "id"))
        or (d.get("pmcid") or "")
        or ""
    )
    if not source_id:
        raise KgError("a paper entry has no source_id")
    return Paper(
        source_id=source_id,
        role=str(d.get("role") or default_role),
        doi=str(d.get("doi") or ""),
        title=str(d.get("title") or ""),
        authors=[str(a) for a in (d.get("authors") or [])],
        licence=str(d.get("licence") or d.get("license") or ""),
        origin=str(d.get("origin") or ""),
        sha256=str(d.get("sha256") or d.get("sha256_of_xml") or ""),
        paragraphs=_coerce_paragraphs(source_id, d.get("paragraphs") or d.get("paras") or []),
    )


def load_corpus(corpus_dir: str | Path, case: dict | None = None) -> Corpus:
    """Load the corpus bundle tolerantly, recording every adaptation as a warning.

    Recognised layouts (first that yields papers wins):
      1. material.json  {"papers": [...]} / {"corpus": [...]} / {"documents": [...]}
      2. material.json  as a bare list of paper objects
      3. material.json  as {source_id: {paragraphs: [...]}}
      4. material.json  as a flat list of paragraph dicts carrying their own source_id
      5. primary-material.json + secondary-material.json
    """
    root = Path(corpus_dir)
    if not root.is_dir():
        raise KgError(f"corpus directory not found: {root}")

    warnings: list[dict] = []
    manifest = _read_json(root / "corpus-manifest.json") or {}
    if not manifest:
        warnings.append(
            {
                "kind": "missing_manifest",
                "detail": "corpus-manifest.json absent: DOI/licence/hash provenance is incomplete",
                "impact": "sources[] will carry empty provenance fields",
            }
        )

    papers: list[Paper] = []
    material = _read_json(root / "material.json")

    def _adopt(p: Paper) -> None:
        papers.append(p)

    if isinstance(material, dict):
        for key in ("papers", "documents", "materials", "corpus", "items"):
            if isinstance(material.get(key), list):
                for d in material[key]:
                    if isinstance(d, dict):
                        _adopt(_paper_from_dict(d))
                    else:
                        raise KgError(f"material.json: {key}[] entry is not an object")
                break
        if not papers:
            mapping = {k: v for k, v in material.items() if isinstance(v, (dict, list))}
            for sid, val in mapping.items():
                if isinstance(val, dict):
                    d = dict(val)
                    d.setdefault("source_id", sid)
                    _adopt(_paper_from_dict(d))
                else:
                    _adopt(Paper(source_id=sid, paragraphs=_coerce_paragraphs(sid, val)))
            if papers:
                warnings.append(
                    {
                        "kind": "adapted_layout",
                        "detail": "material.json read as a source_id -> paper mapping",
                        "impact": "no role/doi enrichment for those papers",
                    }
                )
    elif isinstance(material, list):
        if material and isinstance(material[0], dict) and any(k in material[0] for k in PARAGRAPH_ID_KEYS):
            grouped: dict[str, list[dict]] = {}
            for item in material:
                sid = str(_first_key(item, ("source_id", "sourceId", "paper_id", "paperId")) or "")
                if not sid:
                    raise KgError("material.json is a flat paragraph list but a paragraph has no source_id")
                grouped.setdefault(sid, []).append(item)
            for sid, items in grouped.items():
                _adopt(Paper(source_id=sid, paragraphs=_coerce_paragraphs(sid, items)))
            warnings.append(
                {
                    "kind": "adapted_layout",
                    "detail": "material.json read as a flat paragraph list grouped by source_id",
                    "impact": "section/kind fields may be absent",
                }
            )
        else:
            for d in material:
                _adopt(_paper_from_dict(d))

    if not papers:
        for name, role in (("primary-material.json", "primary"), ("secondary-material.json", "secondary")):
            d = _read_json(root / name)
            if d is None:
                continue
            if isinstance(d, dict) and isinstance(d.get("papers"), list):
                for pd in d["papers"]:
                    _adopt(_paper_from_dict(pd, default_role=role))
            elif isinstance(d, dict):
                _adopt(_paper_from_dict(d, default_role=role))
            elif isinstance(d, list):
                _adopt(Paper(source_id=Path(name).stem, role=role, paragraphs=_coerce_paragraphs(name, d)))
        if papers:
            warnings.append(
                {
                    "kind": "material_index_missing",
                    "detail": "material.json absent: rebuilt the index from primary/secondary material files",
                    "impact": "paragraph order follows file order",
                }
            )

    if not papers:
        raise KgError(
            "no papers found: expected material.json (or primary-material.json + secondary-material.json)"
        )

    if len(papers) < 2:
        warnings.append(
            {
                "kind": "single_source",
                "detail": f"only {len(papers)} paper(s) in the corpus",
                "impact": "cross-paper alignment and cross-paper queries are not answerable",
                "human_takeover": "confirm the corpus bundle is complete before trusting cross-paper claims",
            }
        )

    # de-duplicate source ids
    seen: dict[str, int] = {}
    for p in papers:
        if p.source_id in seen:
            seen[p.source_id] += 1
            p.source_id = f"{p.source_id}-{seen[p.source_id]}"
            warnings.append(
                {
                    "kind": "duplicate_source_id",
                    "detail": f"two papers shared a source_id; second renamed to {p.source_id}",
                    "impact": "evidence locators for the renamed paper use the suffixed id",
                }
            )
        else:
            seen[p.source_id] = 1

    case = case or {}
    corpus = Corpus(papers=papers, manifest=manifest, case=case, warnings=warnings)

    # provenance cross-check against the manifest
    by_id = {p.get("source_id"): p for p in manifest.get("papers", []) if isinstance(p, dict)}
    for p in papers:
        m = by_id.get(p.source_id)
        if not m:
            continue
        for attr, key in (("doi", "doi"), ("title", "title"), ("licence", "licence")):
            if not getattr(p, attr) and m.get(key):
                setattr(p, attr, str(m[key]))
        if not p.sha256 and m.get("sha256"):
            p.sha256 = str(m["sha256"])
    return corpus


# --------------------------------------------------------------------------------------
# output contract — resolved at run time, never hardcoded
# --------------------------------------------------------------------------------------

CONTRACT_KEYS = (
    "output_contract",
    "outputContract",
    "required_outputs",
    "requiredOutputs",
    "expected_outputs",
    "expectedOutputs",
    "required_fields",
    "requiredFields",
    "output_fields",
    "outputFields",
    "outputs",
    "contract",
)

CASE_ID_KEYS = ("case_id", "caseId", "id", "task_id", "taskId", "name", "slug")

# Used only when the case carries no machine-readable contract. Declared in the result as
# contract_source="fallback" so nobody can mistake it for the platform's real requirement.
FALLBACK_CONTRACT = [
    "case_id",
    "sources",
    "graph",
    "queries",
    "evidence",
    "run",
    "uncertainty",
    "quality_control",
]


@dataclass
class Contract:
    fields: list[str]
    source: str  # "case.json:<path>" or "fallback"
    raw: Any = None

    @property
    def resolved(self) -> bool:
        return self.source != "fallback"


def _collect_field_names(value: Any, depth: int = 0) -> list[str]:
    """Pull field paths out of whatever shape the contract uses."""
    if depth > 4 or value is None:
        return []
    if isinstance(value, str):
        return [value] if re.fullmatch(r"[A-Za-z_][\w.\-]*", value.strip()) and " " not in value else []
    if isinstance(value, dict):
        # a mapping of field -> description/schema is the most common form
        child = None
        for k in ("fields", "required", "properties", "items", "names", "keys"):
            if k in value and isinstance(value[k], (list, dict)):
                child = value[k]
                break
        if child is not None:
            return _collect_field_names(child, depth + 1)
        names: list[str] = []
        for k, v in value.items():
            if isinstance(v, (dict, list)):
                names.extend(f"{k}.{sub}" for sub in _collect_field_names(v, depth + 1))
            else:
                names.append(str(k))
        return names
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            if isinstance(item, dict):
                named = _first_key(item, ("field", "name", "key", "path", "id"))
                if isinstance(named, str) and named.strip():
                    out.append(named.strip())
                    continue
            out.extend(_collect_field_names(item, depth + 1))
        return out
    return []


def resolve_contract(case: dict) -> Contract:
    if not isinstance(case, dict) or not case:
        return Contract(fields=list(FALLBACK_CONTRACT), source="fallback")

    for key in CONTRACT_KEYS:
        if key not in case:
            continue
        fields = _collect_field_names(case[key])
        # drop obvious non-fields and de-duplicate, keeping order
        seen: set[str] = set()
        clean: list[str] = []
        for f in fields:
            f = f.strip()
            if not f or f.startswith("_") or f in seen or len(f) > 80:
                continue
            seen.add(f)
            clean.append(f)
        if clean:
            return Contract(fields=clean, source=f"case.json:{key}", raw=case[key])

    # a contract may be nested deeper, e.g. under `task`
    for wrapper in ("task", "spec", "requirements", "meta"):
        sub = case.get(wrapper)
        if isinstance(sub, dict):
            for key in CONTRACT_KEYS:
                if key in sub:
                    fields = _collect_field_names(sub[key])
                    if fields:
                        return Contract(fields=fields, source=f"case.json:{wrapper}.{key}", raw=sub[key])

    return Contract(fields=list(FALLBACK_CONTRACT), source="fallback")


def resolve_case_id(case: dict, fallback: str = "unknown-case") -> str:
    if isinstance(case, dict):
        for key in CASE_ID_KEYS:
            v = case.get(key)
            if isinstance(v, (str, int)) and str(v).strip():
                return str(v)
    return fallback


def get_path(result: dict, path: str) -> tuple[bool, Any]:
    """Resolve a dotted field path against the result document."""
    cur: Any = result
    for part in path.split("."):
        if isinstance(cur, dict):
            if part in cur:
                cur = cur[part]
                continue
            return False, None
        if isinstance(cur, list):
            return True if False else (False, None)
        return False, None
    return True, cur


def contract_check(result: dict, contract: Contract) -> dict:
    found, missing = [], []
    for f in contract.fields:
        ok, _ = get_path(result, f)
        (found if ok else missing).append(f)
    return {
        "source": contract.source,
        "required": list(contract.fields),
        "found": found,
        "missing": missing,
        "satisfied": not missing,
    }


# --------------------------------------------------------------------------------------
# ontology
# --------------------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
REFERENCES = ROOT / "references"


def load_ontology() -> dict:
    types_path = REFERENCES / "entity-types.json"
    rels_path = REFERENCES / "relations.json"
    if not types_path.exists() or not rels_path.exists():
        raise KgError(
            f"ontology files missing under {REFERENCES}: expected entity-types.json and relations.json"
        )
    types = json.loads(types_path.read_text(encoding="utf-8"))
    rels = json.loads(rels_path.read_text(encoding="utf-8"))
    return {"types": types, "relations": rels}


def relation_signature(ontology: dict, rel_type: str) -> dict | None:
    for r in ontology["relations"]["relations"]:
        if r["type"] == rel_type:
            return r
    return None


# --------------------------------------------------------------------------------------
# status
# --------------------------------------------------------------------------------------

SUCCESS = "success"
PARTIAL = "partial"
UNCERTAIN = "uncertain"
REFUSED = "refused"

STATUS_EXIT = {SUCCESS: 0, PARTIAL: 1, UNCERTAIN: 4, REFUSED: 5}


def derive_status(checks: dict) -> str:
    """Status is a *consequence* of the checks. It is never passed in by hand."""
    if checks.get("refused_reason"):
        return REFUSED
    if not checks.get("has_content"):
        return UNCERTAIN if checks.get("readable") else REFUSED
    if checks.get("unverified_evidence", 0) > 0 or checks.get("contract_missing"):
        return PARTIAL
    if checks.get("critical_failures", 0) > 0:
        return PARTIAL
    return SUCCESS


def write_json(path: str | Path, obj: Any) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return p
