"""Download de focos de queimadas do INPE (Queimadas) - CSVs mensais do Brasil.

Fonte: https://dataserver-coids.inpe.br/queimadas/queimadas/focos/csv/mensal/Brasil/

Para cada mês do intervalo [start_date, end_date], tenta baixar o arquivo:
    Focos_YYYYMM.csv

Os CSVs mensais do INPE contêm focos de todo o Brasil (lat/lon WGS84).
Após o download, os pontos devem ser recortados (clip) pela máscara da
Amazônia Legal.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable, List
import requests

log = logging.getLogger(__name__)

BASE_URL = "https://dataserver-coids.inpe.br/queimadas/queimadas/focos/csv/mensal/Brasil"

RETRY_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})


@dataclass(frozen=True)
class InpeDownload:
    month: str  # YYYYMM
    path: Path


def _month_start(d: date) -> date:
    return d.replace(day=1)


def _iter_months(start: date, end: date) -> Iterable[date]:
    cur = _month_start(start)
    endm = _month_start(end)
    while cur <= endm:
        yield cur
        # next month
        if cur.month == 12:
            cur = cur.replace(year=cur.year + 1, month=1, day=1)
        else:
            cur = cur.replace(month=cur.month + 1, day=1)


def _filename_for_month(d: date) -> str:
    return f"focos_mensal_br_{d.year:04d}{d.month:02d}.csv"


def _download_file(url: str, dest: Path, timeout: int = 60, retries: int = 3) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    for attempt in range(1, retries + 2):
        try:
            resp = session.get(url, timeout=timeout)
            if resp.status_code in RETRY_STATUSES and attempt <= retries:
                log.warning("INPE download %s retornou %s (tentativa %d)", url, resp.status_code, attempt)
                continue
            if resp.status_code == 404:
                # Arquivo mensal pode não existir (mês em processamento) — trata como ausente
                raise FileNotFoundError(f"Arquivo não encontrado no INPE: {url} (404)")
            resp.raise_for_status()
            dest.write_bytes(resp.content)
            log.info("Baixado %s (%d bytes)", dest.name, len(resp.content))
            return dest
        except requests.RequestException as e:
            if attempt <= retries and getattr(e, "response", None) and e.response.status_code in RETRY_STATUSES:
                log.warning("Erro INPE %s (tentativa %d): %s", url, attempt, e)
                continue
            raise
    return dest


def download_inpe_months(
    start_date: date,
    end_date: date,
    out_dir: str | Path,
) -> List[InpeDownload]:
    """Baixa CSVs mensais do INPE para o Brasil no intervalo [start_date, end_date].

    Retorna lista com caminhos dos arquivos baixados (ou que já existiam).
    Arquivos 404 são ignorados (registrados em log).
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    results: List[InpeDownload] = []
    for m in _iter_months(start_date, end_date):
        fname = _filename_for_month(m)
        fpath = out_dir / fname
        url = f"{BASE_URL}/{fname}"
        if fpath.exists():
            log.info("CSV INPE já existe: %s", fpath.name)
            results.append(InpeDownload(month=f"{m.year:04d}{m.month:02d}", path=fpath))
            continue
        try:
            _download_file(url, fpath)
            results.append(InpeDownload(month=f"{m.year:04d}{m.month:02d}", path=fpath))
        except FileNotFoundError as e:
            log.warning("%s", e)
        except Exception as e:
            log.warning("Falha ao baixar %s: %s", url, e)
    return results
