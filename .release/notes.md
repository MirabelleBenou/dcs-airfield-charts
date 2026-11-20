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
