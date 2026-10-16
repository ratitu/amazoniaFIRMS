"""Clip espacial de pontos (CSV INPE) pela Amazônia Legal."""

from __future__ import annotations

import logging
from pathlib import Path
import geopandas as gpd
import pandas as pd
from shapely import contains_xy

log = logging.getLogger(__name__)

# Colunas típicas do CSV INPE (Focos_YYYYMM.csv)
# Campos comuns: LAT, LON, DATA, HORA, SATELITE, INSTRUMENTO, CONFIANCA, FRP, CLASSE, etc.
# Vamos ler flexível.

REQUIRED_LATLON = {"lat", "lon"}


def _find_latlon_cols(df: pd.DataFrame) -> tuple[str, str]:
    # Try common names
    lower_cols = {c.lower(): c for c in df.columns}
    if "lat" in lower_cols and "lon" in lower_cols:
        return lower_cols["lat"], lower_cols["lon"]
    if "latitude" in lower_cols and "longitude" in lower_cols:
        return lower_cols["latitude"], lower_cols["longitude"]
    if "latg" in lower_cols and "long" in lower_cols:
        # sometimes used
        pass
    # fallback first match
    candidates = [
        ("LAT", "LON"),
        ("latitude", "longitude"),
        ("Latitude", "Longitude"),
    ]
    for la, lo in candidates:
        if la in df.columns and lo in df.columns:
            return la, lo
    raise ValueError(f"Não encontrei colunas de lat/lon em {list(df.columns)[:10]}")


def clip_csvs_to_amazon(
    csv_paths: list[Path],
    boundary_geojson: str | Path,
    out_geojson: str | Path,
) -> Path:
    boundary_geojson = Path(boundary_geojson)
    out_geojson = Path(out_geojson)
    out_geojson.parent.mkdir(parents=True, exist_ok=True)

    # Read boundary (ensure WGS84)
    gdf_boundary = gpd.read_file(boundary_geojson)
    if gdf_boundary.crs is None:
        gdf_boundary.set_crs(epsg=4326, inplace=True)
    elif gdf_boundary.crs.to_epsg() != 4326:
        gdf_boundary = gdf_boundary.to_crs(4326)

    # Dissolve to single polygon mask
    mask = gdf_boundary.geometry.union_all()
    log.info("Boundary: %d features dissolved to single polygon", len(gdf_boundary))

    chunk_size = 50000
    parts = []

    for csv_path in csv_paths:
        log.info("Reading %s ...", csv_path.name)
        df = pd.read_csv(csv_path)
        la_col, lo_col = _find_latlon_cols(df)
        df = df.dropna(subset=[la_col, lo_col])
        df[lo_col] = pd.to_numeric(df[lo_col], errors="coerce")
        df[la_col] = pd.to_numeric(df[la_col], errors="coerce")
        df = df.dropna(subset=[la_col, lo_col])
        log.info("  %d rows with valid coords", len(df))

        # Vectorized point-in-polygon using shapely.contains_xy
        # This is orders of magnitude faster than per-geometry .within()
        lon_vals = df[lo_col].values
        lat_vals = df[la_col].values
        contains = contains_xy(mask, lon_vals, lat_vals)
        df_in = df[contains]
        log.info("  %d points inside mask (vectorized)", len(df_in))

        if len(df_in) > 0:
            # Build GeoDataFrame only for points inside mask (much smaller)
            gdf_in = gpd.GeoDataFrame(
                df_in,
                geometry=gpd.points_from_xy(df_in[lo_col], df_in[la_col]),
                crs="EPSG:4326",
            )
            parts.append(gdf_in)

    if not parts:
        # Write empty file
        gdf_out = gpd.GeoDataFrame(
            columns=["acq_date", "acq_time", "satellite", "instrument", "confidence", "type"],
            geometry=[],
            crs="EPSG:4326",
        )
    else:
        gdf_out = pd.concat(parts, ignore_index=True)
        gdf_out = gpd.GeoDataFrame(gdf_out, geometry="geometry", crs="EPSG:4326")

        rename = {
            "data": "acq_date",
            "hora": "acq_time",
            "satelite": "satellite",
            "instrumento": "instrument",
            "confianca": "confidence",
            "classe": "type",
        }
        gdf_out = gdf_out.rename(columns={k: v for k, v in rename.items() if k in gdf_out.columns})

        dup_cols = [c for c in ["lat", "lon", "data_hora_gmt", "acq_date", "acq_time"] if c in gdf_out.columns]
        if dup_cols:
            before = len(gdf_out)
            gdf_out = gdf_out.drop_duplicates(subset=dup_cols)
            log.info("Dedup: %d -> %d", before, len(gdf_out))

    log.info("Writing %s (%d features)", out_geojson, len(gdf_out))
    gdf_out.to_file(out_geojson, driver="GeoJSON", encoding="utf-8")
    log.info("Done: %s", out_geojson)

    return out_geojson
