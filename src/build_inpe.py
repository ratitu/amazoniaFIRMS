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
OUT_DATA_JS = Path("docs") / "fire_data.js"
# Thousandths of a degree (~111 m). Fixed origins rather than data-derived ones:
# a reordered input must not change the encoding, and positive values stay 5 digits.
COORD_SCALE = 1000
LAT_ORIGIN = -18.0
LON_ORIGIN = -74.0
# INPE sends -999 for "no risk score"; map it and absent values to _MISSING.
_MISSING = -1
_RISK_NO_DATA = -999.0
_RISK_SCALE = 100

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


def _pack_risk(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return _MISSING
    if value <= _RISK_NO_DATA:
        return _MISSING
    return int(round(float(value) * _RISK_SCALE))


def _pack_frp(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return _MISSING
    return int(round(float(value)))


def _hhmm(acq_time: str) -> str:
    hours, _, rest = acq_time.partition(":")
    minutes = rest.partition(":")[0]
    try:
        return f"{int(hours):02d}:{int(minutes):02d}"
    except ValueError:
        return "00:00"


def _extract_marker_data(features: list[dict]) -> dict:
    """Pack hotspots into columnar integer arrays plus small lookup tables.

    Row-oriented JSON costs ~54 bytes per hotspot, and nesting 1.4M six-element
    arrays forces the browser to allocate 1.4M objects before it can draw
    anything. Columnar integer arrays cost ~23 bytes per hotspot and parse as a
    handful of flat number arrays instead.
    """
    dates: list[str] = []
    date_index: dict[str, int] = {}
    times: list[str] = []
    time_index: dict[str, int] = {}
    lat: list[int] = []
    lon: list[int] = []
    day: list[int] = []
    minute: list[int] = []
    risk: list[int] = []
    frp: list[int] = []

    for feat in features:
        geom = feat.get("geometry", {})
        if geom.get("type") != "Point":
            continue
        coords = geom.get("coordinates", [])
        if len(coords) < 2:
            continue
        lon_value, lat_value = coords[0], coords[1]
        if lat_value is None or lon_value is None:
            continue
        props = feat.get("properties", {})
        acq_date, _, acq_time = str(props.get("data_hora_gmt", "") or "").partition(" ")
        day_idx = date_index.get(acq_date)
        if day_idx is None:
            day_idx = len(dates)
            date_index[acq_date] = day_idx
            dates.append(acq_date)
        label = _hhmm(acq_time)
        time_idx = time_index.get(label)
        if time_idx is None:
            time_idx = len(times)
            time_index[label] = time_idx
            times.append(label)
        lat.append(int(round((float(lat_value) - LAT_ORIGIN) * COORD_SCALE)))
        lon.append(int(round((float(lon_value) - LON_ORIGIN) * COORD_SCALE)))
        day.append(day_idx)
        minute.append(time_idx)
        risk.append(_pack_risk(props.get("risco_fogo")))
        frp.append(_pack_frp(props.get("frp")))

    return {
        "scale": COORD_SCALE,
        "lat0": LAT_ORIGIN,
        "lon0": LON_ORIGIN,
        "dates": dates,
        "times": times,
        "lat": lat,
        "lon": lon,
        "day": day,
        "minute": minute,
        "risk": risk,
        "frp": frp,
    }


def generate_map(
    boundary_path: Path = BOUNDARY_PATH,
    hotspots_path: Path = INPE_CLIPPED,
    out_html: Path = OUT_HTML,
    out_data_js: Path | None = None,
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

        marker_data = _extract_marker_data(features)
        map_name = m.get_name()
        if out_data_js is None:
            out_data_js = out_html.with_name(OUT_DATA_JS.name)

        out_data_js.parent.mkdir(parents=True, exist_ok=True)
        out_data_js.write_text(
            "window.FIRE_DATA=" + json.dumps(marker_data, separators=(",", ":")) + ";",
            encoding="utf-8",
        )

        # The marker payload goes in a sibling file so the map can paint before
        # it is parsed. chunkedLoading only applies to addLayers() batches,
        # never to per-marker addLayer() calls.
        js_code = f"""
<script>
(function() {{
    var CHUNK_SIZE = 2000;
    function render() {{
        var d = window.FIRE_DATA;
        if (!d) {{
            return;
        }}
        var scale = d.scale, lat0 = d.lat0, lon0 = d.lon0;
        var dates = d.dates, times = d.times;
        var lat = d.lat, lon = d.lon, day = d.day, minute = d.minute;
        var risk = d.risk, frp = d.frp;
        var cluster = L.markerClusterGroup({{
            chunkedLoading: true,
            chunkInterval: 50,
            chunkDelay: 10,
            maxClusterRadius: 50
        }});
        var chunk = [];
        for (var i = 0; i < lat.length; i++) {{
            var marker = L.circleMarker(
                [lat0 + lat[i] / scale, lon0 + lon[i] / scale],
                {{
                    radius: 4,
                    color: '#ff4500',
                    fillColor: '#ff6600',
                    fillOpacity: 0.7,
                    weight: 1
                }}
            );
            var popup = '<b>Data:</b> ' + dates[day[i]] +
                       '<br><b>Hora:</b> ' + times[minute[i]] +
                       '<br><b>Risco de fogo:</b> ' + (risk[i] < 0 ? '-' : (risk[i] / {_RISK_SCALE}).toFixed(2)) +
                       '<br><b>FRP:</b> ' + (frp[i] < 0 ? '-' : String(frp[i]));
            marker.bindPopup(popup);
            chunk.push(marker);
            if (chunk.length >= CHUNK_SIZE) {{
                cluster.addLayers(chunk);
                chunk = [];
            }}
        }}
        if (chunk.length) {{
            cluster.addLayers(chunk);
        }}
        {map_name}.addLayer(cluster);
    }}
    var s = document.createElement('script');
    s.src = '{out_data_js.name}';
    s.onload = function() {{
        if (document.readyState === 'loading') {{
            document.addEventListener('DOMContentLoaded', render);
        }} else {{
            render();
        }}
    }};
    document.head.appendChild(s);
}})();
</script>
"""
        m.get_root().html.add_child(folium.Element(js_code))
        log.info(
            "Marcadores: %d em %s (payload separado)",
            len(marker_data["lat"]),
            out_data_js,
        )
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
