# Changelog

What changed in the published charts, version by version. Each version is a GitHub release (tag `AC-V<major>.<minor>.<patch>`). A new version is published every time the charts in `downloads/` change.

- **Minor** version (1.**x**.0): new charts, or a visible change to every chart.
- **Patch** version (1.2.**x**): corrections to some charts.

## Coming next

- Cold War Germany.

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
