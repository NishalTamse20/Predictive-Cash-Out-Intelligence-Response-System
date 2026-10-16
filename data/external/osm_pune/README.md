# Pune-area OpenStreetMap ATM extract

## Source and scope

The original source is a custom rectangular area extract from BBBike's
OpenStreetMap extract service, retrieved on 2026-10-12. The requested bounds
are south `18.40`, west `73.70`, north `18.65`, east `74.00` (latitude and
longitude in WGS 84). This is a Pune urban-area envelope including adjacent
suburbs, not an administrative boundary. OSM coverage and ATM tagging are
incomplete; this dataset is not claimed to contain every ATM in Pune.

Attribution: © OpenStreetMap contributors. The data is licensed under the Open
Database License (ODbL). See <https://www.openstreetmap.org/copyright> and the
acquisition details in `metadata/acquisition.json`.

OSM locations describe mapped places only. They do not reveal withdrawal
history, cash-out timing, or which ATM a person used.

## Files

- `raw/planet_73.70,18.40_74.00,18.65.osm.gz` is the original gzip-compressed
  OSM XML download. It is preserved without decompression or transformation.
- `processed/pune_atms.geojson` contains validated ATM-only Point features in
  WGS 84 / CRS84 longitude-latitude order.
- `processed/pune_atms.csv` contains the same feature properties as a flat
  analysis table.
- `metadata/acquisition.json` records source, date, query area, attribution,
  license, raw file size, and SHA-256.
- `processed/validation_report.json` records element counts, filtering and
  geometry validation results, duplicate counts, missing optional tags, and
  rejected features.

## Processing and validation

Run the reproducible parser from the project root:

```powershell
.\.venv-pcirs-test\Scripts\python.exe -B scripts\process_osm_pune_atms.py
```

It checks the raw file's gzip signature, byte size and SHA-256 against the
acquisition metadata before parsing. It selects only OSM features tagged
`amenity=atm`. OSM nodes use their own coordinates. For a fully resolved way,
a closed ring uses its planar polygon centroid calculated in longitude and
latitude; an open way uses the arithmetic mean of its resolved vertices. A
missing way node reference is reported and the feature is excluded from the
processed point file. ATM relations are reported as unresolved by this parser.
No names, operators, brands, or access values are invented; missing optional
tags are represented as null in GeoJSON and empty fields in CSV. Rejected
records and duplicate/out-of-bounds counts are retained in the validation
report. Existing processed files are not overwritten.

## Current validation result

- Raw OSM elements: 2,215,288 (1,865,456 nodes; 347,618 ways; 2,214 relations).
- `amenity=atm`: 312 nodes; 0 ways; 0 relations.
- Valid processed ATM records: 312; all carry `amenity=atm`.
- Duplicate type/ID records: 0; duplicate coordinates: 0.
- Invalid or missing geometry: 0; unresolved ATM ways: 0; unresolved ATM
  relations: 0; representative coordinates outside the requested bounds: 0.
- Missing optional tags: name 164; operator 30; brand 66; access 312.
- Coordinate reference system: WGS 84 (EPSG:4326; GeoJSON CRS84 order is
  longitude, latitude).

The dashboard exposes this processed dataset through an explicit OSM candidate
source option. The default remains the clearly labelled synthetic fixture, and
an invalid selected OSM source does not silently fall back to fabricated
points. The current complaint flow provides no verified, explicitly sourced
incident-origin coordinate, so OSM candidates are not geographically ranked.
Do not interpret mapped ATM locations as cash-out events or as evidence about
a suspect's activity.
