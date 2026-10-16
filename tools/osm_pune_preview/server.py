"""Serve only the standalone Pune ATM preview and its validated GeoJSON."""
from __future__ import annotations

import json
import math
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

TOOL_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TOOL_DIR.parents[1]
GEOJSON_PATH = PROJECT_ROOT / "data" / "external" / "osm_pune" / "processed" / "pune_atms.geojson"
SOUTH, WEST, NORTH, EAST = 18.40, 73.70, 18.65, 74.00
OPTIONAL_PROPERTIES = ("name", "operator", "brand", "access")


def load_geojson(path: Path = GEOJSON_PATH) -> dict:
    """Load and validate ATM point records, reporting invalid features by count."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read ATM GeoJSON: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("type") != "FeatureCollection":
        raise ValueError("ATM GeoJSON must be a FeatureCollection")
    source_features = payload.get("features")
    if not isinstance(source_features, list):
        raise ValueError("ATM GeoJSON FeatureCollection must contain a features array")
    valid = []
    invalid_count = 0
    seen_ids: set[tuple[str, str]] = set()
    for feature in source_features:
        if not isinstance(feature, dict) or feature.get("type") != "Feature":
            invalid_count += 1
            continue
        properties = feature.get("properties")
        geometry = feature.get("geometry")
        if not isinstance(properties, dict) or not isinstance(geometry, dict):
            invalid_count += 1
            continue
        coordinates = geometry.get("coordinates")
        if geometry.get("type") != "Point" or not isinstance(coordinates, list) or len(coordinates) < 2:
            invalid_count += 1
            continue
        try:
            lon, lat = float(coordinates[0]), float(coordinates[1])
            prop_lat, prop_lon = float(properties["latitude"]), float(properties["longitude"])
        except (KeyError, TypeError, ValueError):
            invalid_count += 1
            continue
        if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
            invalid_count += 1
            continue
        if not (SOUTH <= lat <= NORTH and WEST <= lon <= EAST):
            invalid_count += 1
            continue
        if not (math.isclose(lat, prop_lat, abs_tol=1e-7) and math.isclose(lon, prop_lon, abs_tol=1e-7)):
            invalid_count += 1
            continue
        if properties.get("amenity") != "atm" or not properties.get("osm_id") or not properties.get("osm_type"):
            invalid_count += 1
            continue
        key = (str(properties["osm_type"]), str(properties["osm_id"]))
        if key in seen_ids:
            invalid_count += 1
            continue
        seen_ids.add(key)
        # Keep optional tags exactly as stored; absent values remain null.
        valid.append(feature)
    return {"type": "FeatureCollection", "attribution": payload.get("attribution", "© OpenStreetMap contributors"),
            "license": payload.get("license", "Open Database License (ODbL)"),
            "features": valid, "source_feature_count": len(source_features),
            "valid_feature_count": len(valid), "invalid_feature_count": invalid_count,
            "optional_properties": list(OPTIONAL_PROPERTIES)}


class PreviewHandler(BaseHTTPRequestHandler):
    routes = {
        "/": (TOOL_DIR / "index.html", "text/html; charset=utf-8"),
        "/index.html": (TOOL_DIR / "index.html", "text/html; charset=utf-8"),
        "/preview.css": (TOOL_DIR / "preview.css", "text/css; charset=utf-8"),
        "/preview.js": (TOOL_DIR / "preview.js", "text/javascript; charset=utf-8"),
    }

    def do_GET(self) -> None:  # noqa: N802 - standard-library handler API
        path = urlsplit(self.path).path
        if path == "/data/pune_atms.geojson":
            try:
                body = json.dumps(load_geojson(), ensure_ascii=False).encode("utf-8")
            except ValueError as exc:
                self.send_error(500, str(exc))
                return
            self._send(body, "application/geo+json; charset=utf-8")
            return
        route = self.routes.get(path)
        if route is None:
            self.send_error(404, "Not found")
            return
        file_path, content_type = route
        try:
            body = file_path.read_bytes()
        except OSError as exc:
            self.send_error(500, f"Preview asset unavailable: {exc}")
            return
        self._send(body, content_type)

    def _send(self, body: bytes, content_type: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        print(f"{self.address_string()} - {format % args}")


def main() -> None:
    address = ("127.0.0.1", 8765)
    server = ThreadingHTTPServer(address, PreviewHandler)
    print(f"PCIRS Pune ATM preview at http://{address[0]}:{address[1]}/")
    print("Press Ctrl+C to stop. This server exposes only the preview assets and validated ATM GeoJSON.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nPreview server stopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
