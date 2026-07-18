# Manuscript Citation Verifier

Catches fabricated or mismatched references, uncited/orphan reference-list
entries, citations that don't actually support their claim, and produces a
hyperlinked copy of your manuscript where every in-text citation jumps to its
reference-list entry.

## What it checks

1. **Extraction & cross-matching** — every in-text `(Author, Year)` citation
   is matched against the reference list.
   - *Orphan citations*: cited in text, no matching reference entry — the
     strongest fabrication signal.
   - *Orphan references*: listed but never cited — a common manuscript-hygiene
     issue reviewers flag.
2. **CrossRef fabrication check** — every reference is queried against the
   free CrossRef API. `NO_MATCH` is a flag for manual review, not proof of
   fabrication (theses, reports, and some regional journals aren't indexed).
3. **Zotero cross-check** *(optional)* — confirms the reference is actually in
   *your* Zotero library, not just that it exists somewhere.
4. **Claim-support check** *(optional)* — for each citation, fetches the cited
   paper's abstract (Semantic Scholar) and asks Claude whether it actually
   supports the specific claim in that sentence. Catches "real paper, wrong
   claim" — the failure mode CrossRef/Zotero checks can't see.
5. **Hyperlinked output** — a copy of your manuscript where every citation is
   an internal Word hyperlink to its reference entry.

## Setup

```bash
pip install -r requirements.txt
```

Pandoc/LibreOffice are **not** required — extraction works directly off the
`.docx` via `python-docx`.

## Run

```bash
streamlit run app.py
```

Opens at `http://localhost:8501`. Upload a `.docx` with a **"References"**
heading paragraph, click **Run verification**.

### Optional credentials (entered in the sidebar, never stored)

| Credential | Where to get it | Enables |
|---|---|---|
| Zotero API key | zotero.org/settings/keys | Step 3 |
| Zotero library ID | Same settings page (your numeric userID) | Step 3 |
| Anthropic API key | console.anthropic.com/settings/keys | Step 4 |
| Semantic Scholar API key *(optional)* | semanticscholar.org/product/api | Raises S2's strict free-tier rate limit for step 4 |

Nothing is written to disk or persisted between sessions — keys live only in
the running Streamlit session's memory.

## Project layout

```
app.py                        Streamlit UI
scripts/
  docx_utils.py                docx -> plain text
  extract_citations.py         citation/reference extraction + cross-matching
  verify_crossref.py           fabrication check via CrossRef
  verify_zotero.py             personal-library cross-check via Zotero API
  claim_support_check.py       claim-support judgment via Semantic Scholar + Claude
  link_citations.py            hyperlinked .docx output
```

Each script also runs standalone from the command line for scripting/CI use,
e.g.:

```bash
python scripts/extract_citations.py manuscript.md step1.json
python scripts/verify_crossref.py step1.json step2.json
ZOTERO_API_KEY=... ZOTERO_LIBRARY_ID=... python scripts/verify_zotero.py step2.json step3.json
ANTHROPIC_API_KEY=... python scripts/claim_support_check.py step3.json step4.json
python scripts/link_citations.py manuscript.docx manuscript_linked.docx
```//NOTE: the CLI path for extract_citations.py expects markdown (pandoc -t markdown file.docx > manuscript.md); the Streamlit app bypasses this via docx_utils.py.

## Known limitations

- Designed for **author-year** citation styles (APA/Harvard-like). Numbered
  (IEEE/Vancouver) styles aren't supported yet.
- Citation matching uses first-author surname + year; two references sharing
  both (e.g. a thesis and a related journal paper by the same first author,
  same year) are disambiguated heuristically — check the "Author-year
  collisions" notice if one appears.
- Hyperlinking only auto-links citations that fall entirely within a single
  Word XML run. Heavily tracked-changes documents can fragment runs; unlinked
  citations are reported so you can fix those spots by hand.
- CrossRef and Semantic Scholar don't index everything — theses, reports, and
  some regional journals will show as unmatched even when genuine.
