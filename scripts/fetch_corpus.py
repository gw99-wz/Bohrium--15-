#!/usr/bin/env python3
"""Build a LOCAL two-paper corpus bundle from Europe PMC / NCBI JATS full text.

This is development scaffolding, not part of the submitted Skill. It exists so the
pipeline can be exercised end to end on REAL open-access paragraphs with REAL JATS
section/paragraph structure, instead of on invented text.

The official challenge supplies its own 3 fixed cases; nothing here is an official
case, and the generated bundle says so in `corpus-manifest.json`.

Usage:
    python scripts/fetch_corpus.py --out corpus/ws2-pair [--max-paragraphs 15]

Sources (both CC BY, fetched live; nothing is vendored in git beyond a trimmed fixture):
    PMC13495497  doi:10.1021/acsnanoscienceau.5c00165
    PMC12312154  doi:10.1021/acsnano.5c08533
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

PAPERS = [
    {
        "pmcid": "PMC13495497",
        "pmid_numeric": "13495497",
        "role": "secondary",
    },
    {
        "pmcid": "PMC12312154",
        "pmid_numeric": "12312154",
        "role": "primary",
    },
]

EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pmc&id={id}"
UA = "bohrium-reading15-kg/1.0 (local development fixture)"


def fetch(numeric_id: str) -> str:
    req = urllib.request.Request(EFETCH.format(id=numeric_id), headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read()
    return raw.decode("utf-8", "replace")


def text_of(el) -> str:
    """Flatten an XML element to running text, dropping inline markup but keeping words."""
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip()


def strip_ns(tag: str) -> str:
    return tag.split("}", 1)[-1]


def find_all(root, name):
    return [e for e in root.iter() if strip_ns(e.tag) == name]


def load_paper(xml_text: str, spec: dict) -> dict:
    root = ET.fromstring(xml_text)
    front = find_all(root, "front")[0]

    title = text_of(find_all(front, "article-title")[0])
    doi = None
    for aid in find_all(front, "article-id"):
        if aid.get("pub-id-type") == "doi":
            doi = text_of(aid)
    # publishers sometimes put the DOI in the URI of the license block only
    if not doi:
        m = re.search(r"10\.\d{4,9}/[^\s\"<]+", xml_text[:20000])
        doi = m.group(0) if m else ""

    authors = []
    for contrib in find_all(front, "contrib"):
        surname = find_all(contrib, "surname")
        given = find_all(contrib, "given-names")
        if surname:
            name = text_of(surname[0])
            if given:
                name = f"{text_of(given[0])} {name}"
            authors.append(name)

    licence = ""
    lic_el = find_all(front, "license")
    if lic_el:
        lic_el = lic_el[0]
        lic_text = text_of(lic_el)
        href = ""
        for e in lic_el.iter():
            if e.get("xlink:href") or e.get("{http://www.w3.org/1999/xlink}href"):
                href = e.get("xlink:href") or e.get("{http://www.w3.org/1999/xlink}href")
        licence = (lic_text + (" " + href if href else "")).strip()

    paragraphs = []
    body = find_all(root, "body")
    section_stack: list[tuple[int, str]] = []

    def walk(node, depth: int = 0):
        for child in list(node):
            tag = strip_ns(child.tag)
            if tag == "sec":
                title_el = find_all(child, "title")
                sec_title = text_of(title_el[0]) if title_el else ""
                section_stack.append((depth, sec_title))
                walk(child, depth + 1)
                section_stack.pop()
            elif tag == "p":
                body_text = text_of(child)
                if len(body_text) < 40:
                    continue
                section_path = " / ".join(t for _, t in section_stack if t) or "body"
                paragraphs.append(
                    {
                        "paragraph_id": child.get("id")
                        or f"{spec['pmcid']}.s{len(section_stack)}.p{len(paragraphs) + 1:03d}",
                        "section": section_path,
                        "text": body_text,
                    }
                )
            elif tag in {"fig", "table-wrap", "disp-formula"}:
                label = find_all(child, "label")
                cap = find_all(child, "caption")
                cap_text = text_of(cap[0]) if cap else ""
                if cap_text:
                    paragraphs.append(
                        {
                            "paragraph_id": child.get("id")
                            or f"{spec['pmcid']}.{tag}.{len(paragraphs) + 1:03d}",
                            "section": " / ".join(t for _, t in section_stack if t) or "body",
                            "kind": tag,
                            "label": text_of(label[0]) if label else "",
                            "text": cap_text,
                        }
                    )
            else:
                walk(child, depth)

    if body:
        walk(body[0])
    # abstract last so abstract-first ordering is not assumed
    abstract = find_all(root, "abstract")
    abstract_paras = []
    if abstract:
        for i, p in enumerate(find_all(abstract[0], "p")):
            t = text_of(p)
            if len(t) >= 40:
                abstract_paras.append(
                    {
                        "paragraph_id": f"{spec['pmcid']}.abstract.p{i + 1:03d}",
                        "section": "abstract",
                        "text": t,
                    }
                )

    return {
        "source_id": spec["pmcid"],
        "role": spec["role"],
        "doi": doi,
        "title": re.sub(r"\s+", " ", title),
        "authors": authors,
        "licence": licence,
        "pmcid": spec["pmcid"],
        "origin": "Europe PMC / NCBI PMC JATS full text (efetch db=pmc)",
        "sha256_of_xml": hashlib.sha256(xml_text.encode("utf-8")).hexdigest(),
        "paragraphs": abstract_paras + paragraphs,
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="output corpus directory")
    ap.add_argument("--max-paragraphs", type=int, default=0, help="trim each paper (0 = keep all)")
    args = ap.parse_args(argv)

    out = Path(args.out)
    raw_dir = out / "_raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    built = []
    for spec in PAPERS:
        xml_path = raw_dir / f"{spec['pmcid']}.xml"
        if xml_path.exists():
            xml_text = xml_path.read_text(encoding="utf-8")
            print(f"[fetch] reuse {xml_path}")
        else:
            print(f"[fetch] GET {spec['pmcid']}")
            xml_text = fetch(spec["pmid_numeric"])
            xml_path.write_text(xml_text, encoding="utf-8")
        paper = load_paper(xml_text, spec)
        if args.max_paragraphs:
            paper["paragraphs"] = paper["paragraphs"][: args.max_paragraphs]
        print(
            f"           {paper['source_id']} paras={len(paper['paragraphs'])} "
            f"doi={paper['doi']} licence={paper['licence'][:60]!r}"
        )
        built.append(paper)

    # ---- exact shapes the challenge's input revision describes ----
    manifest = {
        "corpus_id": "local-ws2-pair",
        "provenance": "LOCAL DEVELOPMENT FIXTURE — not an official challenge case",
        "synthetic": False,
        "redistributable": True,
        "note": (
            "Two independent, real, CC BY open-access papers, fetched from PMC JATS. "
            "Paragraph text is the publisher's JATS <p>/caption text with inline markup "
            "flattened. paragraph_id is our own stable id derived from the JATS section "
            "path — it is NOT a PDF page number."
        ),
        "papers": [
            {
                "source_id": p["source_id"],
                "role": p["role"],
                "doi": p["doi"],
                "title": p["title"],
                "authors": p["authors"],
                "licence": p["licence"],
                "origin": p["origin"],
                "sha256": p["sha256_of_xml"],
                "paragraph_count": len(p["paragraphs"]),
            }
            for p in built
        ],
    }
    (out / "corpus-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    primary = next(p for p in built if p["role"] == "primary")
    secondary = next(p for p in built if p["role"] == "secondary")

    (out / "primary-material.json").write_text(
        json.dumps(
            {
                "source_id": primary["source_id"],
                "doi": primary["doi"],
                "title": primary["title"],
                "authors": primary["authors"],
                "paragraphs": primary["paragraphs"],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (out / "secondary-material.json").write_text(
        json.dumps(
            {
                "source_id": secondary["source_id"],
                "doi": secondary["doi"],
                "title": secondary["title"],
                "authors": secondary["authors"],
                "paragraphs": secondary["paragraphs"],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    # material.json = aggregate paragraph index across both papers
    (out / "material.json").write_text(
        json.dumps(
            {
                "corpus_id": manifest["corpus_id"],
                "papers": [
                    {
                        "source_id": p["source_id"],
                        "doi": p["doi"],
                        "title": p["title"],
                        "paragraphs": p["paragraphs"],
                    }
                    for p in built
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    # a case.json in the shape the runtime contract promises (/input/case.json)
    (out / "case.json").write_text(
        json.dumps(
            {
                "case_id": "local-fixture-ws2-pair",
                "challenge_id": "product-arena-10-760d1a0b",
                "fixture": True,
                "instruction": "Build an evidence-bound knowledge graph over the two supplied papers.",
                "input_dir": ".",
                "output_contract": {
                    "fields": [
                        "case_id",
                        "sources",
                        "graph",
                        "queries",
                        "evidence",
                        "run",
                        "uncertainty",
                        "quality_control",
                    ]
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    total = sum(len(p["paragraphs"]) for p in built)
    print(f"[done] {out}  papers={len(built)} paragraphs={total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
