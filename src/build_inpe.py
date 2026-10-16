"""Gerador de mapa estático com Folium usando dados INPE já clipados."""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

import folium
from folium.plugins import MarkerCluster

from . import geo_utils

log = logging.getLogger(__name__)

BOUNDARY_PATH = Path("Limites_Amazonia_Legal_2024.geojson")
INPE_CLIPPED = Path("data") / "inpe_amazonia_legal.geojson"
OUT_HTML = Path("docs") / "index.html"
ESRI_IMAGERY = (
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
)
ESRI_ATTRIBUTION = (
    'Tiles &copy; Esri &mdash; Source: Esri, i-cubed, USDA, USGS, AEX, GeoEye, Getmapping, '
    "Aerogrid, IGN, IGP, UPR-EGP, and the GIS User Community"
)


def _load_hotspots(path: Path) -> list[dict]:
    if not path.is_file():
        raise FileNotFoundError(f"GeoJSON de focos não encontrado: {path}")
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    return data.get("features", [])


def _extract_marker_data(features: list[dict]) -> list[list]:
    """Extract compact marker data from GeoJSON features as arrays.

    Returns list of [lat, lon, date, time, risk, frp] arrays.
    """
    marker_data = []
    for feat in features:
        geom = feat.get("geometry", {})
        if geom.get("type") != "Point":
            continue
        coords = geom.get("coordinates", [])
        if len(coords) < 2:
            continue
        lon, lat = coords[0], coords[1]
        if lat is None or lon is None:
            continue
        props = feat.get("properties", {})
        data_hora = str(props.get("data_hora_gmt", "") or "")
        if " " in data_hora:
            acq_date, acq_time = data_hora.split(" ", 1)
        else:
            acq_date, acq_time = data_hora, ""
        marker_data.append([
            round(lat, 5),
            round(lon, 5),
            acq_date,
            acq_time,
            props.get("risco_fogo", ""),
            props.get("frp", ""),
        ])
    return marker_data


def generate_map(
    boundary_path: Path = BOUNDARY_PATH,
    hotspots_path: Path = INPE_CLIPPED,
    out_html: Path = OUT_HTML,
) -> Path:
    bounds = geo_utils.bounds_from_geojson(boundary_path)
    folium_bounds = bounds.to_folium_bounds()

    m = folium.Map(
        location=[(bounds.min_lat + bounds.max_lat) / 2, (bounds.min_lon + bounds.max_lon) / 2],
        zoom_start=4,
        tiles=None,
        control_scale=True,
    )
    folium.TileLayer(tiles=ESRI_IMAGERY, attr=ESRI_ATTRIBUTION, name="ESRI World Imagery").add_to(m)

    try:
        boundary_simplified = geo_utils.simplified_boundary_geojson(boundary_path)
        folium.GeoJson(
            boundary_simplified,
            name="Limites Amazônia Legal",
            style_function=lambda _: {
                "color": "#ff6b00",
                "weight": 2,
                "fill": False,
                "opacity": 0.9,
            },
        ).add_to(m)
    except Exception as e:
        log.warning("Falha ao adicionar limite simplificado: %s", e)

    features = _load_hotspots(hotspots_path)
    if features:
        # Add MarkerCluster to include JS/CSS assets
        MarkerCluster(name="Focos de Queimadas").add_to(m)

        # Extract compact marker data and inject via JavaScript
        marker_data = _extract_marker_data(features)
        marker_json = json.dumps(marker_data, separators=(",", ":"))
        map_name = m.get_name()

        # Folium emits `var map_xxx = L.map(...)` LAST, during render, i.e. after
        # any element added via add_child(). Injecting here therefore always lands
        # BEFORE the map variable exists -> ReferenceError. Defer until load.
        js_code = f"""
<script>
(function() {{
    function _addMarkers() {{
        var data = {marker_json};
        var cluster = L.markerClusterGroup({{
            chunkedLoading: true,
            chunkInterval: 50,
            chunkDelay: 10,
            maxClusterRadius: 50
        }});
        for (var i = 0; i < data.length; i++) {{
            var d = data[i];
            var marker = L.circleMarker([d[0], d[1]], {{
                radius: 4,
                color: '#ff4500',
                fillColor: '#ff6600',
                fillOpacity: 0.7,
                weight: 1
            }});
            var popup = '<b>Data:</b> ' + (d[2] || '-') +
                       '<br><b>Hora:</b> ' + (d[3] || '-') +
                       '<br><b>Risco de fogo:</b> ' + (d[4] || '-') +
                       '<br><b>FRP:</b> ' + (d[5] || '-');
            marker.bindPopup(popup);
            cluster.addLayer(marker);
        }}
        {map_name}.addLayer(cluster);
    }}
    if (document.readyState === 'loading') {{
        document.addEventListener('DOMContentLoaded', _addMarkers);
    }} else {{
        _addMarkers();
    }}
}})();
</script>
"""
        m.get_root().html.add_child(folium.Element(js_code))
        log.info("Injetados %d marcadores via JavaScript", len(marker_data))
    else:
        log.warning("Nenhum hotspot encontrado em %s", hotspots_path)

    m.fit_bounds(folium_bounds)
    folium.LayerControl().add_to(m)

    out_html.parent.mkdir(parents=True, exist_ok=True)
    m.save(str(out_html))
    log.info("Mapa salvo em %s (features=%d)", out_html, len(features))
    return out_html


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    generate_map()
