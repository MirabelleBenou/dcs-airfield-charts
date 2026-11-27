# Parking stands of the DCS theatres

One file per DCS theatre, named after the `theatre` value a mission file carries (`Caucasus.json`, `GermanyCW.json`, `Syria.json`…), gives every parking stand of every airfield and heliport of the theatre (FARPs placed in a mission are not included), with what a mission file needs to place an aircraft on it cold:

- `airdromeId` — the airfield, as written on the first waypoint of a group that starts from a parking stand (`TakeOffParking`);
- `parking` — the stand's index, written as `parking` on the unit; unique within an airfield;
- `parking_id` — the stand's name as the mission editor shows it, written as `parking_id` on the unit; some airfields repeat a name, so use `parking` as the key;
- `x`, `y` — the stand's position in the DCS frame (metres), written as the unit's `x` and `y`;
- `heli`, `plane` — whether the mission editor marks the stand for helicopters, for airplanes, or both;
- `length`, `width`, `height` — the largest aircraft the stand takes, in metres (`height` is missing on some stands).

`aircraft.json` gives the size of every aircraft type (`heli`, `length`, `width`, `height`, in metres), keyed by its DCS type name.

**Which aircraft fits which stand.** pydcs puts a helicopter only on a stand marked `heli`, an airplane only on a stand marked `plane`, and only when the aircraft is smaller than the stand in length, width and height. Whether DCS also accepts a helicopter on a stand marked for airplanes only is not settled here: the files give the editor's marks as they are.

Not given: the heading of an aircraft on its stand. The airfield names are the DCS mission editor's (Syria's helipads are `HC01`, `HI01`, `HS02`…): match airfields by `airdromeId`.

**Hardened shelters.** `shelters/GermanyCW.json` and `shelters/Syria.json` list, for every airfield and heliport charted in Cold War Germany and Syria, the parking stands (`parking`) that the 10-9 chart draws as a hardened shelter (HAS or hangar), read from the F10 map of DCS. Airfields of other theatres are not covered. Regenerate with `render/build_plans.py` (option `--shelters`) from the zips of a release.

**Source**: the DCS mission editor for Caucasus, Cold War Germany and Syria (`GermanyCW.json`, `Syria.json`, `Caucasus.json`) and the aircraft sizes, with the DCS version in each file (`source`); for the other theatres, and the aircraft types not installed, [pydcs](https://github.com/pydcs/dcs) `84f6335` (LGPL-3.0), which reads them from the same editor. The game facts are read in the DCS mission editor by a probe, then imported with `python -I tools/import_gamedata.py airfields --theatre <Theatre> --out render/data/game/<Theatre>.json <responses…>` (and `aircraft` for the aircraft sizes); regenerate with `python -I render/export_parkings.py --out downloads/parkings`, which writes the three theatres read in the game and `aircraft.json` and leaves the other files as they are. These data follow DCS, not the real world.
