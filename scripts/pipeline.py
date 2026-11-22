#!/usr/bin/env python3
"""Graph construction library: extract -> bind -> align -> build -> query -> QC.

The division of labour is the whole point of the Skill:

  * the AGENT decides what the entities and relations are (semantic judgement);
  * these functions decide whether the decision survives contact with the text
    (verbatim quote present, offsets real, relation signature legal, aliases justified).

`mode="baseline"` is the deliberately naive reference implementation used only for the
same-input comparison report. It exists so the skill-vs-baseline delta is reproducible
rather than asserted. It is never the submission's answer path.
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field
from typing import Any

import kg

# --------------------------------------------------------------------------------------
# small lexical resources
# --------------------------------------------------------------------------------------

METHOD_SUFFIXES = (
    "spectroscopy",
    "spectrometry",
    "microscopy",
    "diffraction",
    "scattering",
    "tomography",
    "ellipsometry",
    "calorimetry",
    "chromatography",
    "electrophoresis",
    "calculation",
    "calculations",
    "simulation",
    "simulations",
    "treatment",
    "annealing",
    "functional",
    "transformation",
)

METHOD_ACRONYMS = {
    "DFT",
    "CVD",
    "MOCVD",
    "PVD",
    "PL",
    "TRPL",
    "Raman",
    "XPS",
    "UPS",
    "XRD",
    "TEM",
    "STEM",
    "SEM",
    "AFM",
    "STM",
    "EELS",
    "EDS",
    "ARPES",
    "IVS",
    "TA",
    "SHG",
    "NMR",
    "FTIR",
    "MBE",
    "ALD",
    "TMD",
    "TMDs",
}

TASK_LEXICON = {
    "defect engineering",
    "exciton trapping",
    "charge transport",
    "band gap engineering",
    "bandgap engineering",
    "valleytronics",
    "quantum emission",
    "single-photon emission",
    "sensing",
    "photocatalysis",
    "hydrogen evolution",
}

CONCLUSION_CUES = (
    "we show",
    "we find",
    "we demonstrate",
    "we conclude",
    "suggest",
    "suggests",
    "indicat",
    "demonstrat",
    "evidence for",
    "evidence that",
    "consistent with",
    "reveals",
    "implies",
    "attribute",
    "attribut",
    "confirms",
)

REL_CUES = {
    "uses": ("use", "using", "used", "employ", "via", "by means of", "with"),
    "compares": ("compared with", "compared to", "versus", "vs.", "relative to", "in contrast to"),
    "outperforms": ("better", "higher", "lower", "improved", "enhanced", "outperform", "superior", "larger", "stronger"),
    "supports": ("consistent with", "supports", "support", "confirms", "confirm", "in agreement with", "evidence for"),
    "contradicts": ("contradict", "inconsistent with", "disagree", "conflict", "unlike"),
    "characterizes": ("characteri", "probe", "recorded on", "taken on", "measured on"),
}

FORMULA_RE = re.compile(r"\b(?:[A-Z][a-z]?)(?:\d{1,3})?(?:[A-Z][a-z]?(?:\d{1,3})?){1,3}\b")
# Lazy word run: stop at the first quantity-style connector, so
# "the emission energy of the neutral exciton is 2.03 eV" names "emission energy"
# rather than the noun phrase that happens to sit next to the number.
METRIC_RE = re.compile(
    r"\b(?P<name>[A-Za-z][A-Za-z\-]{2,30}(?:\s+[A-Za-z][A-Za-z\-]{2,20}){0,3}?)\s+"
    r"(?:of|is|was|at|around|about|≈|~|=)\s*"
    r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>eV|meV|nm|cm-1|cm−1|ps|fs|ns|K|%|mV|THz|GHz)\b"
)
ABBR_DEF_RE = re.compile(r"([A-Za-z][A-Za-z\-]{2,40}(?:\s+[A-Za-z][A-Za-z\-]{2,20}){0,4})\s*\(([A-Z][A-Za-z0-9\-]{1,10})s?\)")
ABBR_DEF_RE2 = re.compile(r"\b([A-Z][A-Za-z0-9\-]{1,10})s?\s*\(([a-z][a-z\-]{3,40}(?:\s+[a-z\-]{3,20}){0,4})\)")

STOP_LABELS = {"the", "this", "these", "that", "those", "we", "our", "their", "it"}


# --------------------------------------------------------------------------------------
# canonicalisation and alias resolution
# --------------------------------------------------------------------------------------


def build_alias_map(corpus: kg.Corpus) -> dict[str, str]:
    """Harvest abbreviation definitions that the corpus itself states.

    Only definitions the papers actually write down are used. A model-invented alias
    would defeat the purpose, so nothing here comes from world knowledge.
    """
    alias: dict[str, str] = {}
    for paper in corpus.papers:
        for para in paper.paragraphs:
            for m in ABBR_DEF_RE.finditer(para.text):
                full, abbr = m.group(1).strip(), m.group(2).strip()
                if len(full.split()) >= 1 and abbr.lower() != full.lower():
                    alias.setdefault(abbr.lower(), full.lower())
            for m in ABBR_DEF_RE2.finditer(para.text):
                abbr, full = m.group(1).strip(), m.group(2).strip()
                alias.setdefault(abbr.lower(), full.lower())
    return alias


FORM_WORDS = (
    "monolayer", "monolayers", "single-layer", "single layer", "few-layer",
    "few layer", "bilayer", "trilayer", "2d", "two-dimensional",
)

STOP_LEAD = {
    "we", "our", "their", "the", "a", "an", "this", "that", "these", "those",
    "use", "uses", "used", "using", "employ", "employs", "employed", "employing",
    "experimental", "computed", "measured", "simulated", "obtained", "recorded",
    "is", "are", "was", "were", "be", "been", "of", "for", "and", "or", "with",
    "by", "on", "in", "from", "to", "at",
}

PREP_TOKENS = {"following", "after", "before", "upon", "during", "via", "through"}


def _clean_method_label(raw: str) -> str:
    """Cut the verb/preposition debris that the suffix regex drags in.

    'employ broadband impulsive vibrational spectroscopy'
        -> 'broadband impulsive vibrational spectroscopy'
    'resolution noncontact atomic force microscopy' -> 'noncontact atomic force microscopy'
    'meV blueshift following n-BuLi treatment'      -> 'n-BuLi treatment'
    """
    toks = [t for t in re.split(r"\s+", raw.strip()) if t]
    for i in range(len(toks) - 1, -1, -1):
        if toks[i].lower() in PREP_TOKENS and i + 1 < len(toks):
            toks = toks[i + 1 :]
            break
    while len(toks) > 1 and (toks[0].lower() in STOP_LEAD or re.fullmatch(r"[A-Za-z]*ed", toks[0].lower())):
        toks = toks[1:]
    if len(toks) > 4:
        toks = toks[-4:]
    return " ".join(toks)


def canonical(label: str, alias_map: dict[str, str]) -> str:
    s = kg.ascii_fold(label).lower()
    s = s.replace("\u2080", "0").replace("\u2081", "1").replace("\u2082", "2").replace("\u2083", "3")
    s = re.sub(r"[^a-z0-9\s\-]", " ", s)
    s = kg.normalize_ws(s)
    if s in alias_map:
        s = alias_map[s]
    s = re.sub(r"\b(the|a|an)\b", " ", s)
    s = kg.normalize_ws(s)
    # a form qualifier narrows the object; it does not mint a second material
    for form in FORM_WORDS:
        if s.startswith(form + " "):
            s = s[len(form) + 1 :]
            break
    # a bare class plural names the class, not an instance
    s = re.sub(r"\btmds?\b", "tmd", s)
    return kg.normalize_ws(s)


# --------------------------------------------------------------------------------------
# extraction (deterministic, no model)
# --------------------------------------------------------------------------------------


@dataclass
class Mention:
    source_id: str
    paragraph_id: str
    section: str
    label: str
    entity_type: str
    quote: str
    rule: str
    extra: dict = field(default_factory=dict)


@dataclass
class RelDraft:
    rel_type: str
    subject_label: str
    subject_type: str
    object_label: str
    object_type: str
    source_id: str
    paragraph_id: str
    quote: str
    rule: str
    conditions: list[str] = field(default_factory=list)
    evidence_strength: str = "direct"
    basis: str = ""


def _clean_label(s: str) -> str:
    s = re.sub(r"^[\s\(\[]+|[\s\)\]]+$", "", s)
    s = re.sub(r"\s+", " ", s).strip(" ,.;:")
    return s


METRIC_NOISE = {
    "these", "this", "for", "both", "respectively", "achieved", "durations",
    "which", "that", "there", "was", "were", "is", "are", "gives", "give",
    "yield", "yields", "about", "around", "of", "at", "a", "an", "the",
}


def _clean_metric_name(raw: str) -> str:
    """Keep the noun phrase that names the quantity, drop the sentence debris.

    'for both films'            -> ''            (rejected: no quantity named)
    'These pulses achieved durations' -> 'durations'
    'temporal resolution'       -> 'temporal resolution'
    """
    toks = [t for t in re.split(r"\s+", raw.strip()) if t]
    while toks and toks[0].lower() in METRIC_NOISE:
        toks = toks[1:]
    if len(toks) > 3:
        toks = toks[-3:]
    name = " ".join(t for t in toks if t)
    if not name or name.lower() in METRIC_NOISE:
        return ""
    return name


def _sentence_at(text: str, start: int, end: int) -> str:
    spans = kg.split_sentences(text)
    for s, e in spans:
        if s <= start < e:
            return text[s:e]
    return text[start:end]


def extract_mentions(corpus: kg.Corpus, alias_map: dict[str, str]) -> list[Mention]:
    mentions: list[Mention] = []
    for paper in corpus.papers:
        for para in paper.paragraphs:
            text = para.text
            sentences = kg.split_sentences(text)
            seen_sentences: set[int] = set()

            def add(label: str, etype: str, pos: int, rule: str, extra: dict | None = None):
                label = _clean_label(label)
                if not label or len(label) < 2 or label.lower() in STOP_LABELS:
                    return
                quote = _sentence_at(text, pos, pos + len(label))
                if len(quote) > 600:
                    quote = quote[:600].rsplit(" ", 1)[0]
                mentions.append(
                    Mention(
                        source_id=paper.source_id,
                        paragraph_id=para.paragraph_id,
                        section=para.section,
                        label=label,
                        entity_type=etype,
                        quote=quote,
                        rule=rule,
                        extra=extra or {"mention_offset_in_paragraph": pos},
                    )
                )

            # methods: suffix words
            for m in re.finditer(r"\b([A-Za-z][A-Za-z\-]{2,25}(?:\s+[A-Za-z][A-Za-z\-]{2,20}){0,4})\s+(spectroscopy|spectrometry|microscopy|diffraction|scattering|calculations?|simulations?|treatment)\b", text, re.I):
                cleaned = _clean_method_label(m.group(0))
                if cleaned:
                    add(cleaned, "method", m.start(), "method_suffix")

            # methods: acronyms (only when written as a standalone token)
            for m in re.finditer(r"\b([A-Z]{2,8}\d?)\b", text):
                tok = m.group(1)
                if tok in METHOD_ACRONYMS:
                    add(tok, "method", m.start(), "method_acronym")

            # materials: chemical formulas and monolayer phrases
            for m in FORMULA_RE.finditer(text):
                cand = m.group(0)
                if re.fullmatch(r"\d+", cand):
                    continue
                if len(cand) < 3:
                    continue
                add(cand, "material", m.start(), "material_formula")
            for m in re.finditer(r"\bmonolayer\s+(?:of\s+)?([A-Z][A-Za-z0-9\-]{1,12})\b", text):
                add(m.group(0), "material", m.start(), "material_monolayer")

            # metrics with a value
            for m in METRIC_RE.finditer(text):
                name = _clean_metric_name(m.group("name"))
                if not name:
                    continue
                add(
                    f"{name} = {m.group('value')} {m.group('unit')}".strip(),
                    "metric",
                    m.start(),
                    "metric_value",
                    {"metric_name": name, "value": m.group("value"), "unit": m.group("unit")},
                )

            # tasks
            low = text.lower()
            for task in TASK_LEXICON:
                pos = low.find(task)
                if pos >= 0:
                    add(text[pos : pos + len(task)], "task", pos, "task_lexicon")

            # conclusions: at most two per paragraph, and only real propositions
            per_para = 0
            for s, e in sentences:
                if per_para >= 2:
                    break
                sent = text[s:e]
                low_s = sent.lower()
                if not any(cue in low_s for cue in CONCLUSION_CUES):
                    continue
                if s in seen_sentences:
                    continue
                seen_sentences.add(s)
                clause = re.split(r"(?<=[,;])\s", sent)[0]
                if len(clause) > 220:
                    clause = clause[:220].rsplit(" ", 1)[0]
                if len(clause) >= 40 and clause.count(" ") >= 5:
                    per_para += 1
                    add(clause, "conclusion", s, "conclusion_cue")
    return mentions


def extract_relations(
    corpus: kg.Corpus, mentions: list[Mention]
) -> list[RelDraft]:
    """Pattern rules over sentences. Deliberately conservative: no cue, no edge."""
    by_locator: dict[str, list[Mention]] = {}
    for m in mentions:
        by_locator.setdefault(f"{m.source_id}#{m.paragraph_id}", []).append(m)

    rels: list[RelDraft] = []
    for paper in corpus.papers:
        for para in paper.paragraphs:
            loc = f"{paper.source_id}#{para.paragraph_id}"
            paras_mentions = by_locator.get(loc, [])
            if len(paras_mentions) < 2:
                continue
            text = para.text
            sentences = kg.split_sentences(text)
            for s, e in sentences:
                sent = text[s:e]
                low = sent.lower()
                in_sent = [m for m in paras_mentions if m.quote and m.quote[:60] in sent]
                methods = [m for m in in_sent if m.entity_type == "method"]
                materials = [m for m in in_sent if m.entity_type == "material"]
                metrics = [m for m in in_sent if m.entity_type == "metric"]
                conclusions = [m for m in in_sent if m.entity_type == "conclusion"]

                def emit(rel_type, subj, obj, strength="direct", basis=""):
                    rels.append(
                        RelDraft(
                            rel_type=rel_type,
                            subject_label=subj.label,
                            subject_type=subj.entity_type,
                            object_label=obj.label,
                            object_type=obj.entity_type,
                            source_id=paper.source_id,
                            paragraph_id=para.paragraph_id,
                            quote=sent if len(sent) <= 600 else sent[:600].rsplit(" ", 1)[0],
                            rule=f"cue:{rel_type}",
                            conditions=_conditions_from(sent),
                            evidence_strength=strength,
                            basis=basis,
                        )
                    )

                # characterizes / uses: method vs material
                if methods and materials:
                    if any(c in low for c in REL_CUES["characterizes"]):
                        emit("characterizes", methods[0], materials[0])
                    elif any(c in low for c in ("use", "using", "used", "employ", "via")):
                        emit("uses", methods[0], materials[0])
                    elif "with" in low:
                        emit("uses", methods[0], materials[0], "indirect", "cue is the preposition 'with'")

                # measures: method vs metric
                if methods and metrics:
                    emit("measures", methods[0], metrics[0])

                # compares / outperforms between two methods
                if len(methods) >= 2:
                    if any(c in low for c in REL_CUES["compares"]):
                        emit("compares", methods[0], methods[1])
                    elif any(c in low for c in REL_CUES["outperforms"]):
                        emit("outperforms", methods[0], methods[1], "direct" if _conditions_from(sent) else "indirect")

                # supports / contradicts involving conclusions
                if conclusions:
                    anchor = (methods + materials + metrics)
                    if anchor and any(c in low for c in REL_CUES["supports"]):
                        emit("supports", anchor[0], conclusions[0])
                    if anchor and any(c in low for c in REL_CUES["contradicts"]):
                        emit("contradicts", anchor[0], conclusions[0])
    return rels


def _conditions_from(sentence: str) -> list[str]:
    """Pull explicitly stated qualifiers out of the sentence; never invent one."""
    conds: list[str] = []
    for pat in (
        r"\b(?:at|under)\s+(\d+(?:\.\d+)?)\s*(K|mK|°C|C|GPa|bar|mbar)\b",
        r"\b(?:room temperature|low temperature|cryogenic)\b",
        r"\b(?:on|in)\s+([A-Za-z0-9\-]+)\s+(?:substrate|monolayer)\b",
        r"\b(\d+(?:\.\d+)?)\s*(?:min|minutes|hours|h|s)\s+(?:treatment|annealing|exposure)\b",
    ):
        for m in re.finditer(pat, sentence, re.I):
            conds.append(kg.normalize_ws(m.group(0)))
    return conds


# --------------------------------------------------------------------------------------
# evidence binding
# --------------------------------------------------------------------------------------


def bind(quotes: list[dict], corpus: kg.Corpus) -> tuple[list[dict], list[dict]]:
    """Verify every quote against the paragraph it claims to come from.

    `quotes` items need: source_id, paragraph_id, quote. Everything else is preserved.
    Returns (verified, rejected). Rejected items carry the reason — they are never dropped
    silently, because an unverifiable quote is itself a finding.
    """
    verified: list[dict] = []
    rejected: list[dict] = []
    for item in quotes:
        locator = f"{item.get('source_id')}#{item.get('paragraph_id')}"
        para = corpus.get(locator)
        if para is None:
            rejected.append(
                {
                    **item,
                    "verification": {
                        "found": False,
                        "reason": "unknown_locator",
                        "detail": f"no paragraph {locator} in the corpus",
                    },
                }
            )
            continue
        loc = kg.locate_quote(para.text, item.get("quote") or "")
        if not loc["found"]:
            rejected.append(
                {
                    **item,
                    "verification": {
                        "found": False,
                        "reason": "quote_not_in_paragraph",
                        "detail": "the quote does not occur verbatim in the cited paragraph",
                        "paragraph_sha256": kg.sha256_text(para.text),
                    },
                }
            )
            continue
        verified.append(
            {
                **item,
                "locator": {
                    "source_id": item["source_id"],
                    "paragraph_id": item["paragraph_id"],
                    "section": para.section,
                    "char_start": loc["char_start"],
                    "char_end": loc["char_end"],
                    "match_kind": loc["match_kind"],
                    "matched_text": loc["matched_text"],
                    "paragraph_sha256": kg.sha256_text(para.text),
                },
            }
        )
    return verified, rejected


# --------------------------------------------------------------------------------------
# alignment
# --------------------------------------------------------------------------------------


def align(
    verified_mentions: list[dict],
    verified_edges: list[dict],
    alias_map: dict[str, str],
    ontology: dict,
    mode: str = "skill",
) -> dict:
    """Group verified mentions into nodes; record every merge and every refusal to merge."""
    type_names = {t["name"] for t in ontology["types"]["entity_types"]}
    merges: list[dict] = []
    blocked: list[dict] = []

    groups: dict[tuple[str, str], dict] = {}
    for m in verified_mentions:
        if m["entity_type"] not in type_names:
            blocked.append(
                {
                    "kind": "ontology_extension",
                    "label": m["label"],
                    "entity_type": m["entity_type"],
                    "reason": "entity type not in controlled vocabulary",
                }
            )
            m["entity_type"] = "conclusion" if mode == "skill" else m["entity_type"]

        if mode == "baseline":
            key = ("exact", m["label"])
        else:
            key = (m["entity_type"], canonical(m["label"], alias_map))

        g = groups.setdefault(
            key,
            {
                "key": key,
                "entity_type": m["entity_type"],
                "labels": [],
                "mentions": [],
            },
        )
        g["labels"].append(m["label"])
        g["mentions"].append(m)

    nodes: list[dict] = []
    for (_, canon), g in sorted(groups.items(), key=lambda kv: repr(kv[0])):
        labels = sorted(set(g["labels"]))
        primary = max(labels, key=lambda s: (len(s), s))
        sources = sorted({m["source_id"] for m in g["mentions"]})
        canon_key = canon if mode != "baseline" else primary
        cross = len(sources) > 1
        conflict, detail = _instance_conflict(g["mentions"])

        # Two distinct refusals, both recorded:
        #  * a metric belongs to the paper that measured it — merging would fuse two
        #    papers' numeric results into one conclusion;
        #  * any other object whose stated conditions disagree across papers is not the
        #    same instance.
        if cross and mode != "baseline" and (g["entity_type"] == "metric" or conflict):
            blocked.append(
                {
                    "kind": "must_not_merge",
                    "level": "value" if g["entity_type"] == "metric" else "condition",
                    "canonical": canon_key,
                    "entity_type": g["entity_type"],
                    "reason": (
                        "metric values and their conditions are paper-specific; merging them "
                        "would assert one conclusion across two papers"
                        if g["entity_type"] == "metric"
                        else "the two papers state different conditions for this object, so it is not the same instance"
                    ),
                    "sources": sources,
                    "conflicting_conditions": detail,
                    "action": "kept as separate per-source nodes",
                }
            )
            nodes.extend(_split_by_source(g, ontology, alias_map, mode))
            continue

        node_id = kg.mint_id("node", g["entity_type"], canon_key, *sources)
        nodes.append(
            {
                "id": node_id,
                "type": g["entity_type"],
                "label": primary,
                "aliases": [l for l in labels if l != primary],
                "canonical": canon_key,
                "sources": sources,
                "scope": "cross_paper_concept" if cross else "single_paper",
                "same_instance_across_papers": False if cross else None,
                "mentions": [_mention_record(m) for m in g["mentions"]],
            }
        )
        if len(labels) > 1 or cross:
            merges.append(
                {
                    "node_id": node_id,
                    "merged_labels": labels,
                    "basis": _merge_basis(labels, alias_map, sources),
                    "sources": sources,
                    "kind": "alias_merge" if len(sources) == 1 else "cross_paper_concept_merge",
                    "same_instance_across_papers": False if cross else None,
                }
            )
        if cross and mode != "baseline":
            blocked.append(
                {
                    "kind": "must_not_merge",
                    "level": "instance",
                    "canonical": canon_key,
                    "entity_type": g["entity_type"],
                    "sources": sources,
                    "reason": "identical concept in both papers, but no shared sample id, dataset id, instrument setting or batch is reported; the concept node is shared, the instance is not",
                    "action": "node kept with same_instance_across_papers=false",
                }
            )

    # A metric QUANTITY may be shared (both papers report an emission energy); the VALUES
    # may not be fused into one comparative statement.
    quantities: dict[str, list[dict]] = {}
    for n in nodes:
        if n["type"] != "metric":
            continue
        qname = kg.normalize_ws((n["label"].split("=")[0] or "").lower())
        quantities.setdefault(qname, []).append(n)
    for qname, group in sorted(quantities.items()):
        srcs = sorted({s for n in group for s in n["sources"]})
        if len(srcs) < 2 or mode == "baseline":
            continue
        blocked.append(
            {
                "kind": "must_not_merge",
                "level": "value",
                "canonical": qname,
                "entity_type": "metric",
                "reason": "the same quantity is reported in both papers with paper-local values and conditions; the values must stay separate so no cross-paper conclusion is implied",
                "sources": srcs,
                "values": [
                    {"node": n["id"], "label": n["label"], "sources": n["sources"]} for n in group
                ],
                "action": "per-paper metric nodes kept; comparison query returns undetermined",
            }
        )

    return {"nodes": nodes, "merges": merges, "blocked": blocked}


def _instance_conflict(mentions: list[dict]) -> tuple[bool, dict]:
    """Do the papers state different, explicit conditions for the same surface form?"""
    per_source: dict[str, set[str]] = {}
    for m in mentions:
        per_source.setdefault(m["source_id"], set()).update(_conditions_from(m.get("quote") or ""))
    keys = sorted(per_source)
    for a, b in itertools.combinations(keys, 2):
        if per_source[a] and per_source[b] and per_source[a] != per_source[b]:
            return True, {a: sorted(per_source[a]), b: sorted(per_source[b])}
    return False, {}


def _split_by_source(g: dict, ontology, alias_map, mode) -> list[dict]:
    out = []
    for sid in sorted({m["source_id"] for m in g["mentions"]}):
        ms = [m for m in g["mentions"] if m["source_id"] == sid]
        labels = sorted({m["label"] for m in ms})
        primary = max(labels, key=len)
        canon = canonical(primary, alias_map) if mode != "baseline" else primary
        out.append(
            {
                "id": kg.mint_id("node", g["entity_type"], canon, sid),
                "type": g["entity_type"],
                "label": primary,
                "aliases": [l for l in labels if l != primary],
                "canonical": canon,
                "sources": [sid],
                "scope": "single_paper",
                "same_instance_across_papers": None,
                "mentions": [_mention_record(m) for m in ms],
            }
        )
    return out


def _mention_record(m: dict) -> dict:
    return {
        "source_id": m["source_id"],
        "paragraph_id": m["paragraph_id"],
        "locator": m["locator"],
        "quote": m["quote"],
        "rule": m.get("rule", ""),
        "evidence_strength": m.get("evidence_strength", "direct"),
    }


def _merge_basis(labels: list[str], alias_map: dict[str, str], sources: list[str] | None = None) -> str:
    pieces = []
    for l in labels:
        low = l.lower()
        if low in alias_map:
            pieces.append(f"'{l}' is defined in the corpus as '{alias_map[low]}'")
    if pieces:
        return "; ".join(pieces)
    if sources and len(sources) > 1:
        return (
            "identical after case/punctuation/whitespace normalisation; merged at CONCEPT level only "
            "(same kind of object, not shown to be the same instance)"
        )
    return "identical after case/punctuation/whitespace normalisation"


# --------------------------------------------------------------------------------------
# graph assembly
# --------------------------------------------------------------------------------------


def build_edges(
    verified_edges: list[dict],
    nodes: list[dict],
    ontology: dict,
    alias_map: dict[str, str],
    mode: str = "skill",
) -> tuple[list[dict], list[dict]]:
    """Resolve edge endpoints to node ids and enforce the relation signature."""
    index: dict[tuple[str, str], list[dict]] = {}
    for n in nodes:
        for lab in [n["label"], *n["aliases"]]:
            key = (n["type"], lab if mode == "baseline" else canonical(lab, alias_map))
            index.setdefault(key, []).append(n)

    edges: list[dict] = []
    issues: list[dict] = []
    seen: set[tuple] = set()

    for e in verified_edges:
        if mode == "baseline":
            skey = (e["subject_type"], e["subject_label"])
            okey = (e["object_type"], e["object_label"])
        else:
            skey = (e["subject_type"], canonical(e["subject_label"], alias_map))
            okey = (e["object_type"], canonical(e["object_label"], alias_map))
        subj = index.get(skey)
        obj = index.get(okey)
        if not subj or not obj:
            issues.append({"kind": "dangling_edge", "edge": e.get("rule"), "detail": f"{skey} -> {okey}"})
            continue
        s_node, o_node = subj[0], obj[0]
        if s_node["id"] == o_node["id"]:
            continue

        rel_type = e["rel_type"]
        sig = kg.relation_signature(ontology, rel_type)
        if sig is None:
            issues.append({"kind": "relation_outside_vocabulary", "rel_type": rel_type})
            rel_type = "uses"
            sig = kg.relation_signature(ontology, rel_type)
        if not sig:
            continue
        if s_node["type"] not in sig["subject_types"] or o_node["type"] not in sig["object_types"]:
            issues.append(
                {
                    "kind": "relation_signature_violation",
                    "rel_type": rel_type,
                    "subject_type": s_node["type"],
                    "object_type": o_node["type"],
                    "allowed": {"subject": sig["subject_types"], "object": sig["object_types"]},
                }
            )
            continue

        dedup = (rel_type, s_node["id"], o_node["id"], e["locator"]["source_id"], e["locator"]["paragraph_id"])
        if dedup in seen:
            continue
        seen.add(dedup)

        conditions = list(e.get("conditions") or [])
        strength = e.get("evidence_strength", "direct")
        if sig.get("conditions_required") and not conditions:
            issues.append(
                {
                    "kind": "missing_conditions",
                    "rel_type": rel_type,
                    "edge": f"{s_node['label']} -> {o_node['label']}",
                    "detail": "this relation needs the basis it was measured on; none was quoted",
                }
            )
            strength = "indirect"

        edges.append(
            {
                "id": kg.mint_id("edge", rel_type, s_node["id"], o_node["id"], e["locator"]["source_id"], e["locator"]["paragraph_id"]),
                "type": rel_type,
                "subject": s_node["id"],
                "object": o_node["id"],
                "subject_label": s_node["label"],
                "object_label": o_node["label"],
                "direction": sig["direction"],
                "conditions": conditions,
                "evidence_strength": strength,
                "basis": e.get("basis", ""),
                "evidence": {
                    "locator": e["locator"],
                    "quote": e["quote"],
                },
            }
        )
    return edges, issues


# --------------------------------------------------------------------------------------
# queries — three real traversals, plus the honest non-answer
# --------------------------------------------------------------------------------------


def run_queries(nodes: list[dict], edges: list[dict], corpus: kg.Corpus) -> list[dict]:
    queries: list[dict] = []
    by_id = {n["id"]: n for n in nodes}

    # Q1: which concepts are shared across the two papers?
    shared = [n for n in nodes if len(n["sources"]) > 1]
    queries.append(
        {
            "id": "Q1-shared-concepts",
            "question": "Which concepts appear in both papers, and what is the evidence in each?",
            "kind": "cross_paper_overlap",
            "executed": True,
            "subgraph": {"nodes": [n["id"] for n in shared]},
            "answers": [
                {
                    "node": n["id"],
                    "label": n["label"],
                    "type": n["type"],
                    "per_source": _per_source_evidence(n),
                }
                for n in shared
            ],
            "answer_count": len(shared),
            "undetermined": [
                {
                    "question": "Does the shared term mean the same physical sample / instance?",
                    "status": "undetermined",
                    "reason": "the corpus states no shared sample id, substrate batch, or dataset identifier; a shared generic term only fixes the KIND of object",
                    "rule": "references/entity-types.json cross_paper_rule",
                }
            ],
            "evidence": [loc_str(m["locator"]) for n in shared for m in n["mentions"]],
        }
    )

    # Q2: which methods are used, in which paper, under which conditions?
    method_nodes = [n for n in nodes if n["type"] == "method"]
    methods_out = []
    for n in method_nodes:
        out_edges = [e for e in edges if e["subject"] == n["id"]]
        methods_out.append(
            {
                "node": n["id"],
                "label": n["label"],
                "sources": n["sources"],
                "relations": [
                    {
                        "type": e["type"],
                        "object": by_id[e["object"]]["label"] if e["object"] in by_id else e["object"],
                        "conditions": e["conditions"],
                        "evidence_strength": e["evidence_strength"],
                        "locator": loc_str(e["evidence"]["locator"]),
                    }
                    for e in out_edges
                ],
            }
        )
    queries.append(
        {
            "id": "Q2-method-usage",
            "question": "Which methods does each paper use, on what object, and under what stated conditions?",
            "kind": "relation_traversal",
            "executed": True,
            "subgraph": {
                "nodes": [n["id"] for n in method_nodes] + sorted({e["object"] for e in edges if e["type"] in ("uses", "characterizes", "measures")}),
            },
            "answers": methods_out,
            "answer_count": len(methods_out),
            "non_empty": len([m for m in methods_out if m["relations"]]),
            "undetermined": [
                {
                    "question": "Were identical instrument settings used across papers?",
                    "status": "undetermined",
                    "reason": "settings are reported paper-locally and are not comparable across papers unless both state them",
                }
            ],
            "evidence": [loc_str(e["evidence"]["locator"]) for e in edges if e["type"] in ("uses", "characterizes", "measures")],
        }
    )

    # Q3: what evidence exists for a cross-paper numeric comparison? (expected: none)
    metric_nodes = [n for n in nodes if n["type"] == "metric"]
    per_source: dict[str, list[dict]] = {}
    for n in metric_nodes:
        for m in n["mentions"]:
            per_source.setdefault(m["source_id"], []).append(
                {"label": n["label"], "node": n["id"], "locator": loc_str(m["locator"])}
            )
    queries.append(
        {
            "id": "Q3-cross-paper-metric-comparison",
            "question": "Can the two papers be compared on a shared metric?",
            "kind": "refusal_with_evidence",
            "executed": True,
            "subgraph": {"nodes": [n["id"] for n in metric_nodes]},
            "answers": [
                {
                    "source_id": sid,
                    "metrics": items,
                }
                for sid, items in sorted(per_source.items())
            ],
            "answer_count": len(per_source),
            "undetermined": [
                {
                    "question": "Is metric A in paper 1 comparable with metric B in paper 2?",
                    "status": "refused",
                    "reason": "the two papers differ in sample, treatment, and stated conditions; no shared sample or identical setting is reported, so a single comparative conclusion is not supported",
                    "rule": "task input revision: never combine conditions, samples or numeric results of different papers into one conclusion",
                    "what_would_be_needed": "the same sample id plus the same measurement setting reported in both papers",
                }
            ],
            "evidence": [loc_str(m["locator"]) for n in metric_nodes for m in n["mentions"]],
        }
    )

    # Q4: where do the papers disagree?
    contra = [e for e in edges if e["type"] == "contradicts"]
    queries.append(
        {
            "id": "Q4-conflicts",
            "question": "Which statements conflict, inside a paper or across the pair?",
            "kind": "conflict_scan",
            "executed": True,
            "subgraph": {"edges": [e["id"] for e in contra]},
            "answers": [
                {
                    "edge": e["id"],
                    "subject": e["subject_label"],
                    "object": e["object_label"],
                    "locator": loc_str(e["evidence"]["locator"]),
                    "quote": e["evidence"]["quote"][:200],
                }
                for e in contra
            ],
            "answer_count": len(contra),
            "undetermined": []
            if contra
            else [
                {
                    "question": "Does the corpus contain no conflicting statements?",
                    "status": "undetermined",
                    "reason": "no contradiction met the evidence bar, which is not the same as the papers agreeing; an explicit conflict may be phrased outside the cue set",
                    "human_takeover": "read the discussion sections against each other before asserting agreement",
                }
            ],
            "evidence": [loc_str(e["evidence"]["locator"]) for e in contra],
        }
    )
    return queries


def loc_str(loc: dict | str) -> str:
    """Compact citation form for query answers: `<source_id>#<paragraph_id>`."""
    if isinstance(loc, str):
        return loc
    return f"{loc.get('source_id', '?')}#{loc.get('paragraph_id', '?')}"


def _per_source_evidence(node: dict) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for m in node["mentions"]:
        out.setdefault(m["source_id"], []).append(loc_str(m["locator"]))
    return {k: sorted(set(v)) for k, v in out.items()}


# --------------------------------------------------------------------------------------
# QC
# --------------------------------------------------------------------------------------


def qc(nodes: list[dict], edges: list[dict], rejected: list[dict], contract_check: dict, contract) -> dict:
    checks: list[dict] = []

    def add(code, severity, passed, detail):
        checks.append({"code": code, "severity": severity, "passed": bool(passed), "detail": detail})

    ids = [n["id"] for n in nodes] + [e["id"] for e in edges]
    add("QC-01", "critical", len(ids) == len(set(ids)), f"{len(ids)} ids, {len(set(ids))} unique")
    add(
        "QC-02",
        "critical",
        all(n.get("mentions") for n in nodes),
        "every node carries at least one verbatim mention",
    )
    add(
        "QC-03",
        "critical",
        all(e["evidence"]["quote"] and e["evidence"]["locator"].get("match_kind") for e in edges),
        "every edge carries a located quote",
    )
    add("QC-04", "warn", not any(r.get("reason") == "quote_not_in_paragraph" for r in rejected), f"{len(rejected)} rejected quotes")
    add(
        "QC-05",
        "warn",
        all(e.get("conditions") for e in edges if e["type"] in ("compares", "outperforms", "measures")),
        "comparative and measured relations state their basis",
    )
    add("QC-06", "critical", contract_check["satisfied"], f"contract fields missing: {contract_check['missing'] or 'none'}")
    dupes = _duplicate_concepts(nodes)
    add("QC-07", "warn", not dupes, f"duplicate concept nodes: {dupes or 'none'}")
    add(
        "QC-08",
        "critical",
        all(n["type"] in {t["name"] for t in ENTITY_TYPES_CACHE} for n in nodes) if ENTITY_TYPES_CACHE else True,
        "all node types within the controlled vocabulary",
    )
    add(
        "QC-09",
        "warn",
        all(len(m.get("quote", "")) >= 15 for n in nodes for m in n["mentions"]),
        "no evidence span is shorter than 15 characters (too short to be meaningful)",
    )
    return {
        "checks": checks,
        "critical_failures": sum(1 for c in checks if c["severity"] == "critical" and not c["passed"]),
        "warnings": sum(1 for c in checks if c["severity"] == "warn" and not c["passed"]),
        "contract_source": contract.source,
    }


ENTITY_TYPES_CACHE: list[dict] = []


def set_entity_types(types: list[dict]) -> None:
    global ENTITY_TYPES_CACHE
    ENTITY_TYPES_CACHE = types


def _duplicate_concepts(nodes: list[dict]) -> list[str]:
    seen: dict[tuple[str, str], list[str]] = {}
    for n in nodes:
        seen.setdefault((n["type"], n["canonical"]), []).append(n["id"])
    return [f"{k[0]}:{k[1]}" for k, v in seen.items() if len(v) > 1]
