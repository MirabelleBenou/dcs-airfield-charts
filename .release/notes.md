The charts get their own website, the reading guide can be read on it, and the Chukurova NDB shows its frequency in kHz.

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

## Version numbers

Each version is a GitHub release, tagged `AC-V<major>.<minor>.<patch>`. A new version is published every time the charts or the files in `downloads/` change.

- **Major** version (**x**.0.0): a redesign of the charts, or a file format that changes in a way that breaks what reads it.
- **Minor** version (1.**x**.0): new charts, a new tool or document, or a visible change to every chart.
- **Patch** version (1.2.**x**): corrections to some charts or files.
