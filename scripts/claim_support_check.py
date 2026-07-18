#!/usr/bin/env python3
"""
claim_support_check.py

Step 4 of the citation-verification pipeline -- the part that catches
"real paper, wrong claim": a citation that resolves to a genuine source
(so CrossRef/Zotero checks pass) but doesn't actually say what the
sentence next to it claims.

For each in-text citation + its sentence:
  1. Fetch the cited paper's abstract from Semantic Scholar (free, no key).
  2. Ask Claude to judge, strictly from the abstract: does it plausibly
     support the specific claim in the sentence?

Requires:
    ANTHROPIC_API_KEY   -- https://console.anthropic.com/settings/keys

Usage:
    export ANTHROPIC_API_KEY="..."
    python claim_support_check.py step3_output.json step4_output.json
"""
import os
import sys
import json
import time
import urllib.request
import urllib.parse

S2_API = "https://api.semanticscholar.org/graph/v1/paper/search"
ANTHROPIC_API = "https://api.anthropic.com/v1/messages"
MODEL = "claude-sonnet-4-6"


def fetch_abstract(query: str, retries=3):
    params = urllib.parse.urlencode({
        "query": query,
        "fields": "title,abstract,year",
        "limit": 1,
    })
    url = f"{S2_API}?{params}"
    headers = {"User-Agent": "citation-verifier/1.0"}
    s2_key = os.environ.get("SEMANTIC_SCHOLAR_API_KEY")
    if s2_key:
        headers["x-api-key"] = s2_key
    req = urllib.request.Request(url, headers=headers)
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                papers = data.get("data", [])
                if papers and papers[0].get("abstract"):
                    return papers[0]
                return None
        except Exception:
            if attempt == retries:
                return None
            # S2's unauthenticated tier rate-limits hard (~1 req/sec, with
            # bursty 429s) -- back off with increasing delay. Get a free
            # key at https://www.semanticscholar.org/product/api to raise
            # the limit and set SEMANTIC_SCHOLAR_API_KEY.
            time.sleep(3 * (attempt + 1))


def ask_claude_claim_support(sentence: str, citation_raw: str, abstract: str, api_key: str):
    prompt = f"""You are checking academic citation accuracy. Below is a sentence from a
manuscript, the citation it uses, and the abstract of the cited paper.

Sentence: "{sentence}"
Citation used: {citation_raw}
Abstract of cited paper: "{abstract}"

Judge strictly from the abstract alone (you do not have the full paper).
Respond with ONLY a JSON object, no other text:
{{"verdict": "SUPPORTED" | "PARTIALLY_SUPPORTED" | "UNSUPPORTED" | "UNCLEAR",
  "reasoning": "one sentence explaining why"}}

SUPPORTED = the abstract clearly backs the specific claim.
PARTIALLY_SUPPORTED = the abstract is related and plausible but doesn't confirm the specific detail claimed.
UNSUPPORTED = the abstract contradicts or has nothing to do with the claim.
UNCLEAR = the abstract doesn't give enough information to judge either way."""

    body = json.dumps({
        "model": MODEL,
        "max_tokens": 300,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")

    req = urllib.request.Request(ANTHROPIC_API, data=body, headers={
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
            text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            return json.loads(text)
    except Exception as e:
        return {"verdict": "CHECK_FAILED", "reasoning": str(e)}


def main():
    if len(sys.argv) != 3:
        print("Usage: python claim_support_check.py step3_output.json step4_output.json")
        sys.exit(1)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: set ANTHROPIC_API_KEY environment variable first.")
        sys.exit(1)

    in_path, out_path = sys.argv[1], sys.argv[2]
    data = json.load(open(in_path, encoding="utf-8"))

    results = []
    for c in data["intext_citations"]:
        query = f"{c['surname']} {c['year']}"
        paper = fetch_abstract(query)
        if not paper:
            results.append({**c, "verdict": "NO_ABSTRACT_FOUND",
                             "reasoning": "Could not retrieve an abstract to check against -- verify manually."})
            print(f"{'NO_ABSTRACT':20s} | {c['surname']} {c['year']}")
            continue

        verdict = ask_claude_claim_support(c["sentence"], c["raw"], paper["abstract"], api_key)
        results.append({**c, "matched_paper_title": paper.get("title"), **verdict})
        print(f"{verdict.get('verdict', '?'):20s} | {c['surname']} {c['year']}")
        time.sleep(2.5)  # respect S2's aggressive free-tier rate limit + Anthropic pacing

    out = dict(data)
    out["claim_support_check"] = results
    out["claim_support_summary"] = {
        v: sum(1 for r in results if r.get("verdict") == v)
        for v in ["SUPPORTED", "PARTIALLY_SUPPORTED", "UNSUPPORTED", "UNCLEAR", "NO_ABSTRACT_FOUND", "CHECK_FAILED"]
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)

    print("\nClaim-support summary:", out["claim_support_summary"])


if __name__ == "__main__":
    main()
