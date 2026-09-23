"""QGIS helpers: layer styles and usage notes.

Generates QGIS Layer Style (.qml) files so index rasters open with sensible
symbology instead of the default grey stretch. Pair with the COGs written by
:mod:`imagery.io` — drag the .tif into QGIS, then apply the matching .qml.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from .io import write_text

# (value, label, (r, g, b, a)) stops for single-band pseudocolor ramps.
_RAMP_NDVI: List[Tuple[float, str, Tuple[int, int, int, int]]] = [
    (-1.0, "Water / cloud shadow", (12, 44, 132, 255)),
    (-0.1, "Bare / built", (165, 142, 110, 255)),
    (0.2, "Sparse vegetation", (222, 214, 120, 255)),
    (0.5, "Moderate vegetation", (120, 180, 70, 255)),
    (0.8, "Dense vegetation", (34, 120, 40, 255)),
    (1.0, "Very dense", (10, 80, 30, 255)),
]

_RAMP_NDWI: List[Tuple[float, str, Tuple[int, int, int, int]]] = [
    (-1.0, "Dry land", (210, 180, 140, 255)),
    (0.0, "Moist soil", (170, 200, 170, 255)),
    (0.3, "Wet / shallow water", (120, 180, 220, 255)),
    (1.0, "Open water", (20, 80, 200, 255)),
]

_RAMP_DIVERGING: List[Tuple[float, str, Tuple[int, int, int, int]]] = [
    (-1.0, "Strong negative", (165, 0, 38, 255)),
    (-0.5, "Negative", (244, 163, 130, 255)),
    (0.0, "Neutral", (245, 245, 220, 255)),
    (0.5, "Positive", (150, 200, 130, 255)),
    (1.0, "Strong positive", (0, 104, 55, 255)),
]

STYLE_RAMPS: Dict[str, List[Tuple[float, str, Tuple[int, int, int, int]]]] = {
    "ndvi": _RAMP_NDVI,
    "evi": _RAMP_NDVI,
    "savi": _RAMP_NDVI,
    "ndwi": _RAMP_NDWI,
    "ndmi": _RAMP_DIVERGING,
    "ndbi": _RAMP_DIVERGING,
    "nbr": _RAMP_DIVERGING,
    "bsi": _RAMP_DIVERGING,
}


def _qml_for_ramp(
    ramp: List[Tuple[float, str, Tuple[int, int, int, int]]],
    band: int = 1,
) -> str:
    items = []
    for value, label, (r, g, b, a) in ramp:
        items.append(
            f'      <item value="{value}" label="{label}" '
            f'color="{r},{g},{b},{a}" alpha="{a}"/>'
        )
    items_xml = "\n".join(items)
    vmin = ramp[0][0]
    vmax = ramp[-1][0]
    return f"""<!DOCTYPE qgis PUBLIC 'http://mrcc.com/qgis.dtd' 'SYSTEM'>
<qgis version="3.28" minScale="1e+08" maxScale="0" styleCategories="Symbology">
  <pipe>
    <rasterrenderer type="singlebandpseudocolor" band="{band}" opacity="1" alphaBand="-1" classificationMin="{vmin}" classificationMax="{vmax}">
      <rastershader>
        <colorrampshader colorRampType="INTERPOLATED" clip="0" classificationMode="2" minimumValue="{vmin}" maximumValue="{vmax}">
          <colorramp type="gradient" name="[source]">
            <Option type="Map">
              <Option type="QString" value="{ramp[0][2][0]},{ramp[0][2][1]},{ramp[0][2][2]},{ramp[0][2][3]}" name="color1"/>
              <Option type="QString" value="{ramp[-1][2][0]},{ramp[-1][2][1]},{ramp[-1][2][2]},{ramp[-1][2][3]}" name="color2"/>
              <Option type="QString" value="0" name="discrete"/>
              <Option type="QString" value="gradient" name="rampType"/>
            </Option>
          </colorramp>
<items>
{items_xml}
</items>
        </colorrampshader>
      </rastershader>
    </rasterrenderer>
    <brightnesscontrast brightness="0" contrast="0" gamma="1"/>
    <huesaturation colorizeOn="0"/>
    <rasterresampler maxOversampling="2"/>
  </pipe>
  <blendMode>0</blendMode>
</qgis>
"""


def style_qml(style: str = "ndvi", band: int = 1) -> str:
    """Return QML text for a named style preset.

    Presets: ndvi (also suits evi/savi), ndwi, and a diverging ramp for
    ndmi/ndbi/nbr/bsi. Raises ``ValueError`` for unknown presets.
    """
    try:
        ramp = STYLE_RAMPS[style]
    except KeyError as exc:
        raise ValueError(
            f"unknown QGIS style {style!r}; known: {', '.join(sorted(STYLE_RAMPS))}"
        ) from exc
    return _qml_for_ramp(ramp, band=band)


def write_style_qml(path: str, style: str = "ndvi", band: int = 1) -> str:
    """Write a .qml sidecar file next to a raster product."""
    return write_text(path, style_qml(style, band=band))


def list_styles() -> List[str]:
    return sorted(STYLE_RAMPS)


def qgis_usage_notes() -> str:
    return (
        "QGIS quickstart\n"
        "---------------\n"
        "1. Drag a product .tif (Cloud-Optimized GeoTIFF) into the Layers panel.\n"
        "2. Right-click the layer > Properties > Symbology > Style > Load Style,\n"
        "   and pick the matching .qml written beside the raster.\n"
        "3. The monitor's timeseries.csv loads via Layer > Add Layer > Add Delimited\n"
        "   Text Layer (or join it to the AOI GeoJSON on scene_id).\n"
        "4. For per-pass refreshes, re-run the monitor with a later --end date;\n"
        "   new scenes append to the same output folder and CSV.\n"
    )
