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


def detect_citation_style(ref_block: str) -> str:
    """
    Sniff whether the reference list is numbered (IEEE/Vancouver, e.g.
    '[1] Smith, J. ...' or '1. Smith, J. ...') or author-year (APA/Harvard,
    e.g. 'Smith, J. (2020) ...'). Checks the first several entries and
    returns whichever pattern matches more of them.
    """
    raw_entries = [p.strip() for p in re.split(r"\n\s*\n", ref_block) if p.strip()][:8]
    numbered_pat = re.compile(r"^\s*\[?\d{1,3}\]?[\.\):]?\s+\S")
    year_paren_pat = re.compile(r"\(\d{4}[a-z]?\)")

    n_numbered = sum(1 for e in raw_entries if numbered_pat.match(e))
    n_author_year = sum(1 for e in raw_entries if year_paren_pat.search(e[:200]))

    if n_numbered >= max(2, len(raw_entries) // 2) and n_numbered >= n_author_year:
        return "numbered"
    return "author_year"


def parse_reference_entries_numbered(ref_block: str):
    """
    Parse a numbered reference list ('[1] ...' or '1. ...' per paragraph)
    into entries carrying their reference number.
    """
    ref_block = ref_block.replace("\\'", "'").replace('\\"', '"')
    raw_entries = [p.strip() for p in re.split(r"\n\s*\n", ref_block) if p.strip()]

    num_pat = re.compile(r"^\s*\[?(\d{1,3})\]?[\.\):]?\s+(.*)$", re.DOTALL)

    entries = []
    for raw in raw_entries:
        clean = re.sub(r"\s+", " ", raw).strip()
        if len(clean) < 10:
            continue
        m = num_pat.match(clean)
        if not m:
            continue  # not a numbered entry -- skip stray fragments/continuation lines
        number, rest = int(m.group(1)), m.group(2).strip()
        entries.append({
            "raw": rest,
            "number": number,
            "first_author_surname": None,
            "year": None,
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


def find_intext_citations_numbered(body: str):
    """
    Find bracketed/parenthetical numbered citations: [1], [2,3], [4-6],
    (1), (2, 5). Each individual number within a group (including expanded
    ranges) becomes its own citation entry, all sharing the same sentence.

    Note: this cannot detect *unbracketed* superscript numeral citations --
    plain-text extraction can't distinguish a superscript reference number
    from an ordinary digit in running text. If your journal uses bare
    superscript numbers with no brackets, this pipeline won't see them;
    everything else (fabrication check, hyperlinking) still works once you
    manually confirm which reference each superscript points to.
    """
    text = re.sub(r"\s+", " ", body)
    citations = []
    seen = set()

    group_pat = re.compile(r"[\[\(]\s*(\d{1,3}(?:\s*[-,–]\s*\d{1,3})*)\s*[\]\)]")

    for m in group_pat.finditer(text):
        group = m.group(1)
        sent = sentence_around(text, m.start(), m.end())
        numbers = []
        for part in re.split(r",\s*", group):
            part = part.strip()
            if "-" in part or "–" in part:
                a, b = re.split(r"[-–]", part)
                a, b = int(a), int(b)
                if 0 < b - a < 50:  # sanity bound against false-positive ranges
                    numbers.extend(range(a, b + 1))
            elif part.isdigit():
                numbers.append(int(part))

        for n in numbers:
            key = (n, sent[:60])
            if key in seen:
                continue
            seen.add(key)
            citations.append({"raw": m.group(0), "number": n, "sentence": sent})

    return citations


def cross_match_numbered(citations, ref_entries):
    ref_numbers = {r["number"] for r in ref_entries}
    cited_numbers = set()

    orphan_citations = []
    for c in citations:
        cited_numbers.add(c["number"])
        if c["number"] not in ref_numbers:
            orphan_citations.append(c)

    orphan_references = [r for r in ref_entries if r["number"] not in cited_numbers]

    return orphan_citations, orphan_references


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


def extract(text: str, style: str = "auto"):
    """
    Unified entry point used by the app: splits text, detects or accepts a
    citation style, and returns a consistent result dict regardless of style.
    style: "auto" | "author_year" | "numbered"
    """
    body, ref_block = split_references(text)
    if not ref_block.strip():
        return {"style": None, "ref_entries": [], "citations": [],
                "orphan_citations": [], "orphan_references": [], "no_references_found": True}

    resolved_style = detect_citation_style(ref_block) if style == "auto" else style

    if resolved_style == "numbered":
        ref_entries = parse_reference_entries_numbered(ref_block)
        citations = find_intext_citations_numbered(body)
        orphan_c, orphan_r = cross_match_numbered(citations, ref_entries)
    else:
        resolved_style = "author_year"
        ref_entries = parse_reference_entries(ref_block)
        citations = find_intext_citations(body)
        orphan_c, orphan_r = cross_match(citations, ref_entries)

    return {
        "style": resolved_style,
        "ref_entries": ref_entries,
        "citations": citations,
        "orphan_citations": orphan_c,
        "orphan_references": orphan_r,
        "no_references_found": False,
    }


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
