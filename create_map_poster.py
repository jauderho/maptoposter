#!/usr/bin/env python3
"""
City Map Poster Generator

This module generates beautiful, minimalist map posters for any city in the world.
It fetches OpenStreetMap data using OSMnx, applies customizable themes, and creates
high-quality poster-ready images with roads, water features, and parks.

Non-road layers (water, parks, farmland, forest, grass, rail, ...) are declared
declaratively in layers.json (falls back to an embedded default if missing).

Rendering pipeline (for performance):
  1. fetch_layers()        - concurrent network I/O (graph + every configured layer),
                               raw-pickle cached per layer.
  2. prepare_render_data()  - theme-independent preprocessing (projection, edge geometry
                               extraction, road classification, polygon layers converted
                               to matplotlib Path objects, line layers converted to Nx2
                               coordinate arrays), cached as a single "prepared_v3" bundle
                               (keyed in part by a hash of layers.json) so warm runs skip
                               networkx/osmnx/geopandas work entirely.
  3. render_poster()        - pure matplotlib rendering (LineCollection for roads/line
                               layers, PathCollection for polygon layers), parameterized
                               by an explicit theme dict (no mutable global state), safe
                               to run in worker processes for --all-themes.

Heavy/optional dependencies (osmnx, networkx, geopy, shapely, geopandas) are imported
lazily inside the functions that need them, so the warm path (cached prepared bundle ->
render_poster) never pays their import cost.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import pickle
import sys
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict, cast

import matplotlib

matplotlib.use("Agg")  # noqa: E402 - must be set before importing pyplot; also needed by spawned workers

# Slightly more aggressive path simplification than the 0.111 default. Measured
# effect on this project's output: RMSE 0.0 vs default (0 pixels differ) and a
# small (~2%, within run-to-run noise here) warm single-theme render time win.
# Set at module level so spawned --all-themes worker processes inherit it too.
matplotlib.rcParams["path.simplify_threshold"] = 1.0

import matplotlib.colors as mcolors  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from lat_lon_parser import parse  # noqa: E402
from matplotlib.collections import LineCollection, PathCollection  # noqa: E402
from matplotlib.font_manager import FontProperties  # noqa: E402
from matplotlib.path import Path as MplPath  # noqa: E402
from tqdm import tqdm  # noqa: E402

from font_management import load_fonts  # noqa: E402

if TYPE_CHECKING:
    # Only used for type hints at module scope; the real modules are imported
    # lazily inside the functions that need them (see module docstring).
    from geopandas import GeoDataFrame
    from networkx import MultiDiGraph


class CacheError(Exception):
    """Raised when a cache operation fails."""


CACHE_DIR_PATH = os.environ.get("CACHE_DIR", "cache")
CACHE_DIR = Path(CACHE_DIR_PATH)
CACHE_DIR.mkdir(exist_ok=True)

THEMES_DIR = "themes"
FONTS_DIR = "fonts"
POSTERS_DIR = "posters"
LAYERS_CONFIG_FILE = "layers.json"

FILE_ENCODING = "utf-8"

PREPARED_CACHE_VERSION = "prepared_v3"

# --ocean support: OSM's natural=water/bay/strait Overpass tags don't cover the
# open sea, so coastal posters otherwise render an empty background where the
# ocean should be (issue #125). Optionally filled in from OpenStreetMap's
# pre-built "water polygons" dataset (https://osmdata.openstreetmap.de/data/water-polygons.html),
# a global shapefile distributed as a single zip. It is NOT an Overpass-fetched
# layer (no fetch_layers job) and is only ever touched on the --ocean build path.
WATER_POLYGONS_URL = "https://osmdata.openstreetmap.de/download/water-polygons-split-4326.zip"
WATER_POLYGONS_ZIP_NAME = "water-polygons-split-4326.zip"
WATER_POLYGONS_INTERNAL_SHP = "water-polygons-split-4326/water_polygons.shp"
# The zip is ~860MB as of this writing; require a bit of headroom on top of that.
MIN_FREE_BYTES_FOR_OCEAN_DOWNLOAD = 1_073_741_824  # 1GiB

# Embedded fallback used when layers.json is missing (same pattern as
# load_theme's embedded terracotta fallback). Keep the "water" and "parks"
# entries' tags identical to their historical hard-coded values so their raw
# fetch_features() cache keys (derived from tag dict keys) stay valid.
DEFAULT_LAYERS_CONFIG: dict[str, Any] = {
    "polygon_layers": [
        {
            "name": "water",
            "tags": {"natural": ["water", "bay", "strait"], "waterway": "riverbank"},
            "color_key": "water",
            "fallback_key": None,
            "zorder": 0.5,
        },
        {
            "name": "farmland",
            "tags": {"landuse": ["farmland", "orchard", "vineyard"]},
            "color_key": "farmland",
            "fallback_key": "parks",
            "zorder": 0.6,
        },
        {
            "name": "forest",
            "tags": {"landuse": ["forest"], "natural": ["wood"]},
            "color_key": "forest",
            "fallback_key": "parks",
            "zorder": 0.65,
        },
        {
            "name": "grass",
            "tags": {"landuse": ["meadow", "village_green", "recreation_ground"], "natural": ["grassland"]},
            "color_key": "grass",
            "fallback_key": "parks",
            "zorder": 0.75,
        },
        {
            "name": "parks",
            "tags": {"leisure": "park", "landuse": "grass"},
            "color_key": "parks",
            "fallback_key": None,
            "zorder": 0.8,
        },
    ],
    "line_layers": [
        {
            "name": "rail",
            "tags": {"railway": ["rail", "subway", "tram", "light_rail"]},
            "color_key": "rail",
            "fallback_key": "road_secondary",
            "zorder": 1.05,
            "linewidth": 0.5,
            "core_key": "rail_core",
            "core_width_ratio": 0.4,
        },
    ],
}

# Road classification hierarchy shared by width + color assignment.
# Order matters and mirrors the original get_edge_colors_by_type/get_edge_widths_by_type.
_BASE_ROAD_WIDTHS: dict[str, float] = {
    "motorway": 1.2,
    "primary": 1.0,
    "secondary": 0.8,
    "tertiary": 0.6,
    "residential": 0.4,
    "default": 0.4,
}

FONTS = load_fonts()


class PreparedData(TypedDict):
    """Theme-independent, pre-processed render data for a single (point, dist)."""

    segments: list[np.ndarray]
    classes: list[str]
    base_widths: np.ndarray
    # Matplotlib-native Path objects (exterior + interior rings as one compound
    # path each, mirroring geopandas' _PolygonPatch) so the warm render path can
    # draw them with a plain PathCollection without importing geopandas at all.
    # Keyed by polygon_layers[*].name (e.g. "water", "parks", "farmland", ...).
    polygons: dict[str, list[MplPath] | None]
    # Keyed by line_layers[*].name (e.g. "rail"); each value is a list of Nx2
    # numpy coordinate arrays, same shape as `segments` above.
    lines: dict[str, list[np.ndarray] | None]
    crs: Any


def _cache_path(key: str) -> str:
    """
    Generate a safe cache file path from a cache key.

    Args:
        key: Cache key identifier

    Returns:
        Path to cache file with .pkl extension
    """
    safe = key.replace(os.sep, "_")
    return os.path.join(CACHE_DIR, f"{safe}.pkl")


def cache_get(key: str):
    """
    Retrieve a cached object by key.

    Args:
        key: Cache key identifier

    Returns:
        Cached object if found, None otherwise

    Raises:
        CacheError: If cache read operation fails
    """
    try:
        path = _cache_path(key)
        if not os.path.exists(path):
            return None
        with open(path, "rb") as f:
            return pickle.load(f)
    except Exception as e:
        raise CacheError(f"Cache read failed: {e}") from e


def cache_set(key: str, value):
    """
    Store an object in the cache.

    Args:
        key: Cache key identifier
        value: Object to cache (must be picklable)

    Raises:
        CacheError: If cache write operation fails
    """
    try:
        if not os.path.exists(CACHE_DIR):
            os.makedirs(CACHE_DIR)
        path = _cache_path(key)
        with open(path, "wb") as f:
            pickle.dump(value, f, protocol=pickle.HIGHEST_PROTOCOL)
    except Exception as e:
        raise CacheError(f"Cache write failed: {e}") from e


# Font loading now handled by font_management.py module


def is_latin_script(text):
    """
    Check if text is primarily Latin script.
    Used to determine if letter-spacing should be applied to city names.

    :param text: Text to analyze
    :return: True if text is primarily Latin script, False otherwise
    """
    if not text:
        return True

    latin_count = 0
    total_alpha = 0

    for char in text:
        if char.isalpha():
            total_alpha += 1
            # Latin Unicode ranges:
            # - Basic Latin: U+0000 to U+007F
            # - Latin-1 Supplement: U+0080 to U+00FF
            # - Latin Extended-A: U+0100 to U+017F
            # - Latin Extended-B: U+0180 to U+024F
            if ord(char) < 0x250:
                latin_count += 1

    # If no alphabetic characters, default to Latin (numbers, symbols, etc.)
    if total_alpha == 0:
        return True

    # Consider it Latin if >80% of alphabetic characters are Latin
    return (latin_count / total_alpha) > 0.8


def generate_output_filename(city, theme_name, output_format):
    """
    Generate unique output filename with city, theme, and datetime.
    """
    if not os.path.exists(POSTERS_DIR):
        os.makedirs(POSTERS_DIR)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    city_slug = city.lower().replace(" ", "_")
    ext = output_format.lower()
    filename = f"{city_slug}_{theme_name}_{timestamp}.{ext}"
    return os.path.join(POSTERS_DIR, filename)


def get_available_themes():
    """
    Scans the themes directory and returns a list of available theme names.
    """
    if not os.path.exists(THEMES_DIR):
        os.makedirs(THEMES_DIR)
        return []

    themes = []
    for file in sorted(os.listdir(THEMES_DIR)):
        if file.endswith(".json"):
            theme_name = file[:-5]  # Remove .json extension
            themes.append(theme_name)
    return themes


def load_theme(theme_name="terracotta"):
    """
    Load theme from JSON file in themes directory.
    """
    theme_file = os.path.join(THEMES_DIR, f"{theme_name}.json")

    if not os.path.exists(theme_file):
        print(f"⚠ Theme file '{theme_file}' not found. Using default terracotta theme.")
        # Fallback to embedded terracotta theme
        return {
            "name": "Terracotta",
            "description": "Mediterranean warmth - burnt orange and clay tones on cream",
            "bg": "#F5EDE4",
            "text": "#8B4513",
            "gradient_color": "#F5EDE4",
            "water": "#A8C4C4",
            "parks": "#E8E0D0",
            "road_motorway": "#A0522D",
            "road_primary": "#B8653A",
            "road_secondary": "#C9846A",
            "road_tertiary": "#D9A08A",
            "road_residential": "#E5C4B0",
            "road_default": "#D9A08A",
        }

    with open(theme_file, "r", encoding=FILE_ENCODING) as f:
        theme = json.load(f)
        print(f"✓ Loaded theme: {theme.get('name', theme_name)}")
        if "description" in theme:
            print(f"  {theme['description']}")
        return theme


def load_layers_config() -> dict[str, Any]:
    """
    Load the declarative non-road layer configuration from layers.json.

    Falls back to the embedded DEFAULT_LAYERS_CONFIG (same pattern as
    load_theme's embedded terracotta fallback) if the file is missing.
    """
    if not os.path.exists(LAYERS_CONFIG_FILE):
        print(f"⚠ Layers file '{LAYERS_CONFIG_FILE}' not found. Using default layer configuration.")
        return DEFAULT_LAYERS_CONFIG

    with open(LAYERS_CONFIG_FILE, "r", encoding=FILE_ENCODING) as f:
        return cast("dict[str, Any]", json.load(f))


def _layers_config_hash(layers_config: dict[str, Any]) -> str:
    """
    Short stable hash of the layers config, used in the prepared cache key so
    editing layers.json invalidates prepared_v3 bundles built from the old config.
    """
    canonical = json.dumps(layers_config, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:8]


def create_gradient_fade(ax, color, location="bottom", zorder=10):
    """
    Creates a fade effect at the top or bottom of the map.
    """
    vals = np.linspace(0, 1, 256).reshape(-1, 1)
    gradient = np.hstack((vals, vals))

    rgb = mcolors.to_rgb(color)
    my_colors = np.zeros((256, 4))
    my_colors[:, 0] = rgb[0]
    my_colors[:, 1] = rgb[1]
    my_colors[:, 2] = rgb[2]

    if location == "bottom":
        my_colors[:, 3] = np.linspace(1, 0, 256)
        extent_y_start = 0
        extent_y_end = 0.25
    else:
        my_colors[:, 3] = np.linspace(0, 1, 256)
        extent_y_start = 0.75
        extent_y_end = 1.0

    custom_cmap = mcolors.ListedColormap(my_colors)

    xlim = ax.get_xlim()
    ylim = ax.get_ylim()
    y_range = ylim[1] - ylim[0]

    y_bottom = ylim[0] + y_range * extent_y_start
    y_top = ylim[0] + y_range * extent_y_end

    ax.imshow(
        gradient,
        extent=[xlim[0], xlim[1], y_bottom, y_top],
        aspect="auto",
        cmap=custom_cmap,
        zorder=zorder,
        origin="lower",
    )


def _classify_highway(highway: str | list[str] | None) -> str:
    """
    Classify a highway tag into a road-class label.

    Mirrors the priority order used by the original get_edge_colors_by_type /
    get_edge_widths_by_type functions, but produces a single label used to look
    up both width and (theme-dependent) color.
    """
    if isinstance(highway, list):
        highway = highway[0] if highway else "unclassified"
    if highway is None:
        highway = "unclassified"

    if highway in ("motorway", "motorway_link"):
        return "motorway"
    if highway in ("trunk", "trunk_link", "primary", "primary_link"):
        return "primary"
    if highway in ("secondary", "secondary_link"):
        return "secondary"
    if highway in ("tertiary", "tertiary_link"):
        return "tertiary"
    if highway in ("residential", "living_street", "unclassified"):
        return "residential"
    return "default"


def _theme_road_colors(theme: dict[str, str]) -> dict[str, str]:
    """Map road-class labels to this theme's colors."""
    return {
        "motorway": theme["road_motorway"],
        "primary": theme["road_primary"],
        "secondary": theme["road_secondary"],
        "tertiary": theme["road_tertiary"],
        "residential": theme["road_residential"],
        "default": theme["road_default"],
    }


def get_coordinates(city, country):
    """
    Fetches coordinates for a given city and country using geopy.
    Includes rate limiting to be respectful to the geocoding service.
    """
    coords = f"coords_{city.lower()}_{country.lower()}"
    cached = cache_get(coords)
    if cached:
        print(f"✓ Using cached coordinates for {city}, {country}")
        return cached

    print("Looking up coordinates...")
    from geopy.geocoders import Nominatim

    geolocator = Nominatim(user_agent="city_map_poster", timeout=10)

    # Add a small delay to respect Nominatim's usage policy
    time.sleep(1)

    try:
        location = geolocator.geocode(f"{city}, {country}")
    except Exception as e:
        raise ValueError(f"Geocoding failed for {city}, {country}: {e}") from e

    # If geocode returned a coroutine in some environments, run it to get the result.
    if asyncio.iscoroutine(location):
        try:
            location = asyncio.run(location)
        except RuntimeError as exc:
            # If an event loop is already running, try using it to complete the coroutine.
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Running event loop in the same thread; raise a clear error.
                raise RuntimeError(
                    "Geocoder returned a coroutine while an event loop is already running. "
                    "Run this script in a synchronous environment."
                ) from exc
            location = loop.run_until_complete(location)

    if location:
        # Use getattr to safely access address (helps static analyzers)
        addr = getattr(location, "address", None)
        if addr:
            print(f"✓ Found: {addr}")
        else:
            print("✓ Found location (address not available)")
        print(f"✓ Coordinates: {location.latitude}, {location.longitude}")
        try:
            cache_set(coords, (location.latitude, location.longitude))
        except CacheError as e:
            print(e)
        return (location.latitude, location.longitude)

    raise ValueError(f"Could not find coordinates for {city}, {country}")


def get_crop_limits(
    crs: Any, center_lat_lon: tuple[float, float], fig, dist: float
) -> tuple[tuple[float, float], tuple[float, float]]:
    """
    Crop inward to preserve aspect ratio while guaranteeing
    full coverage of the requested radius.

    Args:
        crs: Projected CRS (as used by the graph/prepared bundle) to project the
            center point into.
        center_lat_lon: (latitude, longitude) of the map center.
        fig: Matplotlib figure (used for its aspect ratio).
        dist: Requested radius in meters.
    """
    lat, lon = center_lat_lon

    # Project center point into the prepared data's CRS. Uses pyproj directly
    # (already a transitive dependency via geopandas) instead of osmnx, so this
    # warm-path call doesn't need to import osmnx.
    from pyproj import Transformer

    center_x, center_y = Transformer.from_crs("EPSG:4326", crs, always_xy=True).transform(lon, lat)

    fig_width, fig_height = fig.get_size_inches()
    aspect = fig_width / fig_height

    # Start from the *requested* radius
    half_x = dist
    half_y = dist

    # Cut inward to match aspect
    if aspect > 1:  # landscape → reduce height
        half_y = half_x / aspect
    else:  # portrait → reduce width
        half_x = half_y * aspect

    return (
        (center_x - half_x, center_x + half_x),
        (center_y - half_y, center_y + half_y),
    )


def fetch_graph(point, dist) -> MultiDiGraph | None:
    """
    Fetch street network graph from OpenStreetMap.

    Uses caching to avoid redundant downloads. Fetches all network types
    within the specified distance from the center point.

    Args:
        point: (latitude, longitude) tuple for center point
        dist: Distance in meters from center point

    Returns:
        MultiDiGraph of street network, or None if fetch fails
    """
    lat, lon = point
    graph = f"graph_{lat}_{lon}_{dist}"
    cached = cache_get(graph)
    if cached is not None:
        print("✓ Using cached street network")
        return cast("MultiDiGraph", cached)

    import osmnx as ox

    try:
        g = ox.graph_from_point(point, dist=dist, dist_type='bbox', network_type='all', truncate_by_edge=True)
        # Rate limit between requests
        time.sleep(0.5)
        try:
            cache_set(graph, g)
        except CacheError as e:
            print(e)
        return g
    except Exception as e:
        print(f"OSMnx error while fetching graph: {e}")
        return None


def fetch_features(point, dist, tags, name) -> GeoDataFrame | None:
    """
    Fetch geographic features (water, parks, etc.) from OpenStreetMap.

    Uses caching to avoid redundant downloads. Fetches features matching
    the specified OSM tags within distance from center point.

    Args:
        point: (latitude, longitude) tuple for center point
        dist: Distance in meters from center point
        tags: Dictionary of OSM tags to filter features
        name: Name for this feature type (for caching and logging)

    Returns:
        GeoDataFrame of features, or None if fetch fails
    """
    lat, lon = point
    tag_str = "_".join(tags.keys())
    features = f"{name}_{lat}_{lon}_{dist}_{tag_str}"
    cached = cache_get(features)
    if cached is not None:
        print(f"✓ Using cached {name}")
        return cast("GeoDataFrame", cached)

    import osmnx as ox

    try:
        data = ox.features_from_point(point, tags=tags, dist=dist)
        # Rate limit between requests
        time.sleep(0.3)
        try:
            cache_set(features, data)
        except CacheError as e:
            print(e)
        return data
    except Exception as e:
        print(f"OSMnx error while fetching features: {e}")
        return None


class FetchedLayers(TypedDict):
    """Raw (un-prepared) fetch results: the street graph plus every configured layer."""

    graph: MultiDiGraph | None
    polygons: dict[str, GeoDataFrame | None]
    lines: dict[str, GeoDataFrame | None]


def fetch_layers(point: tuple[float, float], dist: float, layers_config: dict[str, Any]) -> FetchedLayers:
    """
    Fetch the street graph and every configured polygon/line layer concurrently.

    Each fetch still checks its own raw pickle cache first (cache_get), so this
    is cheap on warm runs and only hits the network for missing pieces. The
    per-request rate-limit sleeps in fetch_graph/fetch_features only run on
    actual network fetches, and now happen inside worker threads.

    ThreadPoolExecutor is capped at max_workers=3 (rather than one worker per
    job) to stay polite to the Overpass API now that the layer count is
    configurable and can grow past a handful.

    Args:
        point: (latitude, longitude) tuple for center point
        dist: Distance in meters from center point (already "compensated" for crop)
        layers_config: Parsed layers.json (or DEFAULT_LAYERS_CONFIG), with
            "polygon_layers" and "line_layers" lists.

    Returns:
        FetchedLayers with the graph and one GeoDataFrame (or None on failure)
        per configured layer, keyed by layer name.
    """
    # Pre-warm the osmnx/geopandas/shapely import chain in this (main) thread
    # before dispatching the pool below. osmnx and geopandas are lazily imported
    # inside fetch_graph/fetch_features/cache_get's unpickling, and importing the
    # same heavy extension-module chain for the first time concurrently from
    # multiple threads can deadlock Python's per-module import lock. A single
    # eager import here is a no-op cost-wise once cached in sys.modules.
    import osmnx as ox  # noqa: F401

    polygon_layers = layers_config["polygon_layers"]
    line_layers = layers_config["line_layers"]

    jobs: dict[str, Any] = {"street network": (fetch_graph, (point, dist))}
    for layer in polygon_layers:
        jobs[f"polygon:{layer['name']}"] = (fetch_features, (point, dist, layer["tags"], layer["name"]))
    for layer in line_layers:
        jobs[f"line:{layer['name']}"] = (fetch_features, (point, dist, layer["tags"], layer["name"]))

    results: dict[str, Any] = {}
    with tqdm(
        total=len(jobs),
        desc="Fetching map data",
        unit="step",
        bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt}",
    ) as pbar:
        with ThreadPoolExecutor(max_workers=3) as executor:
            future_to_name = {executor.submit(fn, *fn_args): name for name, (fn, fn_args) in jobs.items()}
            for future in as_completed(future_to_name):
                name = future_to_name[future]
                results[name] = future.result()
                pbar.set_description(f"Downloaded {name}")
                pbar.update(1)

    return {
        "graph": results["street network"],
        "polygons": {layer["name"]: results[f"polygon:{layer['name']}"] for layer in polygon_layers},
        "lines": {layer["name"]: results[f"line:{layer['name']}"] for layer in line_layers},
    }


def _polygon_paths(gdf: GeoDataFrame | None, crs: Any) -> list[MplPath] | None:
    """
    Filter a features GeoDataFrame to Polygon/MultiPolygon geometries, project it
    into the given CRS, and convert every polygon into a matplotlib-native `Path`
    (exterior ring + interior/hole rings combined into one compound path each).

    This exactly mirrors how geopandas builds polygon patches internally
    (geopandas.plotting._PolygonPatch / _plot_polygon_collection), so the
    rendered fill is identical, but the result is a plain list of `Path`
    objects: pickling/unpickling it and drawing it via `PathCollection` never
    needs geopandas to be imported, unlike a GeoDataFrame.

    Returns None if there's nothing to draw.
    """
    if gdf is None or gdf.empty:
        return None

    polys = gdf[gdf.geometry.type.isin(["Polygon", "MultiPolygon"])]
    if polys.empty:
        return None

    import osmnx as ox

    try:
        polys = ox.projection.project_gdf(polys)
    except Exception:
        polys = polys.to_crs(crs)

    paths: list[MplPath] = []
    for geom in polys.geometry:
        if geom is None or geom.is_empty:
            continue
        # geopandas' _sanitize_geoms() normalizes geometries before extracting
        # components (canonical ring start point/winding). Match that exactly:
        # it doesn't change the shape, but it does change per-vertex ordering,
        # which shows up as sub-pixel antialiasing differences at fill edges
        # if skipped.
        geom = geom.normalize()
        sub_polygons = geom.geoms if geom.geom_type == "MultiPolygon" else (geom,)
        for poly in sub_polygons:
            paths.append(
                MplPath.make_compound_path(
                    MplPath(np.asarray(poly.exterior.coords)[:, :2], closed=True),
                    *[
                        MplPath(np.asarray(ring.coords)[:, :2], closed=True)
                        for ring in poly.interiors
                    ],
                )
            )

    return paths or None


def _line_segments(gdf: GeoDataFrame | None, crs: Any) -> list[np.ndarray] | None:
    """
    Filter a features GeoDataFrame to LineString/MultiLineString geometries,
    project it into the given CRS, and flatten it into a list of Nx2 numpy
    coordinate arrays - the same plain-numpy shape used for road segments, so
    the render path can draw it with a LineCollection without geopandas.

    Returns None if there's nothing to draw.
    """
    if gdf is None or gdf.empty:
        return None

    lines = gdf[gdf.geometry.type.isin(["LineString", "MultiLineString"])]
    if lines.empty:
        return None

    import osmnx as ox

    try:
        lines = ox.projection.project_gdf(lines)
    except Exception:
        lines = lines.to_crs(crs)

    segments: list[np.ndarray] = []
    for geom in lines.geometry:
        if geom is None or geom.is_empty:
            continue
        parts = geom.geoms if geom.geom_type == "MultiLineString" else (geom,)
        for line in parts:
            segments.append(np.asarray(line.coords)[:, :2])

    return segments or None


def _ensure_water_polygons_zip() -> Path:
    """
    Ensure the OSM water-polygons dataset zip exists in CACHE_DIR, downloading
    it on first use.

    Build-path only: only reached when --ocean is set and the zip isn't
    already cached. Streams to a ".part" file and atomically renames it into
    place on success, so an interrupted download never leaves a corrupt file
    that a later run would mistake for a complete one.
    """
    zip_path = CACHE_DIR / WATER_POLYGONS_ZIP_NAME
    if zip_path.exists():
        return zip_path

    import shutil

    import requests

    free_bytes = shutil.disk_usage(CACHE_DIR).free
    if free_bytes < MIN_FREE_BYTES_FOR_OCEAN_DOWNLOAD:
        raise RuntimeError(
            "Not enough free disk space to download the OSM water-polygons dataset: "
            f"{free_bytes / 1_073_741_824:.2f}GiB free in '{CACHE_DIR}', need at least "
            f"{MIN_FREE_BYTES_FOR_OCEAN_DOWNLOAD / 1_073_741_824:.0f}GiB."
        )

    print(
        "⚠ --ocean needs the global OSM water-polygons dataset (~800MB zipped, "
        "https://osmdata.openstreetmap.de/data/water-polygons.html).\n"
        f"  Downloading once to '{zip_path}' (kept in cache for future runs)..."
    )

    part_path = zip_path.with_name(zip_path.name + ".part")
    try:
        with requests.get(WATER_POLYGONS_URL, stream=True, timeout=60) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0)) or None
            with open(part_path, "wb") as f, tqdm(
                total=total,
                unit="B",
                unit_scale=True,
                unit_divisor=1024,
                desc="Downloading water polygons",
            ) as pbar:
                for chunk in resp.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)
                        pbar.update(len(chunk))
    except BaseException:
        # Partial/corrupt download: never let a ".part" masquerade as done, and
        # don't leave one lying around to confuse a retry either.
        part_path.unlink(missing_ok=True)
        raise

    os.replace(part_path, zip_path)
    return zip_path


def _ocean_bbox(point: tuple[float, float], dist: float) -> tuple[float, float, float, float]:
    """
    Approximate WGS84 lon/lat bbox (minx, miny, maxx, maxy) covering `dist`
    meters around `point`, padded by 20%.

    Uses a simple degrees-per-meter approximation (good enough at poster
    scale): 1 degree latitude ~= 111320m, 1 degree longitude ~= 111320m *
    cos(latitude).
    """
    import math

    lat, lon = point
    dlat = (dist / 111320) * 1.2
    dlon = (dist / (111320 * math.cos(math.radians(lat)))) * 1.2
    return (lon - dlon, lat - dlat, lon + dlon, lat + dlat)


def _fetch_ocean_gdf(point: tuple[float, float], dist: float) -> GeoDataFrame | None:
    """
    Bbox-read and clip the OSM water-polygons dataset (open sea / ocean fill;
    see module-level WATER_POLYGONS_URL comment) for the area around
    (point, dist).

    Build-path only (--ocean); never called on the warm cached-bundle path.
    Reads directly out of the downloaded zip via GDAL's virtual filesystem
    (no extraction needed). The dataset's polygons can be enormous (a single
    sea can span an entire ocean basin) and the bbox= read is only an
    intersects() prefilter, so this clips geometries down to the exact bbox
    here, before projection - projecting a basin-spanning geometry first
    would be far more expensive.
    """
    zip_path = _ensure_water_polygons_zip()

    import pyogrio
    from shapely.geometry import box

    minx, miny, maxx, maxy = _ocean_bbox(point, dist)
    shp_uri = f"zip://{zip_path.resolve()}!/{WATER_POLYGONS_INTERNAL_SHP}"

    print("Reading ocean/sea polygons for the requested area...")
    gdf = pyogrio.read_dataframe(shp_uri, bbox=(minx, miny, maxx, maxy))
    if gdf is None or gdf.empty:
        return None

    bbox_poly = box(minx, miny, maxx, maxy)
    gdf = gdf.copy()
    gdf["geometry"] = gdf.geometry.intersection(bbox_poly)
    gdf = gdf[~gdf.geometry.is_empty]
    if gdf.empty:
        return None

    return cast("GeoDataFrame", gdf)


def prepare_render_data(
    point: tuple[float, float], dist: float, layers_config: dict[str, Any], ocean: bool = False
) -> PreparedData:
    """
    Build (or load from cache) the theme-independent data needed to render a poster.

    On a warm run this loads a single pickled bundle and does no networkx unpickling,
    no ox.project_graph, no ox.plot_graph, and no geopandas import (water/parks are
    stored as plain matplotlib Path lists, not GeoDataFrames). If the prepared cache
    is missing but the raw graph/water/parks caches exist, this rebuilds the bundle
    from those without any network access.

    Args:
        point: (latitude, longitude) tuple for map center
        dist: Compensated map radius in meters (same value used for the raw fetch caches)
        layers_config: Parsed layers.json (or DEFAULT_LAYERS_CONFIG). A short hash of
            this config is folded into the cache key so editing layers.json invalidates
            stale bundles. Deliberately never includes an "ocean" entry - ocean isn't
            an Overpass-fetched layer, so it's kept out of fetch_layers()'s job list
            and handled separately via the `ocean` flag below.
        ocean: When True, additionally fetch/clip/project the OSM water-polygons
            dataset for this area and store it in the bundle's polygons dict under
            "ocean" (see _fetch_ocean_gdf). Downloads a large dataset to CACHE_DIR
            on first use. Folded into the cache key (suffix "_ocean") so bundles
            built with and without it never collide. When False (default), this
            function's behavior, cache key, and warm-path imports are byte-for-byte
            unchanged from before --ocean existed.

    Returns:
        PreparedData bundle with projected road segments/classes/widths and, per
        configured layer, matplotlib Path lists (polygons) or Nx2 coordinate
        arrays (lines).
    """
    lat, lon = point
    layers_hash = _layers_config_hash(layers_config)
    key = f"{PREPARED_CACHE_VERSION}_{lat}_{lon}_{dist}_{layers_hash}" + ("_ocean" if ocean else "")
    cached = cache_get(key)
    if cached is not None:
        print("✓ Using cached prepared render data")
        return cast(PreparedData, cached)

    fetched = fetch_layers(point, dist, layers_config)
    g = fetched["graph"]
    if g is None:
        raise RuntimeError("Failed to retrieve street network data.")
    print("✓ All data retrieved successfully!")

    print("Preparing render data (projecting + classifying roads)...")
    import osmnx as ox

    g_proj = ox.project_graph(g)
    crs = g_proj.graph["crs"]

    node_coords = {n: (d["x"], d["y"]) for n, d in g_proj.nodes(data=True)}

    segments: list[np.ndarray] = []
    classes: list[str] = []
    for u, v, _k, data in g_proj.edges(keys=True, data=True):
        geom = data.get("geometry")
        if geom is not None:
            coords = np.asarray(geom.coords)
        else:
            coords = np.array([node_coords[u], node_coords[v]])
        segments.append(coords)
        classes.append(_classify_highway(data.get("highway", "unclassified")))

    base_widths = np.array([_BASE_ROAD_WIDTHS[c] for c in classes], dtype=float)

    polygons = {
        layer["name"]: _polygon_paths(fetched["polygons"].get(layer["name"]), crs)
        for layer in layers_config["polygon_layers"]
    }
    lines = {
        layer["name"]: _line_segments(fetched["lines"].get(layer["name"]), crs)
        for layer in layers_config["line_layers"]
    }

    if ocean:
        polygons["ocean"] = _polygon_paths(_fetch_ocean_gdf(point, dist), crs)

    bundle: PreparedData = {
        "segments": segments,
        "classes": classes,
        "base_widths": base_widths,
        "polygons": polygons,
        "lines": lines,
        "crs": crs,
    }

    try:
        cache_set(key, bundle)
    except CacheError as e:
        print(e)

    return bundle


def render_poster(
    prepared: PreparedData,
    theme: dict[str, str],
    point: tuple[float, float],
    dist: float,
    output_file: str,
    output_format: str,
    layers_config: dict[str, Any],
    width: float = 12,
    height: float = 16,
    city: str = "",
    country: str = "",
    country_label: str | None = None,
    name_label: str | None = None,
    display_city: str | None = None,
    display_country: str | None = None,
    fonts: dict[str, str] | None = None,
) -> None:
    """
    Render a complete map poster (roads, polygon/line layers, typography) from
    prepared data.

    Pure rendering step: no network I/O and no graph projection. Safe to call from a
    worker process since `theme` is passed explicitly rather than read from a global.

    Args:
        prepared: Bundle returned by prepare_render_data().
        theme: Theme dict (as returned by load_theme()).
        point: (latitude, longitude) tuple for map center.
        dist: Compensated map radius in meters (same value passed to prepare_render_data).
        output_file: Path where poster will be saved.
        output_format: File format ('png', 'svg', or 'pdf').
        layers_config: Parsed layers.json (or DEFAULT_LAYERS_CONFIG). Cheap JSON, so it's
            re-read/passed in at render time rather than baked into the prepared bundle;
            supplies per-layer zorder/color-key/linewidth/core config.
        width: Poster width in inches (default: 12).
        height: Poster height in inches (default: 16).
        city: City name for display on poster.
        country: Country name for display on poster.
        country_label: Optional override for country text on poster.
        name_label: Optional override for city name.
        display_city: Optional explicit display name for city (i18n).
        display_country: Optional explicit display name for country (i18n).
        fonts: Optional font paths dict; falls back to module-level FONTS.
    """
    # Handle display names for i18n support
    # Priority: display_city/display_country > name_label/country_label > city/country
    display_city = display_city or name_label or city
    display_country = display_country or country_label or country

    print(f"Rendering '{theme.get('name', 'theme')}' poster for {city}, {country}...")

    fig, ax = plt.subplots(figsize=(width, height), facecolor=theme["bg"])
    ax.set_facecolor(theme["bg"])
    ax.set_position((0.0, 0.0, 1.0, 1.0))

    # Match osmnx's ox.plot_graph/_config_ax axes configuration: no margins, no
    # visible spines/ticks/axis. Without this, matplotlib's default tick labels
    # (large projected-CRS coordinate numbers) extend well past the plot area and
    # get included in the bbox_inches="tight" crop, inflating the saved image.
    ax.margins(0)
    ax.tick_params(which="both", direction="in")
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.get_xaxis().set_visible(False)
    ax.get_yaxis().set_visible(False)

    # Layer 1: Polygons (matplotlib-native PathCollection - no geopandas import
    # needed on the render path; paths were built once in prepare_render_data).
    # Color resolution: theme[color_key], falling back to theme[fallback_key] if
    # given, else the layer is skipped entirely (e.g. the 17 existing themes have
    # no farmland/forest/grass keys, so those layers fall back to "parks").
    polygons = prepared["polygons"]
    for layer in layers_config["polygon_layers"]:
        paths = polygons.get(layer["name"])
        if not paths:
            continue
        fallback_key = layer.get("fallback_key")
        color = theme.get(layer["color_key"]) or (theme.get(fallback_key) if fallback_key else None)
        if not color:
            continue
        ax.add_collection(
            PathCollection(paths, facecolor=color, edgecolor="none", zorder=layer["zorder"])
        )

    # Calculate scale factor based on smaller dimension (reference 12 inches).
    # Used both for text sizing and for road/line-layer linewidth scaling (issue #125).
    scale_factor = min(height, width) / 12.0

    # Layer 2: Roads with hierarchy coloring, as a single LineCollection
    road_colors_by_class = _theme_road_colors(theme)
    edge_colors = [road_colors_by_class[c] for c in prepared["classes"]]
    edge_widths = prepared["base_widths"] * scale_factor

    line_collection = LineCollection(
        prepared["segments"],
        colors=edge_colors,
        linewidths=edge_widths,
        zorder=1,
    )
    ax.add_collection(line_collection)

    # Layer 2b: configurable line layers (e.g. rail), each as its own
    # LineCollection at its configured zorder. If the theme defines the layer's
    # core_key (e.g. "rail_core"), draw a second, narrower LineCollection on top
    # in that color for a dual-stroke "neon" look; themes without the core key
    # (all 17 existing themes) just get the single-stroke line.
    line_data = prepared["lines"]
    for layer in layers_config["line_layers"]:
        segments = line_data.get(layer["name"])
        if not segments:
            continue
        fallback_key = layer.get("fallback_key")
        color = theme.get(layer["color_key"]) or (theme.get(fallback_key) if fallback_key else None)
        if not color:
            continue
        linewidth = layer["linewidth"] * scale_factor
        ax.add_collection(
            LineCollection(segments, colors=color, linewidths=linewidth, zorder=layer["zorder"])
        )

        core_key = layer.get("core_key")
        core_color = theme.get(core_key) if core_key else None
        if core_color:
            core_width = linewidth * layer.get("core_width_ratio", 0.4)
            ax.add_collection(
                LineCollection(
                    segments,
                    colors=core_color,
                    linewidths=core_width,
                    zorder=layer["zorder"] + 0.01,
                )
            )

    # Determine cropping limits to maintain the poster aspect ratio
    crop_xlim, crop_ylim = get_crop_limits(prepared["crs"], point, fig, dist)
    ax.set_aspect("equal", adjustable="box")
    ax.set_xlim(crop_xlim)
    ax.set_ylim(crop_ylim)

    # Layer 3: Gradients (Top and Bottom)
    create_gradient_fade(ax, theme["gradient_color"], location="bottom", zorder=10)
    create_gradient_fade(ax, theme["gradient_color"], location="top", zorder=10)

    # Base font sizes (at 12 inches width)
    base_main = 60
    base_sub = 22
    base_coords = 14
    base_attr = 8

    # 4. Typography - use custom fonts if provided, otherwise use default FONTS
    active_fonts = fonts or FONTS
    if active_fonts:
        # font_main is calculated dynamically later based on length
        font_sub = FontProperties(
            fname=active_fonts["light"], size=base_sub * scale_factor
        )
        font_coords = FontProperties(
            fname=active_fonts["regular"], size=base_coords * scale_factor
        )
        font_attr = FontProperties(
            fname=active_fonts["light"], size=base_attr * scale_factor
        )
    else:
        # Fallback to system fonts
        font_sub = FontProperties(
            family="monospace", weight="normal", size=base_sub * scale_factor
        )
        font_coords = FontProperties(
            family="monospace", size=base_coords * scale_factor
        )
        font_attr = FontProperties(family="monospace", size=base_attr * scale_factor)

    # Format city name based on script type
    # Latin scripts: apply uppercase and letter spacing for aesthetic
    # Non-Latin scripts (CJK, Thai, Arabic, etc.): no spacing, preserve case structure
    if is_latin_script(display_city):
        # Latin script: uppercase with letter spacing (e.g., "P  A  R  I  S")
        spaced_city = "  ".join(list(display_city.upper()))
    else:
        # Non-Latin script: no spacing, no forced uppercase
        # For scripts like Arabic, Thai, Japanese, etc.
        spaced_city = display_city

    # Dynamically adjust font size based on city name length to prevent truncation
    # We use the already scaled "main" font size as the starting point.
    base_adjusted_main = base_main * scale_factor
    city_char_count = len(display_city)

    # Heuristic: If length is > 10, start reducing.
    if city_char_count > 10:
        length_factor = 10 / city_char_count
        adjusted_font_size = max(base_adjusted_main * length_factor, 10 * scale_factor)
    else:
        adjusted_font_size = base_adjusted_main

    if active_fonts:
        font_main_adjusted = FontProperties(
            fname=active_fonts["bold"], size=adjusted_font_size
        )
    else:
        font_main_adjusted = FontProperties(
            family="monospace", weight="bold", size=adjusted_font_size
        )

    # --- BOTTOM TEXT ---
    ax.text(
        0.5,
        0.14,
        spaced_city,
        transform=ax.transAxes,
        color=theme["text"],
        ha="center",
        fontproperties=font_main_adjusted,
        zorder=11,
    )

    ax.text(
        0.5,
        0.10,
        display_country.upper(),
        transform=ax.transAxes,
        color=theme["text"],
        ha="center",
        fontproperties=font_sub,
        zorder=11,
    )

    lat, lon = point
    coords = (
        f"{lat:.4f}° N / {lon:.4f}° E"
        if lat >= 0
        else f"{abs(lat):.4f}° S / {lon:.4f}° E"
    )
    if lon < 0:
        coords = coords.replace("E", "W")

    ax.text(
        0.5,
        0.07,
        coords,
        transform=ax.transAxes,
        color=theme["text"],
        alpha=0.7,
        ha="center",
        fontproperties=font_coords,
        zorder=11,
    )

    ax.plot(
        [0.4, 0.6],
        [0.125, 0.125],
        transform=ax.transAxes,
        color=theme["text"],
        linewidth=1 * scale_factor,
        zorder=11,
    )

    # --- ATTRIBUTION (bottom right) ---
    if FONTS:
        font_attr = FontProperties(fname=FONTS["light"], size=8)
    else:
        font_attr = FontProperties(family="monospace", size=8)

    ax.text(
        0.98,
        0.02,
        "© OpenStreetMap contributors",
        transform=ax.transAxes,
        color=theme["text"],
        alpha=0.5,
        ha="right",
        va="bottom",
        fontproperties=font_attr,
        zorder=11,
    )

    # 5. Save
    print(f"Saving to {output_file}...")

    fmt = output_format.lower()

    if fmt == "png":
        # Avoid bbox_inches="tight": it re-draws the figure at screen DPI to
        # measure content, then draws AGAIN at the target DPI. Setting the
        # figure's DPI to the print DPI first and measuring once with
        # get_tightbbox() gives an identical crop for a single draw pass.
        # Calling fig.savefig() directly (instead of plt.savefig()) also
        # avoids pyplot's post-save draw_idle() redraw.
        fig.set_dpi(300)
        renderer = fig.canvas.get_renderer()
        bbox = fig.get_tightbbox(renderer).padded(0.05)
        fig.savefig(
            output_file,
            format=fmt,
            facecolor=theme["bg"],
            bbox_inches=bbox,
            dpi=300,
            # Fast (but still lossless) PNG compression: pixel content is
            # identical, this only trades a slightly larger file for
            # materially faster encoding.
            pil_kwargs={"compress_level": 1},
        )
    else:
        # Vector formats (svg/pdf) aren't the hot path; keep the original,
        # well-tested bbox_inches="tight" behavior for them to avoid parity risk.
        fig.savefig(
            output_file,
            format=fmt,
            facecolor=theme["bg"],
            bbox_inches="tight",
            pad_inches=0.05,
        )

    plt.close(fig)
    print(f"✓ Done! Poster saved as {output_file}")


# Per-worker-process memo of the prepared bundle, keyed by prepared_key. A
# ProcessPoolExecutor reuses each worker process across multiple submitted
# tasks, so without this a worker handling several themes would re-unpickle
# the ~5.7MB prepared bundle (~0.2s) from disk on every single theme.
_PREPARED_CACHE_MEMO: dict[str, PreparedData] = {}


def _render_worker(
    theme: dict[str, str],
    prepared_key: str,
    point: tuple[float, float],
    dist: float,
    output_file: str,
    output_format: str,
    layers_config: dict[str, Any],
    width: float,
    height: float,
    city: str,
    country: str,
    country_label: str | None,
    display_city: str | None,
    display_country: str | None,
    fonts: dict[str, str] | None,
) -> str:
    """
    Module-level worker for ProcessPoolExecutor (--all-themes).

    Must be a top-level function (not a closure) so it can be pickled by reference
    under macOS's "spawn" start method. It loads the prepared bundle from the cache
    file itself rather than having it pickled into every submit() call, and memoizes
    it in-process so a reused worker only unpickles it once across all the themes
    it ends up rendering.

    `theme` is the already-loaded theme dict (not a name): the parent process loads
    every theme JSON once up front, so workers skip that file I/O and the
    "✓ Loaded theme: ..." print entirely. `layers_config` is likewise the already-
    parsed layers.json, passed through instead of re-read per worker.
    """
    prepared = _PREPARED_CACHE_MEMO.get(prepared_key)
    if prepared is None:
        prepared = cache_get(prepared_key)
        if prepared is None:
            raise RuntimeError(f"Prepared render cache '{prepared_key}' not found in worker process")
        _PREPARED_CACHE_MEMO[prepared_key] = prepared

    render_poster(
        cast(PreparedData, prepared),
        theme,
        point,
        dist,
        output_file,
        output_format,
        layers_config,
        width=width,
        height=height,
        city=city,
        country=country,
        country_label=country_label,
        display_city=display_city,
        display_country=display_country,
        fonts=fonts,
    )
    return output_file


def print_examples():
    """Print usage examples."""
    print("""
City Map Poster Generator
=========================

Usage:
  python create_map_poster.py --city <city> --country <country> [options]

Examples:
  # Iconic grid patterns
  python create_map_poster.py -c "New York" -C "USA" -t noir -d 12000           # Manhattan grid
  python create_map_poster.py -c "Barcelona" -C "Spain" -t warm_beige -d 8000   # Eixample district grid

  # Waterfront & canals
  python create_map_poster.py -c "Venice" -C "Italy" -t blueprint -d 4000       # Canal network
  python create_map_poster.py -c "Amsterdam" -C "Netherlands" -t ocean -d 6000  # Concentric canals
  python create_map_poster.py -c "Dubai" -C "UAE" -t midnight_blue -d 15000     # Palm & coastline

  # Radial patterns
  python create_map_poster.py -c "Paris" -C "France" -t pastel_dream -d 10000   # Haussmann boulevards
  python create_map_poster.py -c "Moscow" -C "Russia" -t noir -d 12000          # Ring roads

  # Organic old cities
  python create_map_poster.py -c "Tokyo" -C "Japan" -t japanese_ink -d 15000    # Dense organic streets
  python create_map_poster.py -c "Marrakech" -C "Morocco" -t terracotta -d 5000 # Medina maze
  python create_map_poster.py -c "Rome" -C "Italy" -t warm_beige -d 8000        # Ancient street layout

  # Coastal cities
  python create_map_poster.py -c "San Francisco" -C "USA" -t sunset -d 10000    # Peninsula grid
  python create_map_poster.py -c "Sydney" -C "Australia" -t ocean -d 12000      # Harbor city
  python create_map_poster.py -c "Mumbai" -C "India" -t contrast_zones -d 18000 # Coastal peninsula

  # River cities
  python create_map_poster.py -c "London" -C "UK" -t noir -d 15000              # Thames curves
  python create_map_poster.py -c "Budapest" -C "Hungary" -t copper_patina -d 8000  # Danube split

  # List themes
  python create_map_poster.py --list-themes

Options:
  --city, -c        City name (required)
  --country, -C     Country name (required)
  --country-label   Override country text displayed on poster
  --theme, -t       Theme name (default: terracotta)
  --all-themes      Generate posters for all themes
  --distance, -d    Map radius in meters (default: 18000)
  --list-themes     List all available themes

Distance guide:
  4000-6000m   Small/dense cities (Venice, Amsterdam old center)
  8000-12000m  Medium cities, focused downtown (Paris, Barcelona)
  15000-20000m Large metros, full city view (Tokyo, Mumbai)

Available themes can be found in the 'themes/' directory.
Generated posters are saved to 'posters/' directory.
""")


def list_themes():
    """List all available themes with descriptions."""
    available_themes = get_available_themes()
    if not available_themes:
        print("No themes found in 'themes/' directory.")
        return

    print("\nAvailable Themes:")
    print("-" * 60)
    for theme_name in available_themes:
        theme_path = os.path.join(THEMES_DIR, f"{theme_name}.json")
        try:
            with open(theme_path, "r", encoding=FILE_ENCODING) as f:
                theme_data = json.load(f)
                display_name = theme_data.get('name', theme_name)
                description = theme_data.get('description', '')
        except (OSError, json.JSONDecodeError):
            display_name = theme_name
            description = ""
        print(f"  {theme_name}")
        print(f"    {display_name}")
        if description:
            print(f"    {description}")
        print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate beautiful map posters for any city",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python create_map_poster.py --city "New York" --country "USA"
  python create_map_poster.py --city "New York" --country "USA" -l 40.776676 -73.971321 --theme neon_cyberpunk
  python create_map_poster.py --city Tokyo --country Japan --theme midnight_blue
  python create_map_poster.py --city Paris --country France --theme noir --distance 15000
  python create_map_poster.py --list-themes
        """,
    )

    parser.add_argument("--city", "-c", type=str, help="City name")
    parser.add_argument("--country", "-C", type=str, help="Country name")
    parser.add_argument(
        "--latitude",
        "-lat",
        dest="latitude",
        type=str,
        help="Override latitude center point",
    )
    parser.add_argument(
        "--longitude",
        "-long",
        dest="longitude",
        type=str,
        help="Override longitude center point",
    )
    parser.add_argument(
        "--country-label",
        dest="country_label",
        type=str,
        help="Override country text displayed on poster",
    )
    parser.add_argument(
        "--theme",
        "-t",
        type=str,
        default="terracotta",
        help="Theme name (default: terracotta)",
    )
    parser.add_argument(
        "--all-themes",
        "--All-themes",
        dest="all_themes",
        action="store_true",
        help="Generate posters for all themes",
    )
    parser.add_argument(
        "--distance",
        "-d",
        type=int,
        default=18000,
        help="Map radius in meters (default: 18000)",
    )
    parser.add_argument(
        "--width",
        "-W",
        type=float,
        default=12,
        help="Image width in inches (default: 12, max: 20 )",
    )
    parser.add_argument(
        "--height",
        "-H",
        type=float,
        default=16,
        help="Image height in inches (default: 16, max: 20)",
    )
    parser.add_argument(
        "--list-themes", action="store_true", help="List all available themes"
    )
    parser.add_argument(
        "--ocean",
        action="store_true",
        help=(
            "Fill in open sea/ocean (OSM's natural=water tags don't cover it, so "
            "coastal posters otherwise leave it empty). On first use, downloads the "
            "global OSM water-polygons dataset (~800MB zipped) to the cache directory; "
            "subsequent runs reuse it."
        ),
    )
    parser.add_argument(
        "--display-city",
        "-dc",
        type=str,
        help="Custom display name for city (for i18n support)",
    )
    parser.add_argument(
        "--display-country",
        "-dC",
        type=str,
        help="Custom display name for country (for i18n support)",
    )
    parser.add_argument(
        "--font-family",
        type=str,
        help='Google Fonts family name (e.g., "Noto Sans JP", "Open Sans"). If not specified, uses local Roboto fonts.',
    )
    parser.add_argument(
        "--format",
        "-f",
        default="png",
        choices=["png", "svg", "pdf"],
        help="Output format for the poster (default: png)",
    )

    args = parser.parse_args()

    # If no arguments provided, show examples
    if len(sys.argv) == 1:
        print_examples()
        sys.exit(0)

    # List themes if requested
    if args.list_themes:
        list_themes()
        sys.exit(0)

    # Validate required arguments
    if not args.city or not args.country:
        print("Error: --city and --country are required.\n")
        print_examples()
        sys.exit(1)

    # Enforce maximum dimensions
    if args.width > 20:
        print(
            f"⚠ Width {args.width} exceeds the maximum allowed limit of 20. It's enforced as max limit 20."
        )
        args.width = 20.0
    if args.height > 20:
        print(
            f"⚠ Height {args.height} exceeds the maximum allowed limit of 20. It's enforced as max limit 20."
        )
        args.height = 20.0

    available_themes = get_available_themes()
    if not available_themes:
        print("No themes found in 'themes/' directory.")
        sys.exit(1)

    if args.all_themes:
        themes_to_generate = available_themes
    else:
        if args.theme not in available_themes:
            print(f"Error: Theme '{args.theme}' not found.")
            print(f"Available themes: {', '.join(available_themes)}")
            sys.exit(1)
        themes_to_generate = [args.theme]

    print("=" * 50)
    print("City Map Poster Generator")
    print("=" * 50)

    # Load custom fonts if specified
    custom_fonts = None
    if args.font_family:
        custom_fonts = load_fonts(args.font_family)
        if not custom_fonts:
            print(f"⚠ Failed to load '{args.font_family}', falling back to Roboto")

    # Get coordinates and generate poster
    try:
        if args.latitude and args.longitude:
            lat = parse(args.latitude)
            lon = parse(args.longitude)
            coords = (lat, lon)
            print(f"✓ Coordinates: {', '.join([str(i) for i in coords])}")
        else:
            coords = get_coordinates(args.city, args.country)

        print(f"\nGenerating map for {args.city}, {args.country}...")

        # Compensate the fetch radius so cropping to the poster aspect ratio still
        # covers the full requested viewport (same formula/keys as before, so
        # existing raw caches remain valid).
        compensated_dist = args.distance * (max(args.height, args.width) / min(args.height, args.width)) / 4

        layers_config = load_layers_config()
        layers_hash = _layers_config_hash(layers_config)

        prepared = prepare_render_data(coords, compensated_dist, layers_config, ocean=args.ocean)
        prepared_key = (
            f"{PREPARED_CACHE_VERSION}_{coords[0]}_{coords[1]}_{compensated_dist}_{layers_hash}"
            + ("_ocean" if args.ocean else "")
        )

        # Ocean is not an Overpass-fetched layer, so it's never added to
        # layers.json/DEFAULT_LAYERS_CONFIG (that would create a bogus fetch_layers
        # job and shift layers_hash, invalidating every existing prepared cache).
        # Instead, inject it into a render-only copy of the polygon layer list here,
        # before dispatch, so it flows into both the single-theme render_poster call
        # and every --all-themes worker automatically via `render_layers_config`.
        # prepare_render_data() special-cased fetching/projecting it above and
        # stashed the result in the bundle's polygons["ocean"].
        render_layers_config = layers_config
        if args.ocean:
            render_layers_config = {
                **layers_config,
                "polygon_layers": [
                    {
                        "name": "ocean",
                        "color_key": "ocean",
                        "fallback_key": "water",
                        "zorder": 0.45,
                    },
                    *layers_config["polygon_layers"],
                ],
            }

        if args.all_themes:
            print(f"Rendering {len(themes_to_generate)} themes in parallel...")
            # Load every theme JSON once in the parent process and pass the dict
            # itself to each worker, instead of a theme name each worker would
            # have to re-read from disk.
            theme_objs = {theme_name: load_theme(theme_name) for theme_name in themes_to_generate}
            with ProcessPoolExecutor() as executor:
                futures = []
                for theme_name in themes_to_generate:
                    output_file = generate_output_filename(args.city, theme_name, args.format)
                    futures.append(
                        executor.submit(
                            _render_worker,
                            theme_objs[theme_name],
                            prepared_key,
                            coords,
                            compensated_dist,
                            output_file,
                            args.format,
                            render_layers_config,
                            args.width,
                            args.height,
                            args.city,
                            args.country,
                            args.country_label,
                            args.display_city,
                            args.display_country,
                            custom_fonts,
                        )
                    )

                for future in tqdm(
                    as_completed(futures),
                    total=len(futures),
                    desc="Rendering themes",
                    unit="theme",
                ):
                    result_file = future.result()
                    print(f"✓ Rendered {result_file}")
        else:
            theme = load_theme(args.theme)
            output_file = generate_output_filename(args.city, args.theme, args.format)
            render_poster(
                prepared,
                theme,
                coords,
                compensated_dist,
                output_file,
                args.format,
                render_layers_config,
                width=args.width,
                height=args.height,
                city=args.city,
                country=args.country,
                country_label=args.country_label,
                display_city=args.display_city,
                display_country=args.display_country,
                fonts=custom_fonts,
            )

        print("\n" + "=" * 50)
        print("✓ Poster generation complete!")
        print("=" * 50)

    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
