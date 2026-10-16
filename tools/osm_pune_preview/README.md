# Standalone Pune ATM map preview

This local preview displays the validated source file at
`data/external/osm_pune/processed/pune_atms.geojson`. It does not read the
synthetic candidate fixture and is not part of the FastAPI app or prediction
pipeline.

## Start

From the Desktop PCIRS project root in PowerShell, run:

```powershell
.\.venv-pcirs-test\Scripts\python.exe tools\osm_pune_preview\server.py
```

Open <http://127.0.0.1:8765/>. Stop the local server with **Ctrl+C** in that
PowerShell window. The server binds to loopback and only serves the preview's
HTML/CSS/JavaScript assets and the validated ATM GeoJSON endpoint; it does not
serve the rest of the project tree.

## Network use

The preview loads Leaflet from the jsDelivr CDN and uses the standard
OpenStreetMap raster tile endpoint for the base map. Internet access is needed
for the library and base tiles. The 312 ATM features are fetched from the local
preview server, not from OSM at run time. No tiles are bundled, downloaded in
bulk, or prefetched. Visible attribution is provided on the map. Use of the
standard tile endpoint follows the [OpenStreetMap Tile Usage Policy](https://operations.osmfoundation.org/policies/tiles/);
tile availability is best effort.

## Preview behavior

- The map fits the actual loaded feature extent on startup. Pan and zoom use
  Leaflet controls and normal map gestures.
- The name search matches available OSM `name` values only. Missing optional
  tags are shown as “Not tagged in OSM” in marker popups.
- The side panel reports the validated source record count, records with an
  actual nonblank OSM name, current search matches (shown as a dash when no
  search is active), and filtered markers inside the current map view.
- Select a marker to inspect its OSM ID, coordinates, and available metadata.
  External OSM values are inserted as text.
- Markers come only from the processed GeoJSON. No candidate scores,
  predictions, cash-out labels, or synthetic points are displayed.
- The visible note and attribution distinguish mapped ATMs from withdrawal
  records or confirmed cash-out locations. Coverage is the selected Pune-area
  bounding box and adjacent suburbs, not the full Pune administrative area.

## Troubleshooting

- If the dataset fails to load, confirm that `processed/pune_atms.geojson` is
  present, then restart the server from the project root and reload the page.
- If the map library fails to load or the base map is blank, check internet
  access and browser network restrictions. The local data endpoint may still
  load, but Leaflet markers require the library.
- If tiles are unavailable, wait briefly and reload. OSM's public tile service
  has no availability guarantee; do not bulk-download or prefetch tiles.
- A port conflict on `8765` should be resolved by stopping the other local
  service before starting this preview.

Attribution: © OpenStreetMap contributors. OSM data is licensed under ODbL.
Mapped ATM points alone do not show withdrawal history, cash-out timing, or
which ATM a person used.
