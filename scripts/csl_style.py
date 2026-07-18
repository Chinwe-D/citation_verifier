#!/usr/bin/env python3
"""
csl_style.py

Looks up a citation style BY NAME against Zotero's public CSL style catalog
(https://www.zotero.org/styles-files/styles.json -- ~10,850 styles, no
account or API key needed, this is a public open catalog, not personal
Zotero data) and returns which extraction family to use: "numbered"
(CSL format "numeric") or "author_year" (CSL format "author-date").

This replaces guessing the style from the manuscript's own reference-list
formatting with asking Zotero's authoritative catalog what a *named* style
(the one the target journal actually requires) uses. CSL styles with
format "note" (footnote-based, common in humanities/law) or "label"
(rare) aren't yet supported by the extraction/hyperlinking pipeline --
resolve_style() reports this explicitly so the app can ask for a manual
fallback instead of guessing.

Usage:
    from csl_style import fetch_style_index, search_styles, resolve_style
    index = fetch_style_index()
    matches = search_styles("vancouver", index)
    result = resolve_style("IEEE", index)
    # {'matched': True, 'title': 'IEEE', 'csl_format': 'numeric',
    #  'pipeline_style': 'numbered', 'supported': True}
"""
import json
import re
import urllib.request

STYLES_INDEX_URL = "https://www.zotero.org/styles-files/styles.json"

FORMAT_TO_PIPELINE_STYLE = {
    "numeric": "numbered",
    "author-date": "author_year",
    # "note" (footnote/endnote styles) and "label" are real CSL categories
    # this pipeline doesn't yet extract/hyperlink -- surfaced as unsupported.
}


def fetch_style_index(timeout=20):
    """Fetch the full public style catalog. ~2.9MB, ~10,850 styles. Cache the
    result in the caller (e.g. st.cache_data) rather than refetching per run."""
    req = urllib.request.Request(STYLES_INDEX_URL, headers={"User-Agent": "citation-verifier/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def search_styles(query: str, index: list, limit: int = 8):
    """
    Search the style catalog by name. Ranks exact matches first, then
    prefix matches, then substring matches on title/titleShort/name.
    """
    q = query.strip().lower()
    if not q:
        return []

    exact, prefix, substring = [], [], []
    for s in index:
        title = s.get("title", "")
        short = s.get("titleShort", "") or ""
        name = s.get("name", "")
        haystacks = [title.lower(), short.lower(), name.lower().replace("-", " ")]

        if q in haystacks:
            exact.append(s)
        elif any(h.startswith(q) for h in haystacks):
            prefix.append(s)
        elif any(q in h for h in haystacks):
            substring.append(s)

    ranked = exact + prefix + substring
    return ranked[:limit]


def resolve_style(query: str, index: list):
    """
    Resolve a style name to a pipeline style ("numbered" | "author_year") or
    report that it's unsupported/unmatched. Takes the top search match.
    """
    matches = search_styles(query, index, limit=1)
    if not matches:
        return {"matched": False, "title": None, "csl_format": None,
                "pipeline_style": None, "supported": False}

    top = matches[0]
    csl_format = top.get("categories", {}).get("format")
    pipeline_style = FORMAT_TO_PIPELINE_STYLE.get(csl_format)

    return {
        "matched": True,
        "title": top.get("title"),
        "name": top.get("name"),
        "csl_format": csl_format,
        "pipeline_style": pipeline_style,
        "supported": pipeline_style is not None,
    }
