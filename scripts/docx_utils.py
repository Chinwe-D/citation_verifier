"""
docx_utils.py

Shared helper so the Streamlit app doesn't need pandoc installed on the
deployment host -- pulls plain text straight out of the docx via python-docx,
which is already a hard dependency of link_citations.py.
"""
from docx import Document


def docx_to_text(path: str) -> str:
    doc = Document(path)
    # Blank-line-separated, matching pandoc's paragraph breaks -- extract_citations.py's
    # parse_reference_entries() splits references on blank lines.
    return "\n\n".join(p.text for p in doc.paragraphs)
