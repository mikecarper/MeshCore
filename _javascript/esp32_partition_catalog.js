(function (global) {
  "use strict";

  function validateCatalog(catalog) {
    if (!catalog || catalog.schema !== 1 || !Array.isArray(catalog.repositories) ||
        !Array.isArray(catalog.releases) || !Array.isArray(catalog.builds) ||
        !Array.isArray(catalog.unresolved) || !catalog.layouts ||
        typeof catalog.layouts !== "object" || Array.isArray(catalog.layouts)) {
      throw new Error("Unsupported partition catalog format");
    }
    return catalog;
  }

  function snapshot(catalog) {
    return {
      schema: catalog.schema,
      generatedAt: catalog.generatedAt,
      repositories: catalog.repositories,
      counts: {
        releases: catalog.releases.length,
        builds: catalog.builds.length,
        layouts: Object.keys(catalog.layouts).length,
        unresolved: catalog.unresolved.length,
      },
    };
  }

  function jsonView(catalog, view) {
    validateCatalog(catalog);
    if (view === "all") return JSON.stringify(catalog, null, 2);
    const result = snapshot(catalog);
    if (view === "layouts") result.layouts = catalog.layouts;
    else if (view === "releases") result.releases = catalog.releases;
    else throw new Error("Unknown catalog view");
    return JSON.stringify(result, null, 2);
  }

  function boardMinimums(catalog) {
    validateCatalog(catalog);
    const boards = new Map();
    catalog.builds.forEach(function (build) {
      const key = build.board.toLowerCase();
      if (!boards.has(key)) {
        boards.set(key, {
          board: build.board, smallestBytes: null,
          hasDualOta: false, hasNonDualOta: false, hasUnknownLayout: false,
        });
      }
      const row = boards.get(key);
      const layout = Object.prototype.hasOwnProperty.call(catalog.layouts, build.layout)
        ? catalog.layouts[build.layout] : null;
      if (!layout || !Array.isArray(layout.partitions)) {
        row.hasUnknownLayout = true;
        return;
      }
      const apps = layout.partitions.filter(function (entry) {
        return Array.isArray(entry) && entry[0] === 0 &&
          Number.isSafeInteger(entry[3]) && entry[3] > 0;
      });
      if (!apps.length) {
        row.hasUnknownLayout = true;
        return;
      }
      const smallest = Math.min.apply(null, apps.map((entry) => entry[3]));
      row.smallestBytes = row.smallestBytes === null
        ? smallest : Math.min(row.smallestBytes, smallest);
      if (layout.dualOta === true) row.hasDualOta = true;
      else if (layout.dualOta === false) row.hasNonDualOta = true;
      else row.hasUnknownLayout = true;
    });
    return Array.from(boards.values()).sort(function (left, right) {
      return left.board.localeCompare(right.board, "en", { sensitivity: "base" });
    });
  }

  function formatSize(bytes) {
    return bytes === null ? "Unknown" : (bytes / 1048576).toLocaleString(
      "en-US", { maximumFractionDigits: 4 }
    ) + " MiB";
  }

  function otaLayouts(row) {
    if (row.hasUnknownLayout) return "Unknown layouts present";
    if (row.hasDualOta && row.hasNonDualOta) return "Mixed (some lack dual OTA)";
    return row.hasDualOta ? "Dual-slot OTA" : "No dual-slot OTA";
  }

  async function initializeViewer() {
    const root = document.getElementById("partition-catalog");
    if (!root || root.dataset.initialized) return;
    root.dataset.initialized = "true";
    const selector = document.getElementById("partition-catalog-view");
    const status = document.getElementById("partition-catalog-status");
    const code = document.getElementById("partition-catalog-json");
    const link = document.getElementById("partition-catalog-open");
    const raw = document.getElementById("partition-catalog-raw");
    const search = document.getElementById("partition-catalog-search");
    const tbody = document.getElementById("partition-catalog-boards");
    const count = document.getElementById("partition-catalog-count");
    try {
      const response = await global.fetch(link.href, { cache: "no-cache" });
      if (!response.ok) throw new Error("HTTP " + response.status);
      const catalog = validateCatalog(await response.json());
      const render = function () {
        // Catalog text is data, never HTML. Only format the large builds array
        // when the reader explicitly requests the complete catalog.
        code.textContent = jsonView(catalog, selector.value);
      };
      raw.addEventListener("toggle", function () { if (raw.open) render(); });
      if (raw.open) render();
      selector.disabled = false;
      selector.addEventListener("change", render);
      const rows = boardMinimums(catalog).map(function (board) {
        const tr = document.createElement("tr");
        const name = document.createElement("th");
        name.scope = "row";
        name.textContent = board.board;
        const size = document.createElement("td");
        size.textContent = formatSize(board.smallestBytes);
        if (board.smallestBytes !== null) {
          const bytes = document.createElement("small");
          bytes.textContent = board.smallestBytes.toLocaleString("en-US") + " bytes";
          size.appendChild(bytes);
        }
        const layout = document.createElement("td");
        layout.textContent = otaLayouts(board);
        tr.appendChild(name);
        tr.appendChild(size);
        tr.appendChild(layout);
        tbody.appendChild(tr);
        return { name: board.board.toLowerCase(), element: tr };
      });
      const filter = function () {
        const query = search.value.trim().toLowerCase();
        let visible = 0;
        rows.forEach(function (row) {
          row.element.hidden = !row.name.includes(query);
          if (!row.element.hidden) visible++;
        });
        count.textContent = "Showing " + visible + " of " + rows.length + " boards.";
      };
      filter();
      search.disabled = false;
      search.addEventListener("input", filter);
      document.getElementById("partition-catalog-table").hidden = false;
      const counts = snapshot(catalog).counts;
      status.textContent = "Snapshot " + catalog.generatedAt + ": " +
        counts.builds.toLocaleString("en-US") + " builds, " +
        counts.releases.toLocaleString("en-US") + " releases, " +
        counts.layouts + " layouts, " + counts.unresolved + " unresolved assets.";
    } catch (error) {
      status.textContent = "Could not load the catalog: " + error.message +
        ". Use Open JSON or Download JSON to access the file directly.";
      code.textContent = "Catalog preview unavailable.";
    }
  }

  const api = Object.freeze({
    validateCatalog, jsonView, boardMinimums, formatSize, otaLayouts, initializeViewer,
  });
  if (typeof module === "object" && module.exports) module.exports = api;
  if (typeof document !== "undefined") {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", initializeViewer, { once: true });
    } else {
      initializeViewer();
    }
  }
})(typeof globalThis !== "undefined" ? globalThis : this);
