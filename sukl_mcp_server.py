"""
SUKL MCP Server — exposes Czech SÚKL drug database via MCP protocol.

Provides two tools:
  - sukl_search(name)        search reimbursed drugs by name
  - sukl_drug_info(sukl_kod) get reimbursement + dosing info for a drug

Usage:
  python sukl_mcp_server.py [port]   (default port: 8000)

Dependencies:
  pip install fastmcp requests pdfplumber
"""

import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from fastmcp import FastMCP

from sukl_api import (
    download_pdf_text,
    extract_section_4_2,
    fetch_basic_info,
    fetch_doc_metadata,
    fetch_latest_period,
    fetch_product_codes,
    fetch_reimbursement,
    get_spc_url,
)

# ---------------------------------------------------------------------------
# Cache — maps kodSukl -> "NAZEV SILA" for reimbursed (SCAU) products only
# ---------------------------------------------------------------------------
CACHE_FILE = Path(__file__).parent / "sukl_cache.json"
CACHE_TTL_DAYS = 7
MAX_WORKERS = 20  # concurrent threads for batch name fetching

_cache_lock = threading.Lock()  # ensures only one build runs at a time


def _load_cache() -> dict:
    if not CACHE_FILE.exists():
        return {}
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cache(data: dict):
    CACHE_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _cache_is_fresh(cache: dict) -> bool:
    ts = cache.get("updated_at")
    if not ts:
        return False
    updated = datetime.fromisoformat(ts)
    age = datetime.now(timezone.utc) - updated.replace(tzinfo=timezone.utc)
    return age.days < CACHE_TTL_DAYS


def _fetch_name(kod: str) -> tuple[str, str]:
    """Fetch display name for one SUKL code. Returns (kod, display_name)."""
    try:
        info = fetch_basic_info(kod)
        name = info.get("nazev", "")
        sila = info.get("sila", "")
        display = f"{name} {sila}".strip()
        return kod, display
    except Exception:
        return kod, ""


def _build_product_map() -> dict[str, str]:
    """Fetch all reimbursed product codes, then batch-fetch their names."""
    print("Fetching list of reimbursed products...", flush=True)
    period = fetch_latest_period()
    codes = fetch_product_codes(period, typ_seznamu="scau")
    print(f"Found {len(codes)} products for period {period}. Fetching names...", flush=True)

    product_map: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(_fetch_name, kod): kod for kod in codes}
        done = 0
        for future in as_completed(futures):
            kod, name = future.result()
            if name:
                product_map[kod] = name
            done += 1
            if done % 500 == 0:
                print(f"  {done}/{len(codes)} fetched...", flush=True)

    print(f"Cache ready: {len(product_map)} products.", flush=True)
    return product_map


def get_product_map() -> dict[str, str] | None:
    """Return {kodSukl: 'NAZEV SILA'}, building/refreshing cache as needed.
    Returns None if another thread is already building the cache."""
    cache = _load_cache()
    if _cache_is_fresh(cache) and cache.get("products"):
        return cache["products"]

    # Non-blocking acquire: if another thread is already building, return None
    acquired = _cache_lock.acquire(blocking=False)
    if not acquired:
        return None

    try:
        # Re-check after acquiring — another thread may have just finished
        cache = _load_cache()
        if _cache_is_fresh(cache) and cache.get("products"):
            return cache["products"]

        product_map = _build_product_map()
        _save_cache(
            {
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "products": product_map,
            }
        )
        return product_map
    finally:
        _cache_lock.release()


# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------
mcp = FastMCP("SUKL Drug Database")


@mcp.tool()
def sukl_search(name: str) -> str:
    """
    Vyhledá hrazené léčivé přípravky v databázi SÚKL podle názvu.
    Vrátí seznam kódů SÚKL a názvů přípravků odpovídajících hledanému výrazu.
    Hledá pouze v přípravcích hrazených pojišťovnou (seznam SCAU).

    Args:
        name: Název nebo část názvu léčivého přípravku (např. "Jardiance", "Ibuprofen")
    """
    product_map = get_product_map()
    if product_map is None:
        return (
            "The product name cache is still being built in the background "
            "(fetching ~8500 product names from SUKL API). "
            "Please try again in a few minutes. "
            "If you already know the SUKL code, use sukl_drug_info directly."
        )

    query = name.strip().lower()

    matches = sorted(
        [(k, v) for k, v in product_map.items() if query in v.lower()],
        key=lambda x: x[1],
    )

    if not matches:
        return (
            f"Žádný hrazený přípravek obsahující '{name}' nebyl nalezen.\n"
            "Pokud znáte kód SÚKL, použijte přímo nástroj sukl_drug_info."
        )

    lines = [f"{k}: {v}" for k, v in matches[:50]]
    result = f"Nalezeno {len(matches)} hrazených přípravků (zobrazeno max. 50):\n\n"
    result += "\n".join(lines)

    if len(matches) > 50:
        result += f"\n\n...a dalších {len(matches) - 50} přípravků. Upřesněte název."

    return result


@mcp.tool()
def sukl_drug_info(sukl_kod: str) -> str:
    """
    Vrátí detailní informace o léčivém přípravku:
    - Základní identifikační údaje
    - Úhrada pojišťovnou a podmínky úhrady (indikační omezení)
    - Dávkování a způsob podání ze Souhrnu údajů o přípravku (SPC, sekce 4.2),
      včetně případné redukce dávky u speciálních populací

    Args:
        sukl_kod: 7-místný kód SÚKL přípravku (např. "0210027"). Kratší kódy
                  jsou automaticky doplněny nulami zleva.
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
                "Přípravek není evidován v seznamu SCAU (pravděpodobně není hrazen z veřejného zdravotního pojištění)."
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
        sections.append(f"## Dávkování a způsob podání (SPC sekce 4.2)\nChyba při načítání PDF: {e}")

    return "\n\n---\n\n".join(sections)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import os

    # Render sets the PORT env var; fall back to CLI arg or 8000
    port = int(os.environ.get("PORT", sys.argv[1] if len(sys.argv) > 1 else 8000))

    # Build cache in background so the server port opens immediately
    def _warm_cache():
        print("Building product name cache in background...", flush=True)
        try:
            get_product_map()
        except Exception as e:
            print(f"Warning: cache build failed ({e}). Will retry on first query.", flush=True)

    threading.Thread(target=_warm_cache, daemon=True).start()

    print(f"Starting SUKL MCP server on port {port}...", flush=True)
    mcp.run(transport="sse", host="0.0.0.0", port=port)
