#!/usr/bin/env python3
"""
verify_crossref.py

Step 2 of the citation-verification pipeline.

Takes the JSON produced by extract_citations.py and, for every reference
entry, queries the free CrossRef API to check whether a matching scholarly
work actually exists. This is the core "fabricated reference" check.

CrossRef doesn't index everything (PhD theses, some reports, some
non-journal sources are often missing), so a NO_MATCH is a *flag for human
review*, not automatic proof of fabrication -- the report says so
explicitly for each entry.

Usage:
    python verify_crossref.py step1_output.json step2_output.json
"""
import sys
import json
import time
import urllib.request
import urllib.parse
import re

CROSSREF_API = "https://api.crossref.org/works"
# CrossRef asks for a contact email in the User-Agent as etiquette (the
# "polite pool" -- higher, more reliable rate limits). Not required.
USER_AGENT = "citation-verifier/1.0 (mailto:example@example.com)"


def query_crossref(bibliographic_string: str, retries=2):
    params = urllib.parse.urlencode({
        "query.bibliographic": bibliographic_string,
        "rows": 3,
    })
    url = f"{CROSSREF_API}?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("message", {}).get("items", [])
        except Exception as e:
            if attempt == retries:
                return {"error": str(e)}
            time.sleep(1.5)


def extract_title_guess(raw: str) -> str:
    """
    Pull the title-like segment out of a raw reference string: the text
    after '(YYYY).' up to the next sentence-ending period. Falls back to
    the full string if the pattern isn't found (e.g. no parenthetical year).
    """
    m = re.search(r"\(\d{4}[a-z]?(?:/\d{4})?\)\.\s*(.+?)\.\s", raw + " ")
    return m.group(1) if m else raw


def token_overlap_score(a_title_guess: str, b_title: str) -> float:
    """Fraction of significant words in the (guessed) title also in CrossRef's title."""
    stop = {"the", "a", "an", "of", "and", "in", "for", "to", "on", "with", "using"}
    ta = {w for w in re.findall(r"[a-z0-9]+", a_title_guess.lower()) if w not in stop and len(w) > 2}
    tb = {w for w in re.findall(r"[a-z0-9]+", b_title.lower()) if w not in stop and len(w) > 2}
    if not ta:
        return 0.0
    return len(ta & tb) / len(ta)


def assess_entry(ref):
    raw = ref["raw"]
    title_guess = extract_title_guess(raw)
    items = query_crossref(raw)

    if isinstance(items, dict) and "error" in items:
        return {**ref, "status": "LOOKUP_FAILED", "detail": items["error"], "best_match": None}

    if not items:
        return {
            **ref,
            "status": "NO_MATCH",
            "detail": "No CrossRef record found. Common for PhD theses, technical "
                      "reports, or older/regional-journal papers -- verify manually "
                      "(Google Scholar / Zotero) before treating as fabricated.",
            "best_match": None,
        }

    best = max(items, key=lambda it: token_overlap_score(title_guess, it.get("title", [""])[0] if it.get("title") else ""))
    best_title = best.get("title", [""])[0] if best.get("title") else ""
    score = token_overlap_score(title_guess, best_title)

    best_match = {
        "title": best_title,
        "doi": best.get("DOI"),
        "year": (best.get("issued", {}).get("date-parts", [[None]])[0][0]),
        "container": (best.get("container-title", [""])[0] if best.get("container-title") else None),
        "authors": [
            f"{a.get('family','')}" for a in best.get("author", [])
        ] if best.get("author") else [],
        "overlap_score": round(score, 2),
    }

    if score >= 0.6:
        status = "VERIFIED"
        detail = "Strong title match found in CrossRef."
    elif score >= 0.3:
        status = "PARTIAL_MATCH"
        detail = "A related record was found but title overlap is only partial -- check author/year/title carefully, this can indicate a misremembered or slightly altered citation."
    else:
        status = "NO_MATCH"
        detail = "CrossRef records were returned but none resemble this reference -- check manually before treating as fabricated (theses/reports are often absent from CrossRef)."

    return {**ref, "status": status, "detail": detail, "best_match": best_match}


def main():
    if len(sys.argv) != 3:
        print("Usage: python verify_crossref.py step1_output.json step2_output.json")
        sys.exit(1)

    in_path, out_path = sys.argv[1], sys.argv[2]
    data = json.load(open(in_path, encoding="utf-8"))

    results = []
    for ref in data["reference_entries"]:
        result = assess_entry(ref)
        results.append(result)
        print(f"{result['status']:15s} | {ref['raw'][:70]}")
        time.sleep(0.3)  # be polite to the free API

    out = dict(data)
    out["crossref_verification"] = results
    out["summary"] = {
        "verified": sum(1 for r in results if r["status"] == "VERIFIED"),
        "partial_match": sum(1 for r in results if r["status"] == "PARTIAL_MATCH"),
        "no_match": sum(1 for r in results if r["status"] == "NO_MATCH"),
        "lookup_failed": sum(1 for r in results if r["status"] == "LOOKUP_FAILED"),
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)

    print("\nSummary:", out["summary"])


if __name__ == "__main__":
    main()
