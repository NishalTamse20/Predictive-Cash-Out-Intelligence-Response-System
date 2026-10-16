(() => {
  function searchView(records, query, isInView = () => true) {
    const term = String(query || "").trim().toLocaleLowerCase();
    const namedRecords = records.filter(({ properties }) =>
      typeof properties.name === "string" && properties.name.trim().length > 0
    );
    const filteredRecords = term
      ? namedRecords.filter(({ properties }) => properties.name.toLocaleLowerCase().includes(term))
      : records.slice();
    const visibleRecords = filteredRecords.filter(isInView);
    return {
      namedCount: namedRecords.length,
      queryActive: Boolean(term),
      matchCount: term ? filteredRecords.length : null,
      filteredRecords,
      visibleRecords,
    };
  }

  if (typeof module === "object" && module.exports) {
    module.exports = { searchView };
    return;
  }

  const loadedCount = document.getElementById("loaded-count");
  const namedCount = document.getElementById("named-count");
  const visibleCount = document.getElementById("visible-count");
  const matchCount = document.getElementById("match-count");
  const matchLabel = matchCount.previousElementSibling;
  matchLabel.textContent = "Search matches";
  const status = document.getElementById("data-status");
  const search = document.getElementById("search");
  const searchHint = document.getElementById("search-hint");
  const clearSearch = document.getElementById("clear-search");
  const selectedEmpty = document.getElementById("selected-empty");
  const selectedDetails = document.getElementById("selected-details");

  function setError(message) {
    status.textContent = message;
    status.classList.add("error");
  }

  function appendDetail(list, labelText, value) {
    const label = document.createElement("dt");
    label.textContent = labelText;
    const detail = document.createElement("dd");
    detail.textContent = value === null || value === undefined || value === ""
      ? "Not tagged in OSM"
      : String(value);
    list.append(label, detail);
  }

  function popupFor(properties) {
    const root = document.createElement("div");
    root.className = "atm-popup";
    const title = document.createElement("h3");
    title.textContent = properties.name || "Name not tagged in OSM";
    root.appendChild(title);
    const list = document.createElement("dl");
    for (const [label, value] of [
      ["OSM ID", properties.osm_id],
      ["Type", properties.osm_type],
      ["Name", properties.name],
      ["Operator", properties.operator],
      ["Brand", properties.brand],
      ["Access", properties.access],
      ["Latitude", properties.latitude],
      ["Longitude", properties.longitude],
    ]) appendDetail(list, label, value);
    root.appendChild(list);
    return root;
  }

  function renderSelected(properties) {
    selectedEmpty.hidden = true;
    selectedDetails.hidden = false;
    selectedDetails.replaceChildren();
    for (const [label, value] of [
      ["OSM ID", properties.osm_id],
      ["Latitude", properties.latitude],
      ["Longitude", properties.longitude],
      ["Name", properties.name],
      ["Operator", properties.operator],
      ["Brand", properties.brand],
      ["Access", properties.access],
    ]) appendDetail(selectedDetails, label, value);
  }

  async function start() {
    if (!window.L) {
      setError("Map library did not load. Check internet access, then refresh this page.");
      loadedCount.textContent = "Unavailable";
      return;
    }
    const map = L.map("map", { scrollWheelZoom: true });
    const markerLayer = L.layerGroup().addTo(map);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>',
    }).addTo(map);
    map.setView([18.5368, 73.8321], 11);
    const records = [];
    let selectedRecord = null;

    function refreshMarkers() {
      const view = searchView(records, search.value, item => map.getBounds().contains(item.marker.getLatLng()));
      markerLayer.clearLayers();
      for (const item of view.filteredRecords) item.marker.addTo(markerLayer);
      matchLabel.textContent = "Search matches";
      matchCount.textContent = view.matchCount === null ? "—" : String(view.matchCount);
      namedCount.textContent = String(view.namedCount);
      visibleCount.textContent = String(view.visibleRecords.length);
      searchHint.textContent = view.queryActive
        ? "Matches are based only on actual OSM name values."
        : "No name filter applied; all validated records are shown. Blank names are not searchable.";
    }

    function selectRecord(item) {
      if (selectedRecord) selectedRecord.marker.setStyle({ radius: 6, color: "#ffffff", weight: 2, fillColor: "#087e83", fillOpacity: 0.92 });
      selectedRecord = item;
      item.marker.setStyle({ radius: 8, color: "#ffffff", weight: 3, fillColor: "#075a65", fillOpacity: 1 });
      item.marker.bringToFront();
      renderSelected(item.properties);
      item.marker.openPopup();
    }

    map.on("moveend", refreshMarkers);
    search.addEventListener("input", refreshMarkers);
    clearSearch.addEventListener("click", () => { search.value = ""; refreshMarkers(); search.focus(); });

    try {
      const response = await fetch("/data/pune_atms.geojson", { headers: { Accept: "application/geo+json, application/json" } });
      if (!response.ok) throw new Error(`Data endpoint returned HTTP ${response.status}`);
      const collection = await response.json();
      if (collection.type !== "FeatureCollection" || !Array.isArray(collection.features)) throw new Error("Expected a GeoJSON FeatureCollection with a features array.");
      for (const feature of collection.features) {
        const properties = feature.properties || {};
        const [lon, lat] = feature.geometry?.coordinates || [];
        if (feature.geometry?.type !== "Point" || !Number.isFinite(lat) || !Number.isFinite(lon)) continue;
        const marker = L.circleMarker([lat, lon], {
          radius: 6, color: "#ffffff", weight: 2, fillColor: "#087e83", fillOpacity: 0.92,
        }).bindPopup(popupFor(properties));
        const item = { marker, properties };
        marker.on("click", () => selectRecord(item));
        records.push(item);
      }
      const validatedCount = Number(collection.valid_feature_count);
      loadedCount.textContent = String(Number.isFinite(validatedCount) ? validatedCount : records.length);
      if (records.length === 0) {
        setError("No valid ATM Point features were found in the processed dataset.");
        map.setView([18.5368, 73.8321], 11);
        refreshMarkers();
        return;
      }
      const bounds = L.latLngBounds(records.map(item => item.marker.getLatLng()));
      map.fitBounds(bounds, { padding: [32, 32], maxZoom: 13 });
      refreshMarkers();
      const invalid = Number(collection.invalid_feature_count || 0);
      status.textContent = invalid ? `${invalid} invalid source feature(s) excluded by the local server validator.` : "All validated ATM points loaded from the processed GeoJSON.";
      if (invalid) status.classList.add("error");
    } catch (error) {
      loadedCount.textContent = "Unavailable";
      namedCount.textContent = "—";
      visibleCount.textContent = "—";
      matchCount.textContent = "—";
      setError(`Could not load the ATM dataset: ${error.message}`);
    }
  }

  start();
})();
