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
