# ICAO codes and radio beacons of the DCS airfields

One file per DCS theatre, named after the `theatre` value a mission file carries (`Caucasus.json`, `GermanyCW.json`, `Syria.json`…), lists every airfield, heliport and helipad of the DCS mission editor, with the same `airdromeId` and name as [`downloads/parkings/`](../parkings/README.md):

- `icao` — the airfield's ICAO code, or `null`. DCS gives none: these are **today's real-world codes** from [OurAirports](https://ourairports.com/), taken from the nearest airfield it lists within 3 km. Military bases without a code, and Cold War airfields since closed or renamed, have none or today's.
- `beacons` — the radio beacons DCS ties to the airfield, from the game's own `beacons.lua`: `type` (`HOMER`, `AIRPORT_HOMER`, `VOR_DME`, `TACAN`, `ILS_LOCALIZER`, `ILS_GLIDESLOPE`, `PRMG_LOCALIZER`, `RSBN`…), `callsign`, `frequency` in Hz, `channel` when DCS gives one (TACAN, RSBN, PRMG…), `runway` — the runway end the beacon serves, when DCS says so.
- `enroute` — the beacons DCS ties to no airfield, with their position (`lat`, `lon`).

Beacons are given for Caucasus, Cold War Germany and Syria. On the other theatres, `beacons` and `enroute` are `null`: not known here, which is not the same as none.

**Sources**: [pydcs](https://github.com/pydcs/dcs) (LGPL-3.0) for the airfields; [OurAirports](https://github.com/davidmegginson/ourairports-data) (public domain) for the ICAO codes, at the commit given in each file (`source`); the DCS game files for the beacons. Regenerate with `python -I render/export_navaids.py --ourairports airports.csv --beacons "<DCS>/Mods/terrains" --out downloads/navaids`, with OurAirports' `airports.csv` at that commit. Beacons follow DCS, not the real world.
