"""
SUKL MCP Server — exposes Czech SÚKL drug database via MCP protocol.

Provides one tool:
  - sukl_drug_info(sukl_kod)  get reimbursement + dosing info for a drug

Note: The SUKL API has no text/name search endpoint. Users should look up
the 7-digit SUKL code at https://prehledy.sukl.cz/prehled_leciv.html

Usage:
  python sukl_mcp_server.py [port]   (default port: 8000)

Dependencies:
  pip install fastmcp requests pdfplumber
"""

import os
import sys

from fastmcp import FastMCP

from sukl_api import (
    download_pdf_text,
    extract_section_4_2,
    fetch_basic_info,
    fetch_doc_metadata,
    fetch_reimbursement,
    get_spc_url,
)

mcp = FastMCP("SUKL Drug Database")


@mcp.tool()
def sukl_drug_info(sukl_kod: str) -> str:
    """
    Returns detailed information about a Czech medicinal product:
    - Basic identification (name, form, ATC code, registration status)
    - Insurance reimbursement amount and conditions (indikační omezení)
    - Dosing and administration from the SPC document (section 4.2),
      including dose reductions for special populations

    The SUKL API has no name search — users must provide the 7-digit SUKL
    code, which can be looked up at https://prehledy.sukl.cz/prehled_leciv.html

    Args:
        sukl_kod: 7-digit SUKL code (e.g. "0210027"). Shorter codes are
                  automatically zero-padded on the left.
    """
    kod = sukl_kod.strip().zfill(7)
    sections = []

    # 1. Basic info
    try:
        info = fetch_basic_info(kod)
        sections.append(
            "## Základní informace\n"
            f"**Název:** {info.get('nazev', '')} {info.get('sila', '')}\n"
            f"**Léková forma:** {info.get('lekovaFormaKod', 'N/A')}\n"
            f"**Balení:** {info.get('baleni', 'N/A')}\n"
            f"**ATC kód:** {info.get('ATCkod', 'N/A')}\n"
            f"**Stav registrace:** {info.get('stavRegistraceKod', 'N/A')}\n"
            f"**DDD:** {info.get('dddMnozstvi', 'N/A')} {info.get('dddMnozstviJednotka', '')}"
        )
    except Exception as e:
        sections.append(f"## Základní informace\nChyba při načítání: {e}")

    # 2. Reimbursement
    try:
        reimb = fetch_reimbursement(kod)
        if not reimb:
            sections.append(
                "## Úhrada pojišťovnou\n"
                "Přípravek není evidován v seznamu SCAU "
                "(pravděpodobně není hrazen z veřejného zdravotního pojištění)."
            )
        else:
            if isinstance(reimb, list):
                reimb = reimb[0] if reimb else {}

            text = (
                "## Úhrada pojišťovnou (SCAU)\n"
                f"**Úhrada:** {reimb.get('uhrada', 'není k dispozici')} Kč\n"
                f"**Jádrová úhrada:** {reimb.get('jadrovaUhrada', 'není k dispozici')} Kč\n"
                f"**Cena původce:** {reimb.get('cenaPuvodce', 'není k dispozici')} Kč\n"
                f"**Max. cena v lékárně:** {reimb.get('maxCenaLekarna', 'není k dispozici')} Kč"
            )

            uhrady = reimb.get("uhrady", [])
            if uhrady:
                text += f"\n\n**Podmínky úhrady** ({len(uhrady)} záznam/záznamy):"
                for i, u in enumerate(uhrady, 1):
                    text += f"\n\n**[{i}] Plná úhrada:** {u.get('plnaUhrada', 'není k dispozici')}"
                    omezeni = u.get("indikacniOmezeni", "").strip()
                    if omezeni:
                        text += f"\n\n**Indikační omezení:**\n{omezeni}"

            sections.append(text)
    except Exception as e:
        sections.append(f"## Úhrada pojišťovnou\nChyba při načítání: {e}")

    # 3. Dosing from SPC section 4.2
    try:
        metadata = fetch_doc_metadata(kod)
        spc_url = get_spc_url(metadata or [])
        if not spc_url:
            sections.append(
                "## Dávkování a způsob podání (SPC sekce 4.2)\n"
                "URL dokumentu SPC nebylo nalezeno v metadatech."
            )
        else:
            full_text = download_pdf_text(spc_url)
            section_42 = extract_section_4_2(full_text)
            sections.append(
                f"## Dávkování a způsob podání (SPC sekce 4.2)\n{section_42}"
            )
    except Exception as e:
        sections.append(
            f"## Dávkování a způsob podání (SPC sekce 4.2)\nChyba při načítání PDF: {e}"
        )

    return "\n\n---\n\n".join(sections)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", sys.argv[1] if len(sys.argv) > 1 else 8000))
    print(f"Starting SUKL MCP server on port {port}...", flush=True)
    mcp.run(transport="sse", host="0.0.0.0", port=port)
