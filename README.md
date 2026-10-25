# DCS Airfield Charts

Airfield charts for DCS World, built from the game's own data.

**Status: work in progress.** The DCS F10 map (Alt mode) draws all the pavement of an airfield as flat fills, along with buildings, guidance lines and parking numbers. This repository contains the tool that captures that map, base by base, in the DCS terrain frame (`x` North, `z` East, in metres). The whole Syria theatre is captured (80 airbases, 144 heliports). The renderer, which turns a capture into an A5 chart (page 10-9, plus parking pages 10-9A, 10-9B… when needed), is in `render/` (see "Rendering charts").

The goal is to share a complete set of charts for every airfield, as PDF and PNG, ready for print and for the DCS kneeboard.

## Work in progress: first charts

Early mock-ups, generated with no manual drawing: pavement and buildings are vectorised from the F10 capture; runways are detected from their markings; runway designators, parking positions and numbers, and tower frequencies come from the game data (read through [pydcs](https://github.com/pydcs/dcs)). Hardened shelters are recognised on the map. **Not final** — text sizes, taxiway names, magnetic bearings, ILS/TACAN data and the page layout are still being worked on.

| Konya (Syria) | Ramat David (Syria) |
|---|---|
| ![Konya, work in progress](docs/examples/konya_wip.png) | ![Ramat David, work in progress](docs/examples/ramat_david_wip.png) |

Detail view, Konya main apron:

![Konya apron detail, work in progress](docs/examples/konya_detail_wip.png)

## Contents

| Path | Role |
|---|---|
| `capture/hook/ChartsCaptureHook.lua` | DCS export hook: a small local server (`127.0.0.1:47123`) that places the F10 camera directly above a point, with its axes aligned to the DCS grid; releases the camera after 5 s without a command |
| `capture/f10capture.py` | captures a whole base: extent read from its `.rn4`, camera altitude adapted to the terrain, scale calibration (falls back to the predicted scale if calibration fails), image grid, mosaic; `index <Theatre>` lists every airbase and heliport of a theatre; `theatre <Theatre>` captures them all in one resumable pass (about 45 min for Syria) |
| `capture/f10probe.py` | the original probe (link, stabilisation, scale, alignment) |
| `render/` | the renderer: vectorises a captured base (pavement, buildings, runways), reads the game facts through pydcs, lays out the A5 pages and exports SVG, PDF and PNG; `build_chart.py` renders a base, `fetch_osm.py` downloads real taxiway names from OpenStreetMap; fonts in `render/fonts/` |
| `tests/` | `python -m pytest`; the tests that need captured samples or DCS files run when `DCS_CHARTS_SAMPLES` points to a folder holding them, and are skipped otherwise |

## Installation (Windows)

1. Copy `ChartsCaptureHook.lua` to `Saved Games\DCS...\Scripts\ChartsCapture\`.
2. Append to the end of `Saved Games\DCS...\Scripts\Export.lua`:
   `dofile(lfs.writedir()..[[Scripts\ChartsCapture\ChartsCaptureHook.lua]])`
3. Python 3.12+ with `numpy`, `mss`, `pillow`.
4. Set up the configuration (see below).
5. Start a mission on the theatre you want, open the F10 map in Alt mode, hide labels and units, keep DCS in the foreground; then run `python -I capture/f10capture.py capture <Theatre> <Base>`.

### Configuration

Copy `capture/config.example.json` to `capture/config.json` (ignored by git) and adapt it to your PC:

| Key | Meaning |
|---|---|
| `work_dir` | working folder (required): the scripts write `out/` and `sync/` there; created if it does not exist |
| `dcs_install` | DCS World installation folder (required): terrains are read from `<dcs_install>/Mods/terrains`, which must exist |
| `screen` | screen size in pixels, `[width, height]` (optional: it can be left out, in which case the size of the primary screen is used) |
| `annee` | rendering only (optional, default `1985`): year used for the magnetic declination; a decimal such as `1987.5` is accepted |
| `osm_dir` | rendering only (optional, default `<work_dir>/osm`): where the OpenStreetMap taxiway names are read and written |

The file is looked up in this order: the `--config <path>` command-line option, otherwise the `DCS_CHARTS_CONFIG` environment variable, otherwise `capture/config.json`. `~` is expanded. If the file is missing or incomplete, the script stops with a message saying so.

## Rendering charts

Dependencies: Python 3.12+ and `pip install -r requirements.txt`. The game facts (airfield, runway headings, parking slots, tower frequencies) are read with [pydcs](https://github.com/pydcs/dcs), pinned to one commit and imported as a library; the navaids are read from `<dcs_install>/Mods/terrains/<Theatre>/beacons.lua`. [CairoSVG](https://cairosvg.org/) needs the Cairo library: on Linux and macOS it comes with the system packages; on Windows install the GTK runtime, or run the renderer under WSL.

```
python -I render/build_chart.py <Theatre> <Base> [--annee 1985] [--icao LTAN] [--osm <file>] [--out <folder>] [--config <path>]
```

Input: the captured base folder `<work_dir>/out/<theatre>/<Base>/` (`mosaic.png`, `base.json`, `tiles.json`). Output, in `<work_dir>/charts/<theatre>/` unless `--out` is given: `<ICAO>_<Base>_10-9.{svg,pdf,png}`, the same for the parking pages `10-9A`, `10-9B`…, and the collected `<ICAO>_<Base>_charts.pdf`. Pages are A5 portrait; PNGs are 1748 × 2480 px (300 dpi). `--annee` sets the year of the magnetic declination (IGRF-14, 1 July of that year; default **1985**, or the `annee` key of `config.json`). Without `--icao` and without an OpenStreetMap file the base name replaces the ICAO code.

Real taxiway names come from OpenStreetMap (optional): `python -I render/fetch_osm.py <Theatre> [--bases A,B…] [--force]` queries the Overpass API once per captured base and writes `<osm_dir>/<theatre>/<Base>.json`; a base already fetched is skipped unless `--force`, a failure does not stop the pass, and `run.json` in the same folder gives the outcome. Without this file, charts have no taxiway labels.

The SVG pages are organised as Inkscape layers (Pavement, Runways, Parking numbers, Taxiway labels…, one sub-layer per parking inset), so a layer can be hidden or edited without touching the rest; editing an SVG needs the Barlow Condensed font installed (the `.ttf` files are in `render/fonts/`).

Tests: `python -m pytest` (see the table above for the tests that need samples).

Credits and licences: [pydcs](https://github.com/pydcs/dcs) (LGPL, imported, no code copied); [ppigrf](https://github.com/IAGA-VMOD/ppigrf) for the IGRF-14 geomagnetic field; taxiway names © OpenStreetMap contributors, [ODbL 1.0](https://www.openstreetmap.org/copyright), attribution printed on the charts that use them; [Barlow Condensed](https://github.com/jpt/barlow) (SIL Open Font License 1.1, `render/fonts/OFL.txt`); [CairoSVG](https://cairosvg.org/) (LGPL) and [pypdf](https://github.com/py-pdf/pypdf) for the exports.

## What we measured

- Image aligned with the DCS grid (deviation < 0.01°), seams between images ≤ 1 px, about 1 s per image.
- Scale: 0.33555 mm/px per metre of camera altitude. **DCS keeps the F10 camera at least ~500 m above the ground**; hence the altitude `max(1350, ground + 700)` and a calibration for each base.

## Licence

MIT — see `LICENSE`.

## Acknowledgements

The principle — driving the F10 camera from `Export.lua` and capturing the screen — comes from the map generator of [DCS Olympus](https://github.com/Pax1601/DCSOlympus). No code is taken from it.

DCS World is a trademark of Eagle Dynamics SA. This project is neither made nor endorsed by Eagle Dynamics.
