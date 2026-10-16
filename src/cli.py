"""CLI simples para rodar pipeline INPE."""

from __future__ import annotations

import argparse
from datetime import date

from . import pipeline_inpe


def main():
    p = argparse.ArgumentParser(description="Pipeline INPE: download + clip + mapa")
    p.add_argument("--start", default="2026-08-01", help="Data início YYYY-MM-DD")
    p.add_argument("--end", default=None, help="Data fim YYYY-MM-DD (default: hoje)")
    args = p.parse_args()
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end) if args.end else None
    res = pipeline_inpe.run_pipeline(start, end)
    print(res)


if __name__ == "__main__":
    main()
