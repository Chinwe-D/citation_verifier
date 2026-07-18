#!/usr/bin/env python3
"""
verify_zotero.py

Step 3 of the citation-verification pipeline.

Cross-checks every reference entry against the user's own Zotero library
via the Zotero Web API (https://www.zotero.org/support/dev/web_api/v3/start).
This answers a different question than CrossRef: not "does this source
exist anywhere in the world" but "do I actually have this source in my
library" -- useful for catching citations that snuck in from an LLM
drafting pass without the author ever having read/saved the paper.

Requires two environment variables (never hardcode these):
    ZOTERO_API_KEY      -- from https://www.zotero.org/settings/keys
    ZOTERO_LIBRARY_ID   -- your numeric userID (same page) for a personal
                            library, or a group ID for a shared library
    ZOTERO_LIBRARY_TYPE -- "user" (default) or "group"

Usage:
    export ZOTERO_API_KEY="..."
    export ZOTERO_LIBRARY_ID="..."
    python verify_zotero.py step2_output.json step3_output.json
"""
import os
import sys
import json
import time
import urllib.request
import urllib.parse
import re

ZOTERO_API_BASE = "https://api.zotero.org"


def zotero_search(query: str, api_key: str, library_id: str, library_type: str, retries=2):
    """Search the user's Zotero library for items matching `query`."""
    library_type_path = "users" if library_type == "user" else "groups"
    params = urllib.parse.urlencode({"q": query, "qmode": "everything", "limit": 5})
    url = f"{ZOTERO_API_BASE}/{library_type_path}/{library_id}/items?{params}"
    req = urllib.request.Request(url, headers={
        "Zotero-API-Key": api_key,
        "Zotero-API-Version": "3",
    })
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 403:
                raise RuntimeError(
                    "Zotero returned 403 Forbidden -- check ZOTERO_API_KEY has "
                    "library read access, and ZOTERO_LIBRARY_ID / TYPE are correct."
                )
            if attempt == retries:
                return {"error": f"HTTP {e.code}"}
            time.sleep(1.5)
        except Exception as e:
            if attempt == retries:
                return {"error": str(e)}
            time.sleep(1.5)


def extract_title_guess(raw: str) -> str:
    m = re.search(r"\(\d{4}[a-z]?(?:/\d{4})?\)\.\s*(.+?)\.\s", raw + " ")
    return m.group(1) if m else raw


def title_overlap(a: str, b: str) -> float:
    stop = {"the", "a", "an", "of", "and", "in", "for", "to", "on", "with", "using"}
    ta = {w for w in re.findall(r"[a-z0-9]+", a.lower()) if w not in stop and len(w) > 2}
    tb = {w for w in re.findall(r"[a-z0-9]+", b.lower()) if w not in stop and len(w) > 2}
    if not ta:
        return 0.0
    return len(ta & tb) / len(ta)


def assess_entry(ref, api_key, library_id, library_type):
    title_guess = extract_title_guess(ref["raw"])
    search_query = ref.get("first_author_surname") or title_guess
    items = zotero_search(search_query, api_key, library_id, library_type)

    if isinstance(items, dict) and "error" in items:
        return {**ref, "zotero_status": "LOOKUP_FAILED", "zotero_detail": items["error"]}

    if not items:
        return {
            **ref,
            "zotero_status": "NOT_IN_LIBRARY",
            "zotero_detail": "No matching item found in your Zotero library -- "
                              "you may have read this source without saving it, "
                              "or this citation may not be genuinely yours to cite.",
        }

    best, best_score = None, 0.0
    for it in items:
        data = it.get("data", {})
        title = data.get("title", "")
        score = title_overlap(title_guess, title)
        if score > best_score:
            best, best_score = data, score

    if best_score >= 0.5:
        return {
            **ref,
            "zotero_status": "IN_LIBRARY",
            "zotero_detail": f"Matched '{best.get('title')}' in your Zotero library.",
            "zotero_match_score": round(best_score, 2),
        }
    else:
        return {
            **ref,
            "zotero_status": "WEAK_MATCH",
            "zotero_detail": f"Closest Zotero item ('{best.get('title') if best else 'none'}') "
                              f"only partially overlaps -- verify this is really the same source.",
            "zotero_match_score": round(best_score, 2),
        }


def main():
    if len(sys.argv) != 3:
        print("Usage: python verify_zotero.py step2_output.json step3_output.json")
        sys.exit(1)

    api_key = os.environ.get("ZOTERO_API_KEY")
    library_id = os.environ.get("ZOTERO_LIBRARY_ID")
    library_type = os.environ.get("ZOTERO_LIBRARY_TYPE", "user")

    if not api_key or not library_id:
        print("ERROR: set ZOTERO_API_KEY and ZOTERO_LIBRARY_ID environment variables first.")
        print('  export ZOTERO_API_KEY="your_key_here"')
        print('  export ZOTERO_LIBRARY_ID="your_numeric_userID"')
        sys.exit(1)

    in_path, out_path = sys.argv[1], sys.argv[2]
    data = json.load(open(in_path, encoding="utf-8"))

    results = []
    for ref in data["crossref_verification"]:
        result = assess_entry(ref, api_key, library_id, library_type)
        results.append(result)
        print(f"{result['zotero_status']:15s} | {ref['raw'][:70]}")
        time.sleep(0.2)

    out = dict(data)
    out["zotero_verification"] = results
    out["zotero_summary"] = {
        "in_library": sum(1 for r in results if r["zotero_status"] == "IN_LIBRARY"),
        "weak_match": sum(1 for r in results if r["zotero_status"] == "WEAK_MATCH"),
        "not_in_library": sum(1 for r in results if r["zotero_status"] == "NOT_IN_LIBRARY"),
        "lookup_failed": sum(1 for r in results if r["zotero_status"] == "LOOKUP_FAILED"),
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)

    print("\nZotero summary:", out["zotero_summary"])


if __name__ == "__main__":
    main()
