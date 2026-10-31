# Changelog

What changed in the published charts, version by version. Each version is a GitHub release (tag `AC-V<major>.<minor>.<patch>`). A new version is published every time the charts in `downloads/` change.

- **Minor** version (1.**x**.0): new charts, or a visible change to every chart.
- **Patch** version (1.2.**x**): corrections to some charts.

## Coming next

- Cold War Germany: ICAO codes and taxiway names from OpenStreetMap; the 25 grass strips.

## Unreleased

**Scale bars, and the three helicopter bases drawn on the F10 map.**

- Scale bars: the end of every scale bar now carries its length in metres. On 64 charts the bar ended between two labelled marks (750, 1,250 or 1,750 m) and had no "M" at all.
- At Tanf, Kharab Ishk and Naqoura: the PNG pages are 256-colour images, 0.9 to 1.4 MB instead of 1.9 to 3.7 MB, with no visible change; in the PDF, viewers smooth the map image when zooming; the SVG shows its map image in older Inkscape versions too.
- New tool `render/republish.py` applies every correction to the charts of a release zip, without the F10 captures.
- Runway bearing labels: a label that did not fit inside the frame, or touched another label, a runway or a navaid, now runs along the runway beside its threshold (88 labels on 66 charts, and runways 34 and 35 at Beirut).
- Navaid labels that crossed a runway now sit to the left of, below or above their symbol (16 labels on 10 charts).
- Latitude and longitude labels on the frame: a label that crossed the frame now slides inside it; a label that touched an inset, the north arrow, a bearing or a navaid label is left out, and its tick stays.
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
