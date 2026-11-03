# Parking stands of the DCS theatres

One file per DCS theatre, named after the `theatre` value a mission file carries (`Caucasus.json`, `GermanyCW.json`, `Syria.json`…), gives every parking stand of every airfield, heliport and FARP that DCS lists, with what a mission file needs to place an aircraft on it cold:

- `airdromeId` — the airfield, as written on the first waypoint of a group that starts from a parking stand (`TakeOffParking`);
- `parking` — the stand's index, written as `parking` on the unit; unique within an airfield;
- `parking_id` — the stand's name as the mission editor shows it, written as `parking_id` on the unit; some airfields repeat a name, so use `parking` as the key;
- `x`, `y` — the stand's position in the DCS frame (metres), written as the unit's `x` and `y`;
- `heli`, `plane` — whether the mission editor marks the stand for helicopters, for airplanes, or both;
- `length`, `width`, `height` — the largest aircraft the stand takes, in metres (`height` is missing on some stands).

`aircraft.json` gives the size of every aircraft type (`heli`, `length`, `width`, `height`, in metres), keyed by its DCS type name.

**Which aircraft fits which stand.** pydcs puts a helicopter only on a stand marked `heli`, an airplane only on a stand marked `plane`, and only when the aircraft is smaller than the stand in length, width and height. Whether DCS also accepts a helicopter on a stand marked for airplanes only is not settled here: the files give the editor's marks as they are.

Not given: hardened shelters, and the heading of an aircraft on its stand. The airfield names are pydcs's (the helipads of Syria, all named "H" in DCS, are `Helipad_<id>`): match airfields by `airdromeId`.

**Source**: [pydcs](https://github.com/pydcs/dcs) (LGPL-3.0), which reads them from the DCS mission editor; the commit is given in each file (`source`). Regenerate with `python -I render/export_parkings.py --out downloads/parkings` after a pydcs update. These data follow DCS, not the real world.
