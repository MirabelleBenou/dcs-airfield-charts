# Minimum sector altitudes (MSA) of the DCS airfields

One file per DCS theatre, named after the `theatre` value a mission file carries (`Caucasus.json`, `GermanyCW.json`, `Syria.json`), format `dcs-msa-v1`: a header line (`source`, `rule`, `attribution`), then one airfield, heliport or helipad per line, in the order of [`downloads/parkings/`](../parkings/README.md), with the same `airdromeId`, `name`, `x` and `y` (DCS position, metres).

- `variation_deg` — the magnetic variation at the airfield (IGRF-14, 1985), positive East. `msa_ft` — the minimum sector altitude of the airfield: the highest of its four sectors, in feet.
- `sectors` — the four sectors of the 25 NM disc (46,300 m) around the airfield, `from`–`to` in degrees (000-090, 090-180, 180-270, 270-360) by **magnetic bearing to the airfield**: `terrain_max_m` the highest ground of the sector (m), `peak_x` / `peak_y` where it is (DCS position), `mountain` true when it reaches 1,500 m, `msa_ft` the altitude of the sector.
- Rule: the altitude of a sector is its highest ground plus 1,000 ft (2,000 ft when `mountain`), rounded up to the next 100 ft; the airfield's is the highest of the four.
- **Ground only**: buildings, masts and other obstacles are not included. Not for real-world navigation.

**Sources**: the ground elevation read in DCS World on a 100 m grid (`source.relief_grid_sha256` identifies the grid), and the airfields of [pydcs](https://github.com/pydcs/dcs) (LGPL-3.0), as in [`downloads/parkings/`](../parkings/README.md). Terrain: DCS World, Eagle Dynamics SA — terrain elevation read in the game. Non-commercial use only. Regenerate with `python -I render/build_msa.py <Theatre> --grille relief_<key>.npy --parkings downloads/parkings/<key>.json --edition relief-100m-20261120 --out downloads/msa`, on the grid written by `render/build_relief.py <Theatre> --entrees <zips> --out <folder> --grille-seule`.
