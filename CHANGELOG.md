# Changelog

What changed in the published charts, version by version, newest first.

### Version numbers

Each version is a GitHub release, tagged `AC-V<major>.<minor>.<patch>`. A new version is published every time the charts or the files in `downloads/` change.

- **Major** version (**x**.0.0): a redesign of the charts, or a file format that changes in a way that breaks what reads it.
- **Minor** version (1.**x**.0): new charts, a new tool or document, or a visible change to every chart.
- **Patch** version (1.2.**x**): corrections to some charts or files.

## Unreleased

**Charts rebuilt from the same data are identical, byte for byte.**

- The PDFs no longer record the time they were written: a chart made again from the same data gives the same file, so a new release changes only the charts that really change. Nothing visible changes.

**Taxiway signs: each view is taken after a fixed wait.**

- `capture/signcapture.py capture`: a view is now taken once, after a fixed wait (3 s by default, `attente_vue_s` in `config.json`), instead of waiting for two identical screenshots: grass, trees and clouds move all the time in the free-camera view, so no view was ever found stable and each one took about 14 s. A view now takes about 4 s. `index.json` records the wait used (`attente_s`); `instable` now only means that DCS could not be brought to the foreground.

**Game facts now come from the DCS mission editor itself.**

- Airfields, runways, tower radios, beacons and parking stands of Syria, Cold War Germany and the Caucasus are now read from the DCS World 2.9.32 mission editor and kept in `render/data/game/`; aircraft sizes too. pydcs is no longer a dependency, so a DCS update can be followed the same day. Syria and the Caucasus do not change. Cold War Germany follows DCS 2.9.32: 20 new airfields and the changed parking stands appear in `downloads/parkings/` and `downloads/navaids/`; its charts are rebuilt in a later release.
- The other theatres in `downloads/parkings/` and `downloads/navaids/` are unchanged (still from pydcs 84f6335) until they are read in the game.
- Charts drawn from now on no longer mention pydcs in their footer; pages already published keep theirs until they are redrawn.

**Captures check the DCS window before and after every screenshot.**

- All captures (charts, context, map tiles, taxiway signs) now read the size and place of the DCS window before and after each screenshot. If the window has moved or shrunk (seen on a two-screen PC, where Windows sometimes moves it to the other screen or narrows it), the screenshot is discarded, the window is put back where `fenetre_dcs` in `config.json` says, and the screenshot is taken again. Without `fenetre_dcs`, a window that no longer covers the primary screen stops the capture with a message. Each event is written to the log as a `FENETRE` line.

## AC-V1.12.0 — 26 November 2026

**A website to find, view and download the charts.**

- https://mirabellebenou.github.io/dcs-airfield-charts/ — search an airfield or a heliport by name or ICAO code, browse each theatre, see every page of an airfield before you download it, and get its PDF, its LotAtc files and the theatre zips. Every heliport has its own page with its page of the heliport directory.
- The site is built from the files of each release and published with it. The published charts do not change.
- The reading guide can be read on the website too, one page per chapter, with the same figures and notes as the PDF: https://mirabellebenou.github.io/dcs-airfield-charts/guide/

**The Chukurova NDB shows its frequency in kHz.**

- DCS gives the Chukurova NDB (`CKR`) at 320,000,000 Hz, a thousand times too high: its pages showed "320000". They now show "320" (kHz), like every other NDB, and `downloads/navaids/Syria.json` gives 320000 Hz. An NDB that DCS gives at 2 MHz or more is read the same way on every theatre.
- `render/republish.py` corrects the pages of a published release.
- Known issue: in the game, the beacon keeps the frequency of the game files; it may not be received on 320 kHz.

**The reading guide no longer says "overleaf".**

- In chapter 7, the label of the enlarged view reads "11–12 · ENLARGED": in the PDF the enlargement is on the next page, on the website just below.

**Every release page explains the version numbers.**

- The notes of a release end with the "Version numbers" paragraph of this changelog; the release workflow checks it.

**Downloads.**

- The Cold War Germany zips of AC-V1.11.0 are attached again, unchanged. The Syria zips are those of AC-V1.11.0 with the pages of Chukurova corrected; in `downloads/`, only the Chukurova PDF and the reading guide change.
- The two LotAtc zips are built again for this version: only their `README.txt` changes.

## AC-V1.11.0 — 23 November 2026

**LotAtc drawings of every charted airfield, for air traffic controllers.**

- New tool `render/build_lotatc.py`: from the airfields and helicopters zips of a release, writes for every airfield and helicopter base of the theatre its LotAtc drawing layers: the plan (pavement outline, runways, runway designators and taxiway names, as on the 10-9 chart) and the parking stands (named as in the mission editor), at their positions in DCS. One zip per theatre, `DCS_Airfield_Charts_<Theatre>_1985_lotatc.zip`, ready to copy into the LotAtc server's drawings folder. The published charts do not change.

**The Squadron Ready Room insignia on the reading guide and the README.**

- The cover of the reading guide carries the Airfield Charts insignia in colour, and every other page a small black pictogram before the footer text. The charts do not change.
- The README opens with the Airfield Charts banner, and credits the insignia and its lettering (Oswald, SIL Open Font License 1.1).

**The Squadron Ready Room pictogram at the foot of every chart.**

- Every chart page (10-8, 10-9, 11-x) and every page of the heliport directory carries a small black pictogram before the footer text. Nothing else changes on the charts.
- `render/republish.py` adds it to the charts of a published release; a page that already carries it is left as it is.

**Downloads.**

- The four zips of the charts are those of AC-V1.10.0 with the pictogram at the foot of every page; the PDFs in `downloads/` and the reading guide are rebuilt from them.
- Two new zips, `DCS_Airfield_Charts_Syria_1985_lotatc.zip` and `DCS_Airfield_Charts_GermanyColdWar_1985_lotatc.zip`: the LotAtc drawings of every charted airfield. Their colours and the rendering of their texts in LotAtc have not been checked by a controller yet.

## AC-V1.10.0 — 22 November 2026

**A reading guide for the charts.**

- New `downloads/DCS_Airfield_Charts_Reading_Guide.pdf` (A4, English): how to read every page of an airfield — vicinity, airfield, parking, helicopter bases and the heliport directory, aircraft and helicopter approaches, minimum sector altitudes — on the annotated pages of Konya (Syria); then how to fly with the charts, and which approach pages each of nine aircraft can fly (Mi-24P, UH-1H, AH-64D, OH-58D, MiG-21bis, F-5E, Mirage F1EE, F-4E, A-10C II). The charts do not change.
- New tool `render/build_guide.py`: rebuilds the guide from the zips of a release, so it always shows the pages of that release; it stops when a page it annotates no longer has what a note points at.
- The example charts in the README now come from release AC-V1.9.0: they still showed the first, French, layout.
- **Downloads**: the four zips of AC-V1.9.0 are attached to the release again, unchanged.

## AC-V1.9.0 — 21 November 2026

**An approach chart for every runway with a beacon, for aircraft and for helicopters.**

- Every airfield of Syria and Cold War Germany whose own beacon (VOR/DME, VOR, NDB, TACAN or RSBN) is within 3 NM now has an instrument approach page **for each runway direction** (`11-1`, `11-2`…), built on the DCS beacons and on the ground elevation read in DCS by standard patterns: holding, outbound leg, base turn or racetrack, final on the runway axis, missed approach, and the minimum sector altitudes. Where the ground along the approach would put the final approach fix more than 10 NM out, that runway direction has no approach page. Runways with an ILS keep their ILS final inside the full procedure; the former ILS-only pages of these airfields are replaced. No minima: DCS gives none.
- **Helicopter pages** (`11-1H`, `11-2H`…, in the *helicopters* zip and in the airfield PDF) for every airfield with an NDB: NDB only, one altitude for the whole procedure, 80 kt · 150 km/h, heights above the airfield in metres (QFE) beside altitudes in feet, distances in NM and km, landing on the runway or visually on a helipad of the 10-9.
- The base turn and the intermediate approach keep clear of the runways; the holding and the outbound leg may pass over the airfield, never lower than 1,000 ft above it. When the beacon is off the runway axis, the page gives the bearings to the beacon (QDM) to check at the final approach fix and at the threshold.
- New tool `render/build_approaches.py`: rebuilds the two zips of a theatre from the zips of the previous release, the DCS beacons and the relief grid, with no F10 capture.

**Terrain relief as web map tiles.**

- New tool `render/build_relief.py`: from the ground elevation read in DCS on a 100 m grid (the `relief` step of the unattended night), builds for a theatre a hillshade layer — transparent, lit from several directions, to lay over any map — and an elevation layer in the Mapbox Terrain-RGB format (0.1 m), as web map tiles from zoom 6 to 11, packed in one zip with a description of both layers. The published charts do not change.

**Minimum sector altitudes (MSA) of every airfield.**

- New data in `downloads/msa/` (Caucasus, Cold War Germany, Syria): for each airfield, heliport and helipad of the mission editor, the minimum sector altitude within 25 NM, in four sectors by magnetic bearing to the airfield, computed on the ground elevation read in DCS — 1,000 ft above the highest ground of the sector, 2,000 ft where it reaches 1,500 m, rounded up to 100 ft. Ground only: obstacles are not included. New tool `render/build_msa.py`. The published charts do not change.
- Terrain relief: the ground elevation read in DCS now covers every airfield of Syria and Cold War Germany, beyond the theatre bounds given by pydcs; the relief tiles cover the whole grid, and the hillshade is left transparent next to areas that were not read.

- Relief and MSA tools: a malformed input is now refused with a clear message (a CSV value such as `1_0` names its line; an airfield file without positions is refused instead of crashing), and building the relief grid uses less memory. No data changes.

**Map tiles: uniform areas no longer stall the capture.**

- `capture/f10tiles.py capture`: a cell where the screen does not change after the camera moves (open sea, flat desert: the map is one colour from one cell to the next) is now taken once the screen has stayed still for 3 s, instead of being rejected after 10 s as not stable — such a grid could never be completed. `captures.jsonl` records it (`unchanged`) with the time of the first change seen on the other cells (`first_change_s`), and the log gives their number (`inchangees <n>`).

## AC-V1.8.0 — 20 November 2026

**Vicinity page 10-8 for the eight helicopter bases of Syria.**

- At Tanf, Kharab Ishk, Lakatamia, Marj as Sultan North, Marj as Sultan South, Naqoura, Qabr as Sitt and Taftanaz now open with their vicinity page 10-8, like the airfields: the lightened F10 map around the base, with its runways and their headings, its beacons and the towns within 20 km. Their other pages do not change.

**Airfield plans for DCS Mission Plan, and hardened shelters of the parking stands.**

- New tool `render/build_plans.py`: from the airfields and helicopters zips of a release, writes for every airfield and heliport chart its 10-9 plan reduced to pavement, runways, runway markings and buildings (no text), with an index giving the scale and offset that place each plan on the DCS map frame. DCS Mission Plan draws the parking stands of an airfield on this plan.
- New files `downloads/parkings/shelters/GermanyCW.json` and `Syria.json`: for every airfield and heliport charted in Cold War Germany and Syria, the parking stands drawn as hardened shelters on its chart. The published charts do not change.

**More taxiway position signs are read on large airfields.**

- `capture/signread.py` finds and reads more of the yellow-on-black taxiway position signs: a looser yellow test for the thin sign frame, a second ranking of the candidate frames by what lies inside them, reading of signs seen at an angle (not square on the image), and lower acceptance thresholds for signs 10 px tall or more, still never without a clear margin over the second-best letter. On a set of 31 views of a large airfield, 13 letters out of 23 are now read instead of 6, with no wrong letter; signs under 10 px tall are still not read. The published charts do not change.

**Map tiles from the DCS World F10 map (work in progress).**

- New `capture/f10tiles.py`: captures an area (a GeoJSON extent or a latitude/longitude rectangle) of the F10 map in a grid at the scale of a target web-map zoom, resumes an interrupted pass, then reprojects the captures to 256 px XYZ tiles (Web Mercator), builds the lower zoom levels and packs the tiles in tar parts with a manifest and a layer description. Two layers: `alt` (PNG, 256 colours) and `sat` (WebP). It also turns the airfield captures of the charts into zoom 17 and 18 tiles. The F10 camera scale above about 5,200 m follows a measured law, used to set the camera altitude; the map mode is checked on the first image.
- `capture/autonome/nuit.py`: a new `tuiles` step runs such a capture and its tiling in an unattended night.

**Terrain survey in the unattended night.**

- The night hook no longer stops a terrain survey on its first line: DCS returns nothing from a successful file write, which the hook took for a failure. A write now fails only when it reports an error.

## AC-V1.7.1 — 18 November 2026

**Taxiway names that found no place now get one.**

- A taxiway name that fits nowhere along its taxiway gets a label set apart and joined by a line, as before; when the middle of the taxiway lies inside an area labels must avoid (a runway, typically), or every line from it is blocked, the line now starts from another point of the taxiway.

**Approach pages for ILS runway ends far from the taxiways.**

- When no taxiway point lies within 250 m of a runway threshold, the threshold elevation is now taken from the nearest taxiway points up to 1,000 m away, instead of being left unknown. The runway table shows it, and an ILS runway end in that case gets its approach page.

**Navaid labels, graticule ticks and taxiway labels no longer overlap.**

- A navaid label avoids the latitude/longitude ticks on the edge of the chart, as it already avoided runways and runway bearings; republishing a chart applies the same rule.
- A taxiway label avoids the navaid symbols and their labels.

**Taxiway names are no longer dropped.**

- When a taxiway name finds no room at its usual place, it is tried along every line of that taxiway, then in a smaller size (5 pt), then next to the taxiway with a thin leader line. A name that still finds no room makes `render/build_chart.py` end with exit code 3, after writing the charts, instead of being left out silently. Labels that already had room do not move.

**Terrain survey during unattended nights.**

- New queue step `relief` for `capture/autonome/nuit.py`: the hook reads the ground elevation (and optionally the surface type) of DCS on a square grid and writes it to a CSV file, with the reading speed. The published charts do not change.

**Finished branches are deleted by a workflow.**

- New workflow `.github/workflows/menage-branches.yml` with `tools/menage_branches.py`: once a day, a branch whose last commit is at least 7 days old is deleted if it is finished — its whole content is already in `main`, its release tag exists, or it is a release trial. Every other branch is kept, and every decision is listed on the run page. Run by hand, the workflow only lists its decisions. The published charts do not change.

**Known issue.**

- A few taxiway names still find no room and are missing: on the 10-9, Cologne T2, Frankfurt L3 and S25, Herzliya K2, Kastrup A10, Leipzig Halle S6 and Schonefeld G; on the 10-8 only, some names at Chukurova, Gazipasa, Hamburg, HildesHeim, Konya, Larnaca and Uetersen.

## AC-V1.7.0 — 15 November 2026

**Releases are published by a workflow.**

- New workflow `.github/workflows/release.yml` with `tools/release.py`: pushing a branch `release/AC-V<x.y.z>` with the release text, the zips and a manifest creates the release and its tag, with the zips attached. A zip larger than a git push allows is pushed in numbered parts and joined by the workflow. The published charts do not change.

**Charts in English.**

- Every text on the pages is now in English: headers, runway table, legends, footer, approach pages and heliport directory, with the abbreviations of real-world charts (RWY, THR ELEV, MAG HDG, CRS, GP, NAVAIDS, ELEV). Decimals use a point; thousands have no separator.

**A page showing the airfield in its surroundings.**

- New page `10-8`, placed before the `10-9`, for every airfield with a wider F10 capture (`capture/f10capture.py capture --marge`): the F10 map around the airfield, lightened, with the runways, their headings, the radio beacons and the taxiway names, without parking. Towns less than 20 km from the airfield are marked at the edge of the frame with their distance, or by name when they are inside it.
- On every page drawn over the F10 map, text has a white halo.
- The `10-8` is in this release for the 73 airfields of Syria and the 118 airfields of Cold War Germany, and for Landstuhl; not yet for the 8 helicopter bases of Syria.

**Unattended night captures (Windows).**

- New tool `capture/autonome/nuit.py`: a queue of captures written in advance (F10 captures for the charts, wider F10 context captures, taxiway sign views) runs with nobody at the PC once the mission is flying, within a time budget. The new GUI hook `capture/hook/ChartsAutonome-hook.lua` switches between the F10 map and the free camera, starts the queue and closes DCS at the end.
- The files of the night are packed into zip files with a report, optionally uploaded as the assets of a release of a private GitHub repository; the PC can then shut down.
- The published charts do not change.

**An approach page for every ILS runway in DCS.**

- New page `11-1`, `11-2`… after the airfield pages, one per runway end with an ILS in DCS: localizer frequency and course as read on the cockpit compass, threshold elevation, a plan view of the final approach over 10 NM (game north up), a 3° profile and a distance and altitude table, with the distances read on the nearest DME. It is not a real approach chart: DCS gives no procedure and no minima.
- `render/build_chart.py --approches-seulement` renders only these pages; `render/republish.py --ajouter <folder>` adds them to the charts of a release zip, without the F10 captures.

**Cold War Germany: the grass strips are charted.**

- 24 grass strips that had no chart (the F10 map draws no runway there) now have one. Their runway is the one DCS declares for the airfield: centre, heading, length and width as the game gives them. It is drawn as an outline, without markings, and the runway table says GRASS RUNWAY.
- Landstuhl, whose declared runway is 10 m long, is charted as a helicopter base, on the F10 map background.

**ICAO codes and radio beacons, airfield by airfield.**

- New files in `downloads/navaids/`, one per DCS theatre, give every airfield of the mission editor its ICAO code when it has one (today's codes, from OurAirports, matched by position) and, for Caucasus, Cold War Germany and Syria, the radio beacons DCS ties to it: type, callsign, frequency or channel, and the runway end an ILS serves; the en-route beacons are listed apart. New tool `render/export_navaids.py` writes them.

**A wider F10 capture around an airfield.**

- `capture/f10capture.py capture <Theatre> <Base> --marge <m>` captures the F10 map over the airfield's extent plus `<m>` metres on each side, into `out/<theatre>/contexte/<Base>/`, without touching the captures the charts are rendered from. The published charts do not change.

**Taxiway names read in DCS.**

- New command `capture/signcapture.py attribuer`: the letters read on the taxiway signs at the runway exits are placed on the ground and given to their exit, into `taxiways_dcs/<theatre>/<Base>.json`. When that file exists for an airfield, its chart takes the taxiway names from the game instead of OpenStreetMap. The published charts do not change until such files are produced.
- A taxiway that ends on the runway centreline without a runway node now counts as a runway exit for the letters read there, when it is at least 100 m from the other exits (Mahlwinkel: its B).

## AC-V1.6.0 — 7 November 2026

**Magnetic headings now match the cockpit compass.**

- Runway headings, on the map and in the runway table, are now the DCS grid heading minus the magnetic variation of the chart year: what the magnetic compass reads in the game, where the map grid north is true north. The previous charts added the grid convergence and were off by 5° to 11° in Cold War Germany and by up to 4° in Syria.
- North arrows: the thin arrow now shows the game's north (the map grid, straight up the page) and the solid arrow magnetic north. The variation value does not change.
- Runway bearing, navaid and grid labels are placed again around the new headings, by the same rules.
- `render/republish.py` applies the new headings and north arrows to the charts of a release zip, without the F10 captures.
- Heliport directory, Syria: on seven boxes (HS03, HS07, HS12, HS13, HS17, HT02, H_med_orig_09), the nearest-town line ran into or close to the pad dimensions; it is now set smaller, just enough to leave a gap before them. The other boxes and the index pages do not change.
- `render/republish.py` also corrects the heliport directory of a *helicopters* zip, on its JPEG pages and in its PDF, without the F10 captures.

## AC-V1.5.1 — 6 November 2026

**ICAO codes and taxiway names in Cold War Germany, scale bars, label corrections, and the parking stands of every DCS theatre.**

- Cold War Germany: 62 airfields now carry their ICAO code, in the chart header and in the file names (`EDDF_Frankfurt_10-9.png`), and 39 airfields their taxiway names from OpenStreetMap (364 names; data © OpenStreetMap contributors, ODbL). The other 32 airfields are unchanged.
- Scale bars: the end of every scale bar now carries its length in metres. On 64 charts the bar ended between two labelled marks (750, 1,250 or 1,750 m) and had no "M" at all.
- At Tanf, Kharab Ishk and Naqoura: the PNG pages are 256-colour images, 0.9 to 1.4 MB instead of 1.9 to 3.7 MB, with no visible change; in the PDF, viewers smooth the map image when zooming; the SVG shows its map image in older Inkscape versions too.
- New tool `render/republish.py` applies every correction to the charts of a release zip, without the F10 captures.
- Runway bearing labels: a label that did not fit inside the frame, or touched another label, a runway or a navaid, now runs along the runway beside its threshold (115 labels on 85 charts, and runways 34 and 35 at Beirut).
- Navaid labels that crossed a runway now sit to the left of, below or above their symbol (13 labels on 13 charts).
- Latitude and longitude labels on the frame: a label that crossed the frame now slides inside it; a label that touched an inset, the north arrow, a bearing or a navaid label is left out, and its tick stays.
- Parking stands: new files in `downloads/parkings/`, one per DCS theatre, give every parking stand with the identifiers a mission file uses (`airdromeId`, `parking`, `parking_id`), its position and the aircraft it takes (helicopters, airplanes, length, width, height), from the DCS mission editor through pydcs; `aircraft.json` gives the size of every aircraft type. New tool `render/export_parkings.py` writes them.
- **Downloads**: the four zips attached to the release again; the Cold War Germany helicopters zip is unchanged.

## AC-V1.5.0 — 31 October 2026

**Coordinates in degrees-minutes-seconds and MGRS on every chart.**

- The header line of every chart page now gives the airfield reference point three ways: degrees and decimal minutes (as before), degrees-minutes-seconds, and MGRS to 10 m (for example `32U PD 9233 0798`). Nothing else on the charts changes.
- The heliport directories do not change: their positions stay in degrees and decimal minutes.
- New tool `render/update_coords.py` brings the coordinates line of published charts up to date from the release zips, without the F10 captures.
- **Downloads**: the four zips attached to the release again; the airfields and helicopters zips carry the updated charts, the heliport directories are unchanged.

## AC-V1.4.1 — 28 October 2026

**Cold War Germany: the heliport directory.**

- **Heliport directory**: the 108 heliports of Cold War Germany, four to a page (30 pages of plans and 3 of index, 33 in all), with a plan of the pads and their numbers, position, elevation and nearest town. Nearest towns come from the German list of the town file.
- Fix: the directory looked up the towns of Cold War Germany under the wrong key and stopped with an error; the theatre name `GermanyColdWar` now maps to its town list. Syria does not change.
- **Downloads**: a fourth zip attached to the release, **Cold War Germany helicopters** (the directory as PDF, and its pages as JPEG for the kneeboard). The other zips are attached again, unchanged.

## AC-V1.4.0 — 28 October 2026

**Cold War Germany: 94 airfields.**

- **94 new charts**, one PDF per airfield with its parking pages, from Ramstein, Hahn, Bitburg, Spangdahlem and Frankfurt to the Warsaw Pact bases (Templin, Sperenberg, Wittstock, Laage, Altes Lager…), Kastrup and the motorway strips (Adelsheim, Thurland, Zollschen, Perwenitz). Magnetic declination for 1985.
- The header names the theatre "GERMANY COLD WAR".
- Each chart carries the DCS name of the airfield: ICAO codes and taxiway names from OpenStreetMap come in a later version.
- The 25 grass strips (the F10 map draws no runway there) and the helicopter sites are not charted yet.
- On every theatre, grey areas of the F10 map that are not part of the airfield (motorway slip roads, for instance) are no longer drawn as pavement; at Bitburg this brings the plan to a larger scale. The Syria charts do not change.
- **Downloads**: a third zip attached to the release, **Cold War Germany airfields**. The two Syria zips are attached again, unchanged.

## AC-V1.3.2 — 27 October 2026

Syria: **navaids checked against the game files**, on 19 charts.

- **ILS**: each localizer is now placed on its runway end from its position and course in the game's beacon file. 12 ILS that were missing now appear (among them Beirut 03 and 17, Gaziantep 28, Hatay 04, Sanliurfa 04, Marka 24, Prince Hassan 31, King Abdullah II 31, Ben Gurion 26, Hatzor 05, Chukurova 03R and 21L), and two were on the wrong end (Ben Gurion 08, Nevatim 08R). The "0.00" shown as an ILS at Bassel Al-Assad 35R is gone.
- **Navaids on or next to the airfield** that the game does not tie to it are now in the header band: Kahramanmaras (VOR/DME and NDB KHM), Rene Mouawad (NDB RA), Kingsfield (NDB DKA), Muwaffaq Salti (VORTAC GHI), At Tanf (VOR/DME TAN).
- Navaids drawn close together on the plan share one label (Hatay, Sanliurfa, Chukurova, Kahramanmaras).
- Runway names follow the game: Marj Ruhayyil 06L/24R, Tha'lah 05R/23L.
- Thanks to Minsky's *Aerodrome Data and Frequencies*, which we used to cross-check the charts.

## AC-V1.3.1 — 27 October 2026

Syria: the three heliports with a tower radio are now drawn **on the F10 map**.

- **At Tanf, Kharab Ishk and Naqoura**: their plan showed only pavement and buildings, without roads, berms or camp, and was hard to read. It now shows the F10 map, softened, at about 1:5,000, as in the heliport directory. Each pad is circled in red at its real size, with its DCS number.
- The north arrow and the scale bar sit on a white box, so they can be read on the map.
- The other 78 charts and the heliport directory do not change.
- **Downloads**: the single zip is replaced by two zips attached to the release, **airfields** (73 airfields) and **helicopters** (the 8 helicopter bases and the heliport directory). They are no longer stored in the repository.

## AC-V1.3.0 — 26 October 2026

Syria: **helicopter bases and heliports**.

- **7 new charts** for the helicopter bases: Marj as Sultan South (Der Salman in the DCS files), Marj as Sultan North, Qabr as Sitt and Taftanaz, whose short runways are now found, and At Tanf, Kharab Ishk and Naqoura, the heliports with a tower radio (no runway table on these three). Each one is titled "BASE D'HÉLICOPTÈRES" (the charts are in French), counts its pads ("AIRES"), and every pad has its DCS number.
- **Heliport directory**: the 141 other heliports of Syria in one PDF, four to a page. Each has a plan of the F10 map with its pads circled and numbered, its position, elevation, pad sizes and the nearest town (for example "6,5 km N de Nazareth": 6.5 km north of Nazareth). Sites larger than a box take two; sites in two separate groups (HI17) show both. An index at the front gives the page of every heliport.
- **Scale bar fixed** on 11 small airfields (Gulechoba, Haifa, Ilker Karter, Kingsfield, Kiryat Shmona, Lakatamia, Rosh Pina, T2, Tal Siman, Teyman, Wujah Al Hajar): it only showed "0 FT" and "0"; it now has 50 or 100 m and 200 or 500 ft steps, each one labelled.
- Lakatamia, whose spots are all for helicopters, is now titled "BASE D'HÉLICOPTÈRES" too.

## AC-V1.2.0 — 26 October 2026

Syria, 74 airfields, all charts re-rendered.

- **Parking numbers**: in a row of three or more parking spots, the numbers are now all on the same side of the row, at right angles to it, instead of alternating above and below.
- **Hardened shelters**: the shelters are now solid. The numbers that the F10 map prints inside them used to leave white holes.
- Parking names that DCS repeats at some bases (Ramat David has seven spots named "63") are kept as the game names them.

Thanks to Flappie for the feedback on the SVG files.

## AC-V1.1.0 — 26 October 2026

Syria, **74 of the 80 airfields** (43 new ones).

- **Runways follow the game**: the number of runways and their names now come from the DCS runway list, so every chart shows the runways DCS has, no more and no fewer.
- **Runways with no threshold markings** on the F10 map are now drawn: they end where the pavement ends.
- **Corrected**: Hama (its runway was drawn 288 m long) and Nevatim (a runway was named 08C/26C).
- Airfields without a tower radio in DCS (Gulechoba) show dashes in the frequency band.
- Not yet covered: the four helicopter bases with no runway on the F10 map, T3 (not drawn by the F10 map) and Nicosia (not listed as an airfield by pydcs).

## AC-V1.0.0 — 26 October 2026

First published charts: **Syria, 31 airfields**.

- One A5 PDF per airfield: the airfield chart (10-9), plus parking pages (10-9A, 10-9B…) when the parking numbers need a larger scale.
- One zip with every page as PDF, PNG (1748 × 2480 px, for the DCS kneeboard) and SVG (Inkscape layers).
- Magnetic declination for 1985.
- Taxiway names from OpenStreetMap, where it has them.
