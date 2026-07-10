# City Map Poster Generator

Generate beautiful, minimalist map posters for any city in the world.

<img src="posters/singapore_neon_cyberpunk_20260118_153328.png" width="250">
<img src="posters/dubai_midnight_blue_20260118_140807.png" width="250">

## Examples

| Country      | City           | Theme           | Poster |
|:------------:|:--------------:|:---------------:|:------:|
| USA          | San Francisco  | sunset          | <img src="posters/san_francisco_sunset_20260118_144726.png" width="250"> |
| Spain        | Barcelona      | warm_beige      | <img src="posters/barcelona_warm_beige_20260118_140048.png" width="250"> |
| Italy        | Venice         | blueprint       | <img src="posters/venice_blueprint_20260118_140505.png" width="250"> |
| Japan        | Tokyo          | japanese_ink    | <img src="posters/tokyo_japanese_ink_20260118_142446.png" width="250"> |
| India        | Mumbai         | contrast_zones  | <img src="posters/mumbai_contrast_zones_20260118_145843.png" width="250"> |
| Morocco      | Marrakech      | terracotta      | <img src="posters/marrakech_terracotta_20260118_143253.png" width="250"> |
| Singapore    | Singapore      | neon_cyberpunk  | <img src="posters/singapore_neon_cyberpunk_20260118_153328.png" width="250"> |
| Australia    | Melbourne      | forest          | <img src="posters/melbourne_forest_20260118_153446.png" width="250"> |
| UAE          | Dubai          | midnight_blue   | <img src="posters/dubai_midnight_blue_20260118_140807.png" width="250"> |
| USA          | Seattle        | emerald         | <img src="posters/seattle_emerald_20260124_162244.png" width="250"> |

## Installation

### With uv (Recommended)

Make sure [uv](https://docs.astral.sh/uv/) is installed. Running the script by prepending `uv run` automatically creates and manages a virtual environment.

```bash
# First run will automatically install dependencies
uv run ./create_map_poster.py --city "Paris" --country "France"

# Or sync dependencies explicitly first (using locked versions)
uv sync --locked
uv run ./create_map_poster.py --city "Paris" --country "France"
```

### With pip + venv

```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Usage

### Generate Poster

If you're using `uv`:

```bash
uv run ./create_map_poster.py --city <city> --country <country> [options]
```

Otherwise (pip + venv):

```bash
python create_map_poster.py --city <city> --country <country> [options]
```

### Required Options

| Option | Short | Description |
|--------|-------|-------------|
| `--city` | `-c` | City name (used for geocoding) |
| `--country` | `-C` | Country name (used for geocoding) |

### Optional Flags

| Option | Short | Description | Default |
|--------|-------|-------------|---------|
| **OPTIONAL:** `--latitude` | `-lat` | Override latitude center point (use with --longitude) | |
| **OPTIONAL:** `--longitude` | `-long` | Override longitude center point (use with --latitude) | |
| **OPTIONAL:** `--country-label` | | Override country text displayed on poster | |
| **OPTIONAL:** `--theme` | `-t` | Theme name | terracotta |
| **OPTIONAL:** `--distance` | `-d` | Map radius in meters | 18000 |
| **OPTIONAL:** `--list-themes` | | List all available themes | |
| **OPTIONAL:** `--all-themes` | | Generate posters for all available themes | |
| **OPTIONAL:** `--width` | `-W` | Image width in inches | 12 (max: 20) |
| **OPTIONAL:** `--height` | `-H` | Image height in inches | 16 (max: 20) |
| **OPTIONAL:** `--format` | `-f` | Output format: `png`, `svg`, or `pdf` | png |
| **OPTIONAL:** `--ocean` | | Fill open sea/ocean areas (downloads a ~800MB dataset to `cache/` on first use) | off |

### Multilingual Support - i18n

Display city and country names in your language with custom fonts from google fonts:

| Option | Short | Description |
|--------|-------|-------------|
| `--display-city` | `-dc` | Custom display name for city (e.g., "東京") |
| `--display-country` | `-dC` | Custom display name for country (e.g., "日本") |
| `--font-family` | | Google Fonts family name (e.g., "Noto Sans JP") |

**Examples:**

```bash
# Japanese
python create_map_poster.py -c "Tokyo" -C "Japan" -dc "東京" -dC "日本" --font-family "Noto Sans JP"

# Korean
python create_map_poster.py -c "Seoul" -C "South Korea" -dc "서울" -dC "대한민국" --font-family "Noto Sans KR"

# Arabic
python create_map_poster.py -c "Dubai" -C "UAE" -dc "دبي" -dC "الإمارات" --font-family "Cairo"
```

**Note**: Fonts are automatically downloaded from Google Fonts and cached locally in `fonts/cache/`.

### Resolution Guide (300 DPI)

Use these values for `-W` and `-H` to target specific resolutions:

| Target | Resolution (px) | Inches (-W / -H) |
|--------|-----------------|------------------|
| **Instagram Post** | 1080 x 1080 | 3.6 x 3.6 |
| **Mobile Wallpaper** | 1080 x 1920 | 3.6 x 6.4 |
| **HD Wallpaper** | 1920 x 1080 | 6.4 x 3.6 |
| **4K Wallpaper** | 3840 x 2160 | 12.8 x 7.2 |
| **A4 Print** | 2480 x 3508 | 8.3 x 11.7 |

### Usage Examples

#### Basic Examples

```bash
# Simple usage with default theme
python create_map_poster.py -c "Paris" -C "France"

# With custom theme and distance
python create_map_poster.py -c "New York" -C "USA" -t noir -d 12000
```

#### Multilingual Examples (Non-Latin Scripts)

Display city names in their native scripts:

```bash
# Japanese
python create_map_poster.py -c "Tokyo" -C "Japan" -dc "東京" -dC "日本" --font-family "Noto Sans JP" -t japanese_ink

# Korean
python create_map_poster.py -c "Seoul" -C "South Korea" -dc "서울" -dC "대한민국" --font-family "Noto Sans KR" -t midnight_blue

# Thai
python create_map_poster.py -c "Bangkok" -C "Thailand" -dc "กรุงเทพมหานคร" -dC "ประเทศไทย" --font-family "Noto Sans Thai" -t sunset

# Arabic
python create_map_poster.py -c "Dubai" -C "UAE" -dc "دبي" -dC "الإمارات" --font-family "Cairo" -t terracotta

# Chinese (Simplified)
python create_map_poster.py -c "Beijing" -C "China" -dc "北京" -dC "中国" --font-family "Noto Sans SC"

# Khmer
python create_map_poster.py -c "Phnom Penh" -C "Cambodia" -dc "ភ្នំពេញ" -dC "កម្ពុជា" --font-family "Noto Sans Khmer"
```

#### Advanced Examples

```bash
# Iconic grid patterns
python create_map_poster.py -c "New York" -C "USA" -t noir -d 12000           # Manhattan grid
python create_map_poster.py -c "Barcelona" -C "Spain" -t warm_beige -d 8000   # Eixample district

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
python create_map_poster.py -c "Rome" -C "Italy" -t warm_beige -d 8000        # Ancient layout

# Coastal cities
python create_map_poster.py -c "San Francisco" -C "USA" -t sunset -d 10000    # Peninsula grid
python create_map_poster.py -c "Sydney" -C "Australia" -t ocean -d 12000      # Harbor city
python create_map_poster.py -c "Mumbai" -C "India" -t contrast_zones -d 18000 # Coastal peninsula

# River cities
python create_map_poster.py -c "London" -C "UK" -t noir -d 15000              # Thames curves
python create_map_poster.py -c "Budapest" -C "Hungary" -t copper_patina -d 8000  # Danube split

# Override center coordinates
python create_map_poster.py --city "New York" --country "USA" -lat 40.776676 -long -73.971321 -t noir

# List available themes
python create_map_poster.py --list-themes

# Generate posters for every theme
python create_map_poster.py -c "Tokyo" -C "Japan" --all-themes
```

### Distance Guide

| Distance | Best for |
|----------|----------|
| 4000-6000m | Small/dense cities (Venice, Amsterdam center) |
| 8000-12000m | Medium cities, focused downtown (Paris, Barcelona) |
| 15000-20000m | Large metros, full city view (Tokyo, Mumbai) |

## Map Layers

Non-road layers are declared in `layers.json` (edit it to add layers, change OSM tags, z-order, or colors — no code changes needed). The defaults render:

| Layer | OSM tags | Theme color key (fallback) |
|-------|----------|----------------------------|
| `water` | `natural=water/bay/strait`, `waterway=riverbank` | `water` |
| `farmland` | `landuse=farmland/orchard/vineyard` | `farmland` (`parks`) |
| `forest` | `landuse=forest`, `natural=wood` | `forest` (`parks`) |
| `grass` | `landuse=meadow/village_green/recreation_ground`, `natural=grassland` | `grass` (`parks`) |
| `parks` | `leisure=park`, `landuse=grass` | `parks` |
| `rail` | `railway=rail/subway/tram/light_rail` | `rail` (`road_secondary`) |

Existing themes don't define the new keys, so those layers fall back to the colors shown in parentheses. If neither the color key nor the fallback resolves, the layer is skipped.

### Ocean Fill (`--ocean`)

OSM's water tags don't cover the open sea, so coastal posters normally leave the ocean empty. `--ocean` fills it using OpenStreetMap's [pre-built water polygons](https://osmdata.openstreetmap.de/data/water-polygons.html):

```bash
python create_map_poster.py -c "San Francisco" -C "USA" -t blueprint -d 8000 --ocean
```

On first use this downloads a ~800MB global dataset into `cache/` (kept for future runs). The ocean is colored with the theme's `ocean` key, falling back to `water`.

## Themes

17 themes available in `themes/` directory:

| Theme | Style |
|-------|-------|
| `gradient_roads` | Smooth gradient shading |
| `contrast_zones` | High contrast urban density |
| `noir` | Pure black background, white roads |
| `midnight_blue` | Navy background with gold roads |
| `blueprint` | Architectural blueprint aesthetic |
| `neon_cyberpunk` | Dark with electric pink/cyan |
| `warm_beige` | Vintage sepia tones |
| `pastel_dream` | Soft muted pastels |
| `japanese_ink` | Minimalist ink wash style |
| `emerald`      | Lush dark green aesthetic |
| `forest` | Deep greens and sage |
| `ocean` | Blues and teals for coastal cities |
| `terracotta` | Mediterranean warmth |
| `sunset` | Warm oranges and pinks |
| `autumn` | Seasonal burnt oranges and reds |
| `copper_patina` | Oxidized copper aesthetic |
| `monochrome_blue` | Single blue color family |

## Output

Posters are saved to `posters/` directory with format:

```text
{city}_{theme}_{YYYYMMDD_HHMMSS}.{png|svg|pdf}
```

## Post-Processing Scripts

Standalone scripts in `scripts/` (run with `uv run`, no install needed):

```bash
# Add a neon/bloom glow effect to a poster
uv run scripts/add_glow.py posters/my_poster.png --intensity 1.2 --radius 10

# Stripe the same city in different themes into one comparison image
uv run scripts/merge_bands.py noir.png blueprint.png sunset.png -o merged.png
```

Both support `--dryrun`, `--verbose`, and `--force`; see each script's `--help` for all options.

## Adding Custom Themes

Create a JSON file in `themes/` directory:

```json
{
  "name": "My Theme",
  "description": "Description of the theme",
  "bg": "#FFFFFF",
  "text": "#000000",
  "gradient_color": "#FFFFFF",
  "water": "#C0C0C0",
  "parks": "#F0F0F0",
  "road_motorway": "#0A0A0A",
  "road_primary": "#1A1A1A",
  "road_secondary": "#2A2A2A",
  "road_tertiary": "#3A3A3A",
  "road_residential": "#4A4A4A",
  "road_default": "#3A3A3A"
}
```

### Optional Theme Keys

Themes may additionally style the configurable layers (see [Map Layers](#map-layers)):

```json
{
  "rail": "#00FFFF",
  "rail_core": "#FFFFFF",
  "forest": "#1A3A2A",
  "farmland": "#2A3A1A",
  "grass": "#22442A",
  "ocean": "#0A1A2A"
}
```

`rail_core` enables a dual-stroke "neon" look: a narrower line in the core color is drawn on top of the rail line — great for cyberpunk-style themes. All keys are optional; missing keys use the fallbacks described in [Map Layers](#map-layers).

## Project Structure

```text
map_poster/
├── create_map_poster.py    # Main script
├── font_management.py      # Font loading and Google Fonts integration
├── layers.json             # Declarative non-road layer config (tags, colors, z-order)
├── themes/                 # Theme JSON files
├── scripts/                # Standalone post-processing scripts (glow, band merge)
├── fonts/                  # Font files
│   ├── Roboto-*.ttf        # Default Roboto fonts
│   └── cache/              # Downloaded Google Fonts (auto-generated)
├── cache/                  # Cached OSM data + prepared render bundles (auto-generated)
├── posters/                # Generated posters
└── README.md
```


## Hacker's Guide

Quick reference for contributors who want to extend or modify the script.

### Contributors Guide

- Bug fixes are welcomed
- Don't submit user interface (web/desktop)
- Don't Dockerize for now
- If you vibe code any fix please test it and see before and after version of poster
- Before embarking on a big feature please ask in Discussions/Issue if it will be merged

### Architecture Overview

The script is a three-stage pipeline, built for speed on repeat runs:

```text
┌──────────────────┐   ┌───────────────────────┐   ┌──────────────────────┐
│  fetch_layers()  │──▶│ prepare_render_data() │──▶│    render_poster()   │
│ concurrent OSMnx │   │ project + classify +  │   │  pure matplotlib     │
│ fetches, pickle- │   │ convert to matplotlib │   │  (LineCollection /   │
│ cached per layer │   │ primitives; cached as │   │  PathCollection),    │
│                  │   │ one "prepared" bundle │   │  theme passed in     │
└──────────────────┘   └───────────────────────┘   └──────────────────────┘
```

Warm runs (cached prepared bundle) skip stages 1-2 entirely — they never even import osmnx/geopandas. `--all-themes` prepares once and renders every theme in parallel worker processes. Delete `cache/` (or set `CACHE_DIR`) to force a refetch.

### Key Functions

| Function | Purpose | Modify when... |
|----------|---------|----------------|
| `get_coordinates()` | City → lat/lon via Nominatim | Switching geocoding provider |
| `fetch_layers()` | Concurrent OSM downloads (graph + every configured layer) | Changing how data is fetched/cached |
| `prepare_render_data()` | Projection, road classification, polygon/line extraction; builds the cached bundle | Adding data that rendering needs |
| `render_poster()` | Pure matplotlib rendering from the bundle | Changing visual output |
| `_classify_highway()` | Road class label by OSM highway tag | Changing road hierarchy |
| `_theme_road_colors()` / `_BASE_ROAD_WIDTHS` | Road class → color / width | Changing road styling or weights |
| `create_gradient_fade()` | Top/bottom fade effect | Modifying gradient overlay |
| `load_theme()` | JSON theme → dict | Adding new theme properties |
| `load_layers_config()` | Loads `layers.json` (embedded fallback) | Changing layer config handling |
| `is_latin_script()` | Detects script for typography | Supporting new scripts |
| `load_fonts()` | Load custom/default fonts | Changing font loading logic |

### Rendering Layers (z-order)

```text
z=11    Text labels (city, country, coords)
z=10    Gradient fades (top & bottom)
z=1.05  Rail lines (+0.01 for the optional core stroke)
z=1     Roads (single LineCollection)
z=0.8   Parks          ┐
z=0.75  Grass          │ polygon layers; z-order set
z=0.65  Forest         │ per layer in layers.json
z=0.6   Farmland       │
z=0.5   Water          │
z=0.45  Ocean (--ocean)┘
z=0     Background color
```

### OSM Highway Types → Road Hierarchy

```python
# In _classify_highway() and _BASE_ROAD_WIDTHS
motorway, motorway_link     → Thickest (1.2), darkest
trunk, primary              → Thick (1.0)
secondary                   → Medium (0.8)
tertiary                    → Thin (0.6)
residential, living_street  → Thinnest (0.4), lightest
```

### Typography & Script Detection

The script automatically detects text scripts to apply appropriate typography:

- **Latin scripts** (English, French, Spanish, etc.): Letter spacing applied for elegant "P  A  R  I  S" effect
- **Non-Latin scripts** (Japanese, Arabic, Thai, Korean, etc.): Natural spacing for "東京" (no gaps between characters)

Script detection uses Unicode ranges (U+0000-U+024F for Latin). If >80% of alphabetic characters are Latin, spacing is applied.

### Adding New Features

**New map layer (e.g., beaches):** no code needed — add an entry to `layers.json`:

```json
{"name": "beach", "tags": {"natural": ["beach"]}, "color_key": "beach", "fallback_key": "parks", "zorder": 0.7}
```

Polygon layers go in `polygon_layers`; line layers go in `line_layers` and also take `"linewidth"`, plus optional `"core_key"`/`"core_width_ratio"` for a dual-stroke effect. Editing `layers.json` automatically invalidates the prepared-data cache (its hash is part of the cache key), so the next run refetches/rebuilds as needed.

**New theme property:**

1. Add to theme JSON: `"beach": "#F0E0B0"`
2. Reference it as the layer's `color_key` in `layers.json`
3. Themes without the key use the layer's `fallback_key` (or the layer is skipped)

### Typography Positioning

All text uses `transform=ax.transAxes` (0-1 normalized coordinates):

```text
y=0.14  City name (spaced letters for Latin scripts)
y=0.125 Decorative line
y=0.10  Country name
y=0.07  Coordinates
y=0.02  Attribution (bottom-right)
```

### Useful OSMnx Patterns

```python
# Get all buildings
buildings = ox.features_from_point(point, tags={'building': True}, dist=dist)

# Get specific amenities
cafes = ox.features_from_point(point, tags={'amenity': 'cafe'}, dist=dist)

# Different network types
G = ox.graph_from_point(point, dist=dist, network_type='drive')  # roads only
G = ox.graph_from_point(point, dist=dist, network_type='bike')   # bike paths
G = ox.graph_from_point(point, dist=dist, network_type='walk')   # pedestrian
```

### Performance Tips

- Everything is cached in `cache/` (override with the `CACHE_DIR` env var): raw OSM downloads per layer, plus a prepared render bundle per (location, distance, layer-config). Repeat runs skip fetching and preprocessing entirely — regenerating a poster in a new theme takes ~2s instead of minutes.
- `--all-themes` prepares the data once and renders all themes in parallel worker processes.
- Large `dist` values (>20km) = slow downloads + memory heavy.
- Use `network_type='drive'` instead of `'all'` for faster renders.
- Reduce `dpi` from 300 to 150 for quick previews.
