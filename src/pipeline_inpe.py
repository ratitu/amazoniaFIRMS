"""Pipeline INPE: baixa CSVs mensais + clip + gera mapa."""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

from . import inpe_fetch, clip_amazon, build_inpe

log = logging.getLogger(__name__)

INPE_CSV_DIR = Path("data") / "inpe_csv"
INPE_CLIPPED = Path("data") / "inpe_amazonia_legal.geojson"
BOUNDARY = Path("Limites_Amazonia_Legal_2024.geojson")


def run_pipeline(
    start_date: date = date(2026, 8, 1),
    end_date: date | None = None,
) -> dict:
    if end_date is None:
        end_date = date.today()
    # Download
    downloads = inpe_fetch.download_inpe_months(start_date, end_date, INPE_CSV_DIR)
    csv_paths = [d.path for d in downloads if d.path.exists()]
    # Clip
    clipped = clip_amazon.clip_csvs_to_amazon(csv_paths, BOUNDARY, INPE_CLIPPED)
    # Map
    html = build_inpe.generate_map(BOUNDARY, clipped, build_inpe.OUT_HTML)
    return {"csvs": len(csv_paths), "clipped": str(clipped), "html": str(html)}
