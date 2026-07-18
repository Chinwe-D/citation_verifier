"""
Manuscript Citation Verifier
============================
Streamlit UI wrapping the citation-verification pipeline:
  1. Extract in-text citations + reference list, cross-match them
  2. Verify each reference against CrossRef (fabrication check)
  3. Optionally cross-check against your Zotero library
  4. Optionally check whether each citation actually supports its claim
     (Semantic Scholar abstract + Claude judgment)
  5. Produce a hyperlinked copy of the manuscript (citation -> reference)

Run locally:    streamlit run app.py
API keys are entered in the sidebar and kept only in the browser session --
never written to disk, never hardcoded.
"""
import os
import sys
import tempfile
import time

import streamlit as st
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "scripts"))

from docx_utils import docx_to_text
from extract_citations import split_references, parse_reference_entries, find_intext_citations, cross_match
import verify_crossref
import verify_zotero
import claim_support_check
import link_citations


st.set_page_config(page_title="Manuscript Citation Verifier", layout="wide")
st.title("📚 Manuscript Citation Verifier")
st.caption(
    "Catches fabricated references, uncited/orphan entries, citations that don't "
    "actually support their claim, and produces a hyperlinked manuscript. "
    "Nothing you upload or enter here is stored once you close this tab."
)

# ---------------------------------------------------------------------------
# Sidebar: optional credentials
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("Optional checks")

    st.subheader("Zotero (confirm you hold each source)")
    zotero_key = st.text_input("Zotero API key", type="password", help="zotero.org/settings/keys")
    zotero_lib = st.text_input("Zotero library ID", help="Your numeric userID, same settings page")
    zotero_type = st.selectbox("Library type", ["user", "group"], index=0)

    st.divider()
    st.subheader("Claim-support check (Claude + Semantic Scholar)")
    anthropic_key = st.text_input("Anthropic API key", type="password", help="console.anthropic.com/settings/keys")
    s2_key = st.text_input("Semantic Scholar API key (optional)", type="password",
                            help="Raises S2's strict free rate limit. Get one free at semanticscholar.org/product/api")
    run_claim_check = st.checkbox("Run claim-support check", value=False,
                                   help="Slower (~3s/citation) -- fetches abstracts and asks Claude to judge support.")
    max_claims = st.number_input("Max citations to claim-check", min_value=1, max_value=200, value=15,
                                  help="Caps API usage/cost on first run.")

# ---------------------------------------------------------------------------
# Upload + run
# ---------------------------------------------------------------------------
uploaded = st.file_uploader("Upload manuscript (.docx)", type=["docx"])

run = st.button("🔍 Run verification", type="primary", disabled=uploaded is None)

if run and uploaded is not None:
    tmp_dir = tempfile.mkdtemp()
    docx_path = os.path.join(tmp_dir, "manuscript.docx")
    with open(docx_path, "wb") as f:
        f.write(uploaded.getbuffer())

    progress = st.progress(0.0, text="Extracting citations and reference list...")

    # --- Step 1: extract ---------------------------------------------------
    text = docx_to_text(docx_path)
    body, ref_block = split_references(text)
    if not ref_block.strip():
        st.error("Couldn't find a 'References' heading in this document. "
                 "Make sure the reference list has its own heading paragraph (e.g. 'References').")
        st.stop()

    ref_entries = parse_reference_entries(ref_block)
    citations = find_intext_citations(body)
    orphan_citations, orphan_references = cross_match(citations, ref_entries)
    progress.progress(0.15, text=f"Found {len(ref_entries)} references, {len(citations)} in-text citations.")

    # --- Step 2: CrossRef ---------------------------------------------------
    crossref_results = []
    for i, ref in enumerate(ref_entries):
        crossref_results.append(verify_crossref.assess_entry(ref))
        progress.progress(0.15 + 0.35 * (i + 1) / max(len(ref_entries), 1),
                           text=f"Checking CrossRef ({i+1}/{len(ref_entries)})...")
        time.sleep(0.15)

    # --- Step 3: Zotero (optional) -----------------------------------------
    zotero_results = None
    if zotero_key and zotero_lib:
        zotero_results = []
        for i, ref in enumerate(crossref_results):
            zotero_results.append(verify_zotero.assess_entry(ref, zotero_key, zotero_lib, zotero_type))
            progress.progress(0.5 + 0.15 * (i + 1) / max(len(crossref_results), 1),
                               text=f"Checking your Zotero library ({i+1}/{len(crossref_results)})...")
            time.sleep(0.1)
    else:
        progress.progress(0.65, text="Skipping Zotero check (no credentials provided).")

    # --- Step 4: claim-support (optional) -----------------------------------
    claim_results = None
    if run_claim_check and anthropic_key:
        if s2_key:
            os.environ["SEMANTIC_SCHOLAR_API_KEY"] = s2_key
        claim_results = []
        subset = citations[:max_claims]
        for i, c in enumerate(subset):
            paper = claim_support_check.fetch_abstract(f"{c['surname']} {c['year']}")
            if not paper:
                claim_results.append({**c, "verdict": "NO_ABSTRACT_FOUND",
                                       "reasoning": "Could not retrieve an abstract -- verify manually."})
            else:
                verdict = claim_support_check.ask_claude_claim_support(
                    c["sentence"], c["raw"], paper["abstract"], anthropic_key
                )
                claim_results.append({**c, "matched_paper_title": paper.get("title"), **verdict})
            progress.progress(0.65 + 0.25 * (i + 1) / max(len(subset), 1),
                               text=f"Checking claim support ({i+1}/{len(subset)})...")
            time.sleep(1.0)
    else:
        progress.progress(0.9, text="Skipping claim-support check.")

    # --- Step 5: hyperlinked output -----------------------------------------
    linked_path = os.path.join(tmp_dir, "manuscript_linked.docx")
    try:
        doc = link_citations.Document(docx_path)
        ref_start_idx = link_citations.find_references_section(doc)
        ambiguity_report = []
        ref_keys = link_citations.bookmark_references(doc, ref_start_idx, ambiguity_report)
        unlinked_report = []
        for p in doc.paragraphs[:ref_start_idx]:
            link_citations.link_citations_in_paragraph(p, ref_keys, unlinked_report)
        doc.save(linked_path)
    except Exception as e:
        linked_path = None
        ambiguity_report, unlinked_report = [], [f"Linking failed: {e}"]

    progress.progress(1.0, text="Done.")
    progress.empty()

    st.session_state["results"] = {
        "ref_entries": ref_entries,
        "citations": citations,
        "orphan_citations": orphan_citations,
        "orphan_references": orphan_references,
        "crossref_results": crossref_results,
        "zotero_results": zotero_results,
        "claim_results": claim_results,
        "ambiguity_report": ambiguity_report,
        "unlinked_report": unlinked_report,
        "linked_path": linked_path,
    }

# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------
if "results" in st.session_state:
    r = st.session_state["results"]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("References found", len(r["ref_entries"]))
    c2.metric("In-text citations found", len(r["citations"]))
    c3.metric("Orphan citations", len(r["orphan_citations"]),
              help="Cited in text but no matching reference entry -- strongest fabrication signal.")
    c4.metric("Orphan references", len(r["orphan_references"]),
              help="Listed but never cited in the text.")

    tab_labels = ["Fabrication check", "Orphans", "Zotero", "Claim support", "Download"]
    tabs = st.tabs(tab_labels)

    with tabs[0]:
        st.subheader("CrossRef verification")
        status_color = {"VERIFIED": "🟢", "PARTIAL_MATCH": "🟡", "NO_MATCH": "🔴", "LOOKUP_FAILED": "⚪"}
        rows = []
        for res in r["crossref_results"]:
            rows.append({
                "Status": f"{status_color.get(res['status'], '')} {res['status']}",
                "Reference": res["raw"][:90] + ("..." if len(res["raw"]) > 90 else ""),
                "CrossRef title match": (res.get("best_match") or {}).get("title", ""),
                "DOI": (res.get("best_match") or {}).get("doi", ""),
                "Detail": res["detail"],
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        n_flagged = sum(1 for res in r["crossref_results"] if res["status"] in ("NO_MATCH", "PARTIAL_MATCH"))
        if n_flagged:
            st.warning(f"{n_flagged} reference(s) need manual review -- see 🟡/🔴 rows above. "
                       "NO_MATCH is common for theses/reports not indexed by CrossRef; verify manually before assuming fabrication.")
        else:
            st.success("All references resolved cleanly against CrossRef.")

    with tabs[1]:
        col1, col2 = st.columns(2)
        with col1:
            st.subheader("Orphan citations (cited, no reference entry)")
            if r["orphan_citations"]:
                st.dataframe(pd.DataFrame(r["orphan_citations"])[["surname", "year", "sentence"]],
                             use_container_width=True, hide_index=True)
            else:
                st.success("None -- every in-text citation has a matching reference.")
        with col2:
            st.subheader("Orphan references (listed, never cited)")
            if r["orphan_references"]:
                st.dataframe(pd.DataFrame(r["orphan_references"])[["first_author_surname", "year", "raw"]],
                             use_container_width=True, hide_index=True)
            else:
                st.success("None -- every reference is cited somewhere in the text.")

        if r["ambiguity_report"]:
            st.subheader("Author-year collisions")
            for line in r["ambiguity_report"]:
                st.info(line)

    with tabs[2]:
        st.subheader("Zotero cross-check")
        if r["zotero_results"] is None:
            st.info("Add your Zotero API key and library ID in the sidebar, then re-run, to confirm you hold each source.")
        else:
            zstatus_color = {"IN_LIBRARY": "🟢", "WEAK_MATCH": "🟡", "NOT_IN_LIBRARY": "🔴", "LOOKUP_FAILED": "⚪"}
            rows = [{
                "Status": f"{zstatus_color.get(res['zotero_status'], '')} {res['zotero_status']}",
                "Reference": res["raw"][:90],
                "Detail": res["zotero_detail"],
            } for res in r["zotero_results"]]
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    with tabs[3]:
        st.subheader("Claim-support check")
        if r["claim_results"] is None:
            st.info("Enable the claim-support check in the sidebar (needs an Anthropic API key), then re-run, "
                    "to see whether each citation actually supports the sentence it's attached to.")
        else:
            vcolor = {"SUPPORTED": "🟢", "PARTIALLY_SUPPORTED": "🟡", "UNSUPPORTED": "🔴",
                      "UNCLEAR": "⚪", "NO_ABSTRACT_FOUND": "⚪", "CHECK_FAILED": "⚪"}
            rows = [{
                "Verdict": f"{vcolor.get(res.get('verdict'), '')} {res.get('verdict')}",
                "Citation": f"{res['surname']} ({res['year']})",
                "Sentence": res["sentence"][:150] + ("..." if len(res["sentence"]) > 150 else ""),
                "Reasoning": res.get("reasoning", ""),
            } for res in r["claim_results"]]
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            n_unsupported = sum(1 for res in r["claim_results"] if res.get("verdict") == "UNSUPPORTED")
            if n_unsupported:
                st.warning(f"{n_unsupported} citation(s) flagged as UNSUPPORTED by their own abstract -- review these first.")

    with tabs[4]:
        st.subheader("Hyperlinked manuscript")
        if r["linked_path"] and os.path.exists(r["linked_path"]):
            with open(r["linked_path"], "rb") as f:
                st.download_button("⬇️ Download hyperlinked .docx", f, file_name="manuscript_verified_linked.docx")
            st.caption("Every in-text citation links to its reference-list entry -- click to jump there in Word.")
        else:
            st.error("Could not produce the hyperlinked document for this file.")
        if r["unlinked_report"]:
            with st.expander(f"{len(r['unlinked_report'])} citation(s) could not be auto-linked"):
                for line in r["unlinked_report"]:
                    st.write("-", line)
