#!/usr/bin/env python3
"""
extract_citations.py

Step 1 of the citation-verification pipeline.

Takes a manuscript (already converted to markdown via pandoc) and:
  1. Splits it into body text vs. the reference list.
  2. Parses the reference list into structured entries (first author
     surname, year, raw string).
  3. Finds every in-text author-year citation in the body, together with
     the sentence it sits in (so downstream claim-support checking has
     something to check against).
  4. Cross-matches in-text citations <-> reference list entries and flags:
       - in-text citations with NO matching reference entry (orphan citations
         -- a common signature of a fabricated or mis-typed citation)
       - reference entries that are never cited anywhere in the text
         (orphan references -- not necessarily a problem, but worth a look)

Output: a single JSON file with everything downstream steps need.

Usage:
    python extract_citations.py manuscript.md output.json
"""
import re
import sys
import json


def split_references(text: str):
    """Split manuscript markdown into (body, references_block)."""
    # Matches a line that is essentially just "References" (allowing for
    # markdown bold/heading markers), case-insensitive.
    pattern = re.compile(
        r"^\s*#{0,6}\s*\**\s*References?\s*\**\s*$", re.IGNORECASE | re.MULTILINE
    )
    m = pattern.search(text)
    if not m:
        return text, ""
    return text[: m.start()], text[m.end():]


def parse_reference_entries(ref_block: str):
    """
    Parse a references block into a list of entries. Assumes one reference
    per paragraph (pandoc separates paragraphs with a blank line), which
    holds for the vast majority of academic reference lists.
    """
    # Normalize pandoc's escaped punctuation
    ref_block = ref_block.replace("\\'", "'").replace('\\"', '"')

    # Split on blank lines (pandoc paragraph breaks)
    raw_entries = [p.strip() for p in re.split(r"\n\s*\n", ref_block) if p.strip()]

    entries = []
    for raw in raw_entries:
        # Collapse internal newlines/extra whitespace into single spaces
        clean = re.sub(r"\s+", " ", raw).strip()
        if len(clean) < 15:
            continue  # skip stray fragments

        # Try to pull first-author surname + year, e.g.:
        # "Adiele, J.G., Schut, A.G.T. ... (2020). Towards closing..."
        m = re.match(r"^([A-Z][A-Za-zÀ-ÿ\-']+)[,.\s].*?\((\d{4}[a-z]?(?:/\d{4})?)\)", clean)
        surname = m.group(1) if m else None
        year = m.group(2) if m else None

        entries.append({
            "raw": clean,
            "first_author_surname": surname,
            "year": year,
        })
    return entries


def sentence_around(text: str, start: int, end: int) -> str:
    """
    Return the sentence containing text[start:end], guarding against
    treating 'et al.', 'Fig.', initials (e.g. 'J.G.'), etc. as sentence
    boundaries.
    """
    abbrev_guard = re.compile(r"(et al|Fig|vs|approx|e\.g|i\.e|[A-Z])\.\s*$")

    left = start
    while left > 0:
        left -= 1
        if text[left] in ".!?" and left + 1 < len(text) and text[left + 1] == " ":
            if not abbrev_guard.search(text[max(0, left - 12):left + 1]):
                left += 2
                break
    else:
        left = 0

    right = end
    while right < len(text):
        if text[right] in ".!?":
            if not abbrev_guard.search(text[max(0, right - 12):right + 1]):
                right += 1
                break
        right += 1

    return text[left:right].strip()


def find_intext_citations(body: str):
    """
    Find author-year in-text citations of common forms:
      Author (2020) | Author et al. (2020) | Author and Author (2020)
      (Author, 2020) | (Author et al., 2020; Other, 2019)
    Matching runs on the full text (not per-sentence) so 'et al.' never
    breaks a citation apart; sentence context is recovered afterward.
    """
    text = re.sub(r"\s+", " ", body)
    citations = []
    seen = set()

    narrative_pat = re.compile(
        r"\b([A-Z][A-Za-zÀ-ÿ\-']+)(?:\s+(?:et al\.|and\s+[A-Z][A-Za-zÀ-ÿ\-']+))?\s*"
        r"\((\d{4}[a-z]?(?:/\d{4})?)\)"
    )
    parenthetical_pat = re.compile(
        r"\(([A-Z][A-Za-zÀ-ÿ\-']+(?:\s+et al\.)?,\s*\d{4}[a-z]?"
        r"(?:;\s*[A-Z][A-Za-zÀ-ÿ\-']+(?:\s+et al\.)?,\s*\d{4}[a-z]?)*)\)"
    )

    for m in narrative_pat.finditer(text):
        surname, year = m.group(1), m.group(2)
        sent = sentence_around(text, m.start(), m.end())
        key = (surname, year, sent[:60])
        if key in seen:
            continue
        seen.add(key)
        citations.append({"raw": m.group(0), "surname": surname, "year": year, "sentence": sent})

    for m in parenthetical_pat.finditer(text):
        inner = m.group(1)
        sent = sentence_around(text, m.start(), m.end())
        for part in inner.split(";"):
            part = part.strip()
            pm = re.match(r"([A-Z][A-Za-zÀ-ÿ\-']+)(?:\s+et al\.)?,\s*(\d{4}[a-z]?)", part)
            if not pm:
                continue
            surname, year = pm.group(1), pm.group(2)
            key = (surname, year, sent[:60])
            if key in seen:
                continue
            seen.add(key)
            citations.append({"raw": m.group(0), "surname": surname, "year": year, "sentence": sent})

    return citations


def cross_match(citations, ref_entries):
    ref_index = {}
    for r in ref_entries:
        if r["first_author_surname"] and r["year"]:
            # index by (surname, base year) -- strip a/b suffixes and slash-years
            base_year = re.match(r"(\d{4})", r["year"]).group(1)
            ref_index.setdefault((r["first_author_surname"], base_year), []).append(r)

    orphan_citations = []
    cited_keys = set()
    for c in citations:
        base_year = re.match(r"(\d{4})", c["year"]).group(1)
        key = (c["surname"], base_year)
        cited_keys.add(key)
        if key not in ref_index:
            orphan_citations.append(c)

    orphan_references = []
    for r in ref_entries:
        if not (r["first_author_surname"] and r["year"]):
            orphan_references.append(r)
            continue
        base_year = re.match(r"(\d{4})", r["year"]).group(1)
        if (r["first_author_surname"], base_year) not in cited_keys:
            orphan_references.append(r)

    return orphan_citations, orphan_references


def main():
    if len(sys.argv) != 3:
        print("Usage: python extract_citations.py manuscript.md output.json")
        sys.exit(1)

    md_path, out_path = sys.argv[1], sys.argv[2]
    text = open(md_path, encoding="utf-8").read()

    body, ref_block = split_references(text)
    ref_entries = parse_reference_entries(ref_block)
    citations = find_intext_citations(body)
    orphan_citations, orphan_references = cross_match(citations, ref_entries)

    result = {
        "n_reference_entries": len(ref_entries),
        "n_intext_citations_found": len(citations),
        "reference_entries": ref_entries,
        "intext_citations": citations,
        "orphan_citations": orphan_citations,       # cited but no matching reference
        "orphan_references": orphan_references,     # listed but never cited
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"References found:        {len(ref_entries)}")
    print(f"In-text citations found: {len(citations)}")
    print(f"Orphan citations (cited, no ref entry): {len(orphan_citations)}")
    print(f"Orphan references (listed, never cited): {len(orphan_references)}")


if __name__ == "__main__":
    main()
