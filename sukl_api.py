"""
Shared SUKL API functions used by both sukl_fetch.py (CLI) and sukl_mcp_server.py (MCP).
"""

import io
import re
import requests
import pdfplumber

BASE_URL = "https://prehledy.sukl.cz/dlp/v1"


def fetch_basic_info(kod: str) -> dict:
    r = requests.get(f"{BASE_URL}/lecive-pripravky/{kod}", timeout=30)
    r.raise_for_status()
    return r.json()


def fetch_reimbursement(kod: str):
    r = requests.get(f"{BASE_URL}/cau-scau/{kod}", timeout=30)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def fetch_doc_metadata(kod: str):
    r = requests.get(f"{BASE_URL}/dokumenty-metadata/{kod}", timeout=30)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def get_spc_url(metadata) -> str | None:
    if not metadata:
        return None
    docs = metadata if isinstance(metadata, list) else metadata.get("documents", [])
    for doc in docs:
        typ = doc.get("typ") or doc.get("type", "")
        if typ.upper() == "SPC":
            return doc.get("link") or doc.get("url")
    return None


def download_pdf_text(url: str) -> str:
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    text_pages = []
    with pdfplumber.open(io.BytesIO(r.content)) as pdf:
        for page in pdf.pages:
            t = page.extract_text()
            if t:
                text_pages.append(t)
    return "\n".join(text_pages)


def extract_section_4_2(text: str) -> str:
    text = re.sub(r"\r\n", "\n", text)

    start_patterns = [
        r"4\.2\s+Dávkování\s+a\s+způsob\s+podání",
        r"4\.2\s+Dávkování",
        r"4\.2\b",
    ]
    end_patterns = [
        r"4\.3\s+Kontraindikace",
        r"4\.3\b",
    ]

    start_match = None
    for pat in start_patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            start_match = m
            break

    if not start_match:
        return "(Sekce 4.2 nebyla v textu PDF nalezena)"

    section_text = text[start_match.start():]

    end_match = None
    for pat in end_patterns:
        m = re.search(pat, section_text[10:], re.IGNORECASE)
        if m:
            end_match = m
            break

    if end_match:
        section_text = section_text[: end_match.start() + 10]

    return section_text.strip()


def fetch_latest_period() -> str:
    r = requests.get(f"{BASE_URL}/historicke-davky", timeout=30)
    r.raise_for_status()
    return r.json()[-1]


def fetch_product_codes(period: str, typ_seznamu: str = "scau") -> list[str]:
    """Returns list of SUKL code strings for the given period and list type."""
    r = requests.get(
        f"{BASE_URL}/lecive-pripravky",
        params={"typSeznamu": typ_seznamu, "obdobi": period},
        timeout=120,
    )
    r.raise_for_status()
    data = r.json()
    if isinstance(data, list):
        return data
    # Defensive fallback if API wraps in an object
    for key in ("data", "content", "items", "lecivePripravky"):
        if key in data:
            return data[key]
    return []
