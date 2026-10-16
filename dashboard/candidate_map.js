/* Pure mapping of analysis-response candidates to safe map marker records. */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.PCIRSCandidateMap = api;
})(typeof globalThis === "object" ? globalThis : this, function () {
  "use strict";

  function sourceLabel(mode) {
    if (mode === "osm") return "OpenStreetMap mapped ATM candidates";
    if (mode === "synthetic_fixture") return "Synthetic demo candidates";
    return "Candidate locations";
  }

  function numericCoordinate(value, minimum, maximum) {
    if (typeof value !== "number" || !Number.isFinite(value) || value < minimum || value > maximum) return null;
    return value;
  }

  function formatCandidateNumber(value, digits, unavailable = "Unavailable") {
    if (typeof value !== "number" || !Number.isFinite(value)) return unavailable;
    return value.toFixed(digits);
  }

  function mapCandidateRecords(candidates, mode) {
    const label = sourceLabel(mode);
    if (!Array.isArray(candidates)) return [];
    return candidates.flatMap(candidate => {
      if (!candidate || typeof candidate !== "object") return [];
      const latitude = numericCoordinate(candidate.latitude, -90, 90);
      const longitude = numericCoordinate(candidate.longitude, -180, 180);
      if (latitude === null || longitude === null) return [];
      return [{
        candidate,
        latitude,
        longitude,
        candidateId: candidate.candidate_id == null ? "" : String(candidate.candidate_id),
        sourceLabel: label,
      }];
    });
  }

  return { sourceLabel, mapCandidateRecords, formatCandidateNumber };
});
