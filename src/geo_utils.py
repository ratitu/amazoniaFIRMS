"""Utilitários geoespaciais para o projeto amazoniaFIRMS.

Responsabilidades:
  - Ler o GeoJSON dos limites da Amazônia Legal
  - Extrair o bounding box no formato exigido pela API FIRMS (min_lon,min_lat,max_lon,max_lat)
  - Validar que a geometria é utilizável (CRS WGS84, não vazia, bbox dentro de limites plausíveis)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from shapely.geometry import box, mapping, shape
from shapely.geometry.base import BaseGeometry

log = logging.getLogger(__name__)

# Limites globais WGS84 — usados para detectar coordenadas trocadas ou invertidas
LON_MIN, LON_MAX = -180.0, 180.0
LAT_MIN, LAT_MAX = -90.0, 90.0


@dataclass(frozen=True)
class AreaBounds:
    """Bounding box no formato da API FIRMS: min_lon,min_lat,max_lon,max_lat."""

    min_lon: float
    min_lat: float
    max_lon: float
    max_lat: float

    def to_api_string(self, decimals: int = 6) -> str:
        """Serializa no formato ACEITO pelo endpoint /api/area da FIRMS."""
        fmt = f"{{:.{decimals}f}}"
        return ",".join(
            fmt.format(v)
            for v in (self.min_lon, self.min_lat, self.max_lon, self.max_lat)
        )

    def as_list(self) -> list[float]:
        return [self.min_lon, self.min_lat, self.max_lon, self.max_lat]

    def to_folium_bounds(self) -> list[list[float]]:
        """Formato [[south, west], [north, east]] esperado por folium fit_bounds."""
        return [[self.min_lat, self.min_lon], [self.max_lat, self.max_lon]]


class InvalidGeometryError(ValueError):
    """Levantada quando o GeoJSON não pode ser usado para consultar a FIRMS."""


def load_geojson_geometry(path: str | Path) -> BaseGeometry:
    """Carrega a geometria do primeiro feature de um FeatureCollection GeoJSON."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"GeoJSON não encontrado: {path}")

    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)

    if data.get("type") != "FeatureCollection" or not data.get("features"):
        raise InvalidGeometryError(
            f"{path.name}: esperado FeatureCollection com ao menos 1 feature"
        )

    geom = shape(data["features"][0]["geometry"])

    if geom.is_empty:
        raise InvalidGeometryError(f"{path.name}: geometria vazia")
    if not geom.is_valid:
        # polygonos do IBGE podem ter auto-interseções leves; tentamos corrigir
        log.warning("%s: geometria inválida, aplicando make_valid()", path.name)
        geom = geom.buffer(0)
        if geom.is_empty:
            raise InvalidGeometryError(
                f"{path.name}: geometria inválida e não recuperável com buffer(0)"
            )

    return geom


def validate_wgs84_bounds(bounds: AreaBounds, source: str = "geometria") -> None:
    """Valida que o bbox está em WGS84 e ordenado (min < max)."""
    b = bounds
    if not (LON_MIN <= b.min_lon <= LON_MAX and LON_MIN <= b.max_lon <= LON_MAX):
        raise InvalidGeometryError(
            f"{source}: longitudes fora de WGS84 ({b.min_lon}, {b.max_lon})"
        )
    if not (LAT_MIN <= b.min_lat <= LAT_MAX and LAT_MIN <= b.max_lat <= LAT_MAX):
        raise InvalidGeometryError(
            f"{source}: latitudes fora de WGS84 ({b.min_lat}, {b.max_lat})"
        )
    if b.min_lon >= b.max_lon:
        raise InvalidGeometryError(
            f"{source}: min_lon ({b.min_lon}) >= max_lon ({b.max_lon}) — bbox invertido?"
        )
    if b.min_lat >= b.max_lat:
        raise InvalidGeometryError(
            f"{source}: min_lat ({b.min_lat}) >= max_lat ({b.max_lat}) — bbox invertido?"
        )


def bounds_from_geojson(path: str | Path, buffer_deg: float = 0.0) -> AreaBounds:
    """Extrai o bbox da Amazônia Legal a partir do GeoJSON.

    A API FIRMS aceita apenas bounding box (não polígonos), então retornamos
    a caixa envolvente. `buffer_deg` adiciona uma margem para evitar excluir
    focos exatamente na borda do limite oficial.
    """
    geom = load_geojson_geometry(path)

    if buffer_deg:
        geom = geom.buffer(buffer_deg)

    minx, miny, maxx, maxy = geom.bounds
    bounds = AreaBounds(
        min_lon=round(float(minx), 6),
        min_lat=round(float(miny), 6),
        max_lon=round(float(maxx), 6),
        max_lat=round(float(maxy), 6),
    )
    validate_wgs84_bounds(bounds, source=Path(path).name)
    log.info("bbox extraído: %s", bounds.to_api_string())
    return bounds


def simplified_boundary_geojson(
    path: str | Path, tolerance_deg: float = 0.02, max_vertices: int = 4000
) -> dict:
    """Retorna o limite como GeoJSON simplificado para sobreposição no mapa.

    O arquivo original tem ~9.5 MB e milhares de vértices por polígono, o que
    deixaria o HTML do GitHub Pages pesado. `simplify` do shapely com
    topologia preservada reduz o tamanho mantendo o formato visual.
    """
    geom = load_geojson_geometry(path)

    if tolerance_deg > 0:
        geom = geom.simplify(tolerance_deg, preserve_topology=True)

    payload = mapping(geom)
    vertices = _count_vertices(geom)
    if vertices > max_vertices:
        # reduz a tolerância até ficar dentro do orçamento de vértices
        tol = tolerance_deg
        for _ in range(12):
            tol /= 2
            candidate = geom.simplify(tol, preserve_topology=True)
            if _count_vertices(candidate) <= max_vertices:
                geom = candidate
                break
        else:
            log.warning(
                "limite mantém %d vértices mesmo com simplificação mínima",
                _count_vertices(geom),
            )

    log.info("limite simplificado: %d vértices (buffer inexistente)", _count_vertices(geom))
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"NOME": "Amazônia Legal", "source": Path(path).name},
                "geometry": mapping(geom),
            }
        ],
    }


def bbox_as_geometry(bounds: AreaBounds) -> BaseGeometry:
    """Constrói um shapely polygon a partir do bbox (útil para testes e filtros)."""
    return box(bounds.min_lon, bounds.min_lat, bounds.max_lon, bounds.max_lat)


def _count_vertices(geom: BaseGeometry) -> int:
    """Conta vértices de Polygon/MultiPolygon/GeometryCollection."""
    if geom.geom_type == "Polygon":
        return sum(len(ring.coords) for ring in geom.interiors and [] or []) + len(
            geom.exterior.coords
        )
    if geom.geom_type in ("MultiPolygon", "GeometryCollection"):
        return sum(_count_vertices(g) for g in geom.geoms)
    if geom.geom_type == "LineString":
        return len(geom.coords)
    return 0


def douglas_peucker_line_count(coords: list[tuple[float, float]]) -> int:  # pragma: no cover
    """Utilitário auxiliar para depuração de tamanho de geometria."""
    return len(coords)
