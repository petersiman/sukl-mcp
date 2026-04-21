"""
SUKL medicament info fetcher.
Fetches reimbursement data and extracts dosing (section 4.2) from SPC PDF.

Usage: python sukl_fetch.py <kodSukl>
Example: python sukl_fetch.py 0210027

Dependencies: pip install requests pdfplumber
"""

import sys

from sukl_api import (
    fetch_basic_info,
    fetch_doc_metadata,
    fetch_reimbursement,
    get_spc_url,
    download_pdf_text,
    extract_section_4_2,
)


def print_reimbursement(data):
    if not data:
        print("  Nenalezeno (přípravek pravděpodobně není hrazen přes SCAU).")
        return

    # Some responses wrap in a list
    if isinstance(data, list):
        data = data[0] if data else {}

    uhrada = data.get("uhrada") or data.get("uhrady", [{}])
    if isinstance(uhrada, list) and uhrada:
        uhrada = uhrada[0].get("uhrada", "není k dispozici")

    print(f"  Úhrada pojišťovnou:           {data.get('uhrada', uhrada)} Kč")
    print(f"  Jádrová úhrada:               {data.get('jadrovaUhrada', 'není k dispozici')} Kč")
    print(f"  Cena původce:                 {data.get('cenaPuvodce', 'není k dispozici')} Kč")
    print(f"  Max. cena v lékárně:          {data.get('maxCenaLekarna', 'není k dispozici')} Kč")

    uhrady = data.get("uhrady", [])
    if uhrady:
        print(f"\n  Podmínky úhrady ({len(uhrady)} záznam/záznamy):")
        for i, u in enumerate(uhrady, 1):
            print(f"\n  [{i}] Plná úhrada: {u.get('plnaUhrada', 'není k dispozici')}")
            omezeni = u.get("indikacniOmezeni", "").strip()
            if omezeni:
                print(f"      Indikační omezení:\n")
                for line in omezeni.splitlines():
                    print(f"        {line}")


def main():
    kod = sys.argv[1] if len(sys.argv) > 1 else "0210027"
    print(f"\n{'='*60}")
    print(f"Kód SÚKL: {kod}")
    print(f"{'='*60}\n")

    # 1. Základní informace
    print("[ Základní informace ]")
    info = fetch_basic_info(kod)
    print(f"  Název:          {info.get('nazev')} {info.get('sila')}")
    print(f"  Léková forma:   {info.get('lekovaFormaKod')}  |  Balení: {info.get('baleni')}")
    print(f"  ATC kód:        {info.get('ATCkod')}")
    print(f"  Stav registrace:{info.get('stavRegistraceKod')}")
    print(f"  DDD:            {info.get('dddMnozstvi')} {info.get('dddMnozstviJednotka')}")

    # 2. Úhrada pojišťovnou
    print("\n[ Úhrada pojišťovnou (SCAU) ]")
    reimb = fetch_reimbursement(kod)
    print_reimbursement(reimb)

    # 3. Dávkování ze SPC
    print("\n[ Dávkování a způsob podání — SPC sekce 4.2 ]")
    metadata = fetch_doc_metadata(kod)
    spc_url = get_spc_url(metadata or [])

    if not spc_url:
        print("  URL dokumentu SPC nebylo nalezeno v metadatech.")
    else:
        try:
            print(f"  Stahuji SPC PDF: {spc_url}")
            full_text = download_pdf_text(spc_url)
            section = extract_section_4_2(full_text)
            print()
            print(section)
        except Exception as e:
            print(f"  Chyba při čtení PDF: {e}")

    print(f"\n{'='*60}\n")


if __name__ == "__main__":
    main()
