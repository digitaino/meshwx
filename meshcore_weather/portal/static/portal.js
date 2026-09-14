// Meshcore Weather Portal — SPA controller + MapLibre helpers.
// All map data served from /static/geo/ — fully offline.

// ---------------------------------------------------------------------------
// Map helpers (reused by Weather Map section + System preview map)
// ---------------------------------------------------------------------------

function buildWeatherMapStyle() {
  return {
    version: 8,
    name: "Meshcore Weather",
    sources: {
      countries: { type: "geojson", data: "/static/geo/countries.geojson" },
      states: { type: "geojson", data: "/static/geo/states.geojson" },
      cities: { type: "geojson", data: "/static/geo/cities.geojson" },
    },
    layers: [
      { id: "background", type: "background", paint: { "background-color": "#1b2636" } },
      { id: "countries-fill", type: "fill", source: "countries", paint: { "fill-color": "#2a3546", "fill-opacity": 1 } },
      {
        id: "states-fill", type: "fill", source: "states",
        filter: ["==", ["get", "admin"], "United States of America"],
        paint: { "fill-color": "#334259", "fill-opacity": 1 },
      },
      {
        id: "states-line", type: "line", source: "states",
        paint: { "line-color": "#5a6a80", "line-width": ["interpolate", ["linear"], ["zoom"], 3, 0.3, 6, 0.7, 10, 1.2] },
      },
      {
        id: "countries-line", type: "line", source: "countries",
        paint: { "line-color": "#8fa3bd", "line-width": ["interpolate", ["linear"], ["zoom"], 3, 0.5, 6, 1.0, 10, 1.6] },
      },
      {
        id: "city-dots", type: "circle", source: "cities",
        filter: [">", ["get", "pop"], 100000],
        paint: {
          "circle-radius": ["interpolate", ["linear"], ["zoom"], 3, 1.5, 6, 3, 10, 5],
          "circle-color": "#f5f5f7", "circle-opacity": 0.7,
          "circle-stroke-color": "#0a1220", "circle-stroke-width": 0.5,
        },
      },
    ],
  };
}

const WARNING_COLORS = {
  1: "#e11d48", 2: "#f59e0b", 3: "#06b6d4", 4: "#3b82f6",
  5: "#a855f7", 6: "#f97316", 7: "#dc2626", 8: "#0891b2",
  9: "#fbbf24", 15: "#9ca3af",
};

const WARNING_TYPE_NAMES = {
  1: "Tornado", 2: "Severe T-Storm", 3: "Flash Flood", 4: "Flood",
  5: "Winter Storm", 6: "High Wind", 7: "Fire", 8: "Marine",
  9: "Special", 15: "Other",
};

function createWeatherMap(elementId, options) {
  options = options || {};
  var map = new maplibregl.Map({
    container: elementId,
    style: buildWeatherMapStyle(),
    center: options.center || [-96, 38],
    zoom: options.zoom || 3.5,
    minZoom: 1,
    maxZoom: 10,
    attributionControl: false,
  });
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
  return map;
}

function addWarningsLayer(map, warnings) {
  var sourceId = "warnings";
  var features = warnings
    .filter(function (w) { return w.vertices && w.vertices.length >= 3; })
    .map(function (w) {
      return {
        type: "Feature",
        properties: {
          type: w.warning_type,
          severity: w.severity,
          color: WARNING_COLORS[w.warning_type] || WARNING_COLORS[15],
          name: WARNING_TYPE_NAMES[w.warning_type] || "Unknown",
          headline: w.headline || "",
          in_coverage: w.in_coverage !== false,
        },
        geometry: {
          type: "Polygon",
          coordinates: [w.vertices.map(function (v) { return [v[1], v[0]]; })],
        },
      };
    });

  var data = { type: "FeatureCollection", features: features };

  if (map.getSource(sourceId)) {
    map.getSource(sourceId).setData(data);
  } else {
    map.addSource(sourceId, { type: "geojson", data: data });
    map.addLayer({
      id: "warnings-fill", type: "fill", source: sourceId,
      paint: {
        "fill-color": ["get", "color"],
        "fill-opacity": ["case", ["==", ["get", "in_coverage"], true], 0.35, 0.12],
      },
    });
    map.addLayer({
      id: "warnings-line", type: "line", source: sourceId,
      paint: {
        "line-color": ["get", "color"],
        "line-width": ["case", ["==", ["get", "in_coverage"], true], 2, 1],
        "line-opacity": ["case", ["==", ["get", "in_coverage"], true], 1, 0.4],
      },
    });
    map.on("click", "warnings-fill", function (e) {
      var f = e.features[0];
      var html =
        '<div style="font-family:var(--font-sans,sans-serif);font-size:13px;">' +
        '<strong style="color:' + f.properties.color + '">' + f.properties.name + '</strong>' +
        '<div style="margin-top:4px;">' + escapeHtml(f.properties.headline) + '</div>' +
        (f.properties.in_coverage ? '' : '<div style="margin-top:6px;color:#9ca3af;font-size:11px;">Outside coverage</div>') +
        '</div>';
      new maplibregl.Popup().setLngLat(e.lngLat).setHTML(html).addTo(map);
    });
    map.on("mouseenter", "warnings-fill", function () { map.getCanvas().style.cursor = "pointer"; });
    map.on("mouseleave", "warnings-fill", function () { map.getCanvas().style.cursor = ""; });
  }
}

function addCoverageLayer(map, bbox) {
  if (!bbox) return;
  var n = bbox[0], s = bbox[1], w = bbox[2], e = bbox[3];
  var sourceId = "coverage";
  var data = {
    type: "FeatureCollection",
    features: [{
      type: "Feature", properties: {},
      geometry: { type: "Polygon", coordinates: [[[w, n], [e, n], [e, s], [w, s], [w, n]]] },
    }],
  };
  if (map.getSource(sourceId)) {
    map.getSource(sourceId).setData(data);
  } else {
    map.addSource(sourceId, { type: "geojson", data: data });
    map.addLayer({
      id: "coverage-line", type: "line", source: sourceId,
      paint: { "line-color": "#22d3ee", "line-width": 2, "line-dasharray": [3, 2], "line-opacity": 0.7 },
    });
  }
}

function escapeHtml(s) {
  return String(s || "").replace(/[&<>"']/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
  });
}

// ---------------------------------------------------------------------------
// Portal SPA controller
// ---------------------------------------------------------------------------

var Portal = {

  // -- Router ---------------------------------------------------------------

  router: {
    currentSection: null,
    _sections: ["overview", "console", "radio", "textbot", "broadcast", "sdr", "map", "system"],

    init: function () {
      var self = this;
      // Wire nav links
      document.querySelectorAll("#main-nav a[data-section]").forEach(function (a) {
        a.addEventListener("click", function (e) {
          e.preventDefault();
          self.navigate(a.dataset.section);
        });
      });
      // Also handle in-page hash links (quick actions)
      document.querySelectorAll('a[href^="#"]').forEach(function (a) {
        if (a.dataset.section) return; // already handled above
        a.addEventListener("click", function (e) {
          var target = a.getAttribute("href").replace("#", "");
          if (self._sections.indexOf(target) !== -1) {
            e.preventDefault();
            self.navigate(target);
          }
        });
      });
      window.addEventListener("hashchange", function () { self._onHashChange(); });
      this._onHashChange();
    },

    navigate: function (section) {
      if (this._sections.indexOf(section) === -1) section = "overview";
      if (section === this.currentSection) return;
      window.location.hash = "#" + section;
    },

    _onHashChange: function () {
      var hash = (window.location.hash || "#overview").replace("#", "");
      if (this._sections.indexOf(hash) === -1) hash = "overview";
      if (hash === this.currentSection) return;

      var prev = this.currentSection;
      this.currentSection = hash;

      // Update nav
      document.querySelectorAll("#main-nav a[data-section]").forEach(function (a) {
        a.classList.toggle("active", a.dataset.section === hash);
      });

      // Update sections
      document.querySelectorAll(".section").forEach(function (sec) {
        sec.classList.toggle("active", sec.id === "section-" + hash);
      });

      // Section lifecycle hooks
      if (prev === "map") Portal.weatherMap.onLeave();
      if (prev === "sdr") Portal.sdr.onLeave();
      if (prev === "console") Portal.console.onLeave();
      if (hash === "console") Portal.console.onEnter();
      if (hash === "map") Portal.weatherMap.onEnter();
      if (hash === "overview") Portal.overview.refresh();
      if (hash === "radio") Portal.radio.onEnter();
      if (hash === "textbot") Portal.textbot.onEnter();
      if (hash === "broadcast") Portal.broadcast.onEnter();
      if (hash === "sdr") Portal.sdr.onEnter();
      if (hash === "system") { Portal.system.onEnter(); Portal.sysinfo.onEnter(); }
    },
  },

  // -- UI utilities ---------------------------------------------------------

  ui: {
    _toastTimer: null,

    showToast: function (msg, ok) {
      if (ok === undefined) ok = true;
      var el = document.getElementById("toast");
      el.textContent = msg;
      el.className = "toast" + (ok ? "" : " err");
      el.style.display = "block";
      clearTimeout(this._toastTimer);
      this._toastTimer = setTimeout(function () { el.style.display = "none"; }, 3500);
    },

    openModal: function (html, wide) {
      var content = document.getElementById("modal-content");
      content.innerHTML = html;
      content.className = "modal-content" + (wide ? " modal-wide" : "");
      document.getElementById("modal-overlay").style.display = "flex";
    },

    closeModal: function () {
      document.getElementById("modal-overlay").style.display = "none";
      document.getElementById("modal-content").innerHTML = "";
    },

    formatAgo: function (seconds) {
      if (seconds == null) return "\u2014";
      if (seconds < 60) return seconds + "s";
      if (seconds < 3600) return Math.floor(seconds / 60) + "m";
      if (seconds < 86400) return Math.floor(seconds / 3600) + "h";
      return Math.floor(seconds / 86400) + "d";
    },
  },

  // -- Overview section -----------------------------------------------------

  overview: {
    _rendered: false,

    render: function (boot) {
      var zoneCount = boot.zone_count || 0;
      var el = document.getElementById("overview-stats");
      el.innerHTML =
        '<div class="stat"><div class="stat-label">Coverage Zones</div>' +
          '<div class="stat-value">' + (zoneCount || "All") + '</div>' +
          '<div class="stat-hint">' + (zoneCount ? "Filtered broadcast" : "No filter set") + '</div></div>' +
        '<div class="stat"><div class="stat-label">EMWIN Products</div>' +
          '<div class="stat-value">' + boot.product_count + '</div>' +
          '<div class="stat-hint">In store (last 12h)</div></div>' +
        '<div class="stat"><div class="stat-label">Data Channel</div>' +
          '<div class="stat-value">' +
            (boot.data_channel != null
              ? '<span class="dot dot-green"></span>#' + boot.data_channel
              : '<span class="dot dot-gray"></span>Off') +
          '</div><div class="stat-hint">MeshWX binary broadcast</div></div>' +
        '<div class="stat"><div class="stat-label">Text Channel</div>' +
          '<div class="stat-value">' +
            (boot.channel_idx != null
              ? '<span class="dot dot-green"></span>#' + boot.channel_idx
              : '<span class="dot dot-gray"></span>Off') +
          '</div><div class="stat-hint">Meshtastic channel</div></div>';

      document.getElementById("overview-coverage-text").textContent =
        boot.coverage_summary || "No coverage filter set. Broadcasting for the entire CONUS.";

      this._rendered = true;
    },

    loadHealth: function () {
      Promise.all([apiJson("/api/sdr").catch(function () { return null; }), apiJson("/api/radio").catch(function () { return null; })]).then(function (res) {
        var sdr = res[0] || {}, radio = res[1] || {};
        var st = (sdr.receiver || {}).stats || {}, feed = sdr.feed || {}, info = radio.info || {};
        document.getElementById("overview-health").innerHTML =
          statCard("Satellite", st.locked ? "locked" : (sdr.receiver && !sdr.receiver._error ? "no lock" : "?"), st.vit_avg != null ? "vit " + st.vit_avg + " · drops " + st.drops : "dashboard unreachable", st.locked ? "" : "text-muted") +
          statCard("EMWIN feed", fmtAgeS(feed.newest_age_s), (feed.products_last_hour || 0) + " products/h") +
          statCard("Radio", radio.connected ? "up" : "down", radio.connected ? (info.name || "") + " · " + info.radio_freq + " MHz" : (radio.serial_port || ""), radio.connected ? "" : "text-muted") +
          statCard("Transmit", radio.tx_enabled ? "ON" : "OFF", radio.reply_mode === "channel" ? "reply mode CHANNEL: every reply floods" : (radio.tx_enabled ? "on air · replies by DM" : "receive-only"), radio.reply_mode === "channel" ? "badge-danger" : "") +
          statCard("Warnings", feed.warnings_last_hour != null ? feed.warnings_last_hour : "–", "warning-class products, last hour");
      });
    },

    loadAudit: function () {
      apiJson("/api/audit/last").then(function (d) {
        var el = document.getElementById("overview-audit");
        if (!d.available) { el.textContent = "No audit result yet (the audit timer writes data/audit.json hourly)."; return; }
        var when = new Date(d.at).toLocaleString();
        var parts = Object.keys(d.by_check).map(function (k) {
          var c = d.by_check[k]; return '<span class="badge ' + (c.fail ? "badge-danger" : "badge-success") + '">' + k + " " + c.pass + "/" + (c.pass + c.fail) + '</span>';
        }).join(" ");
        var html = '<div class="mb-4">' + when + " — <strong>" + d.passed + " passed, " + d.failed + " failed</strong> " + parts + '</div>';
        if (d.failures.length) {
          html += d.failures.map(function (f) { return '<div><span class="badge badge-danger">FAIL</span> ' + escapeHtml(f.check + " " + f.subject) + ': <span class="text-muted">' + escapeHtml(f.detail) + '</span></div>'; }).join("");
        }
        el.innerHTML = html;
      }).catch(function () {});
    },

    refresh: function () {
      this.loadHealth();
      this.loadAudit();
      fetch("/api/status").then(function (r) { return r.json(); }).then(function (data) {
        var grid = document.getElementById("overview-status-grid");
        grid.innerHTML =
          '<div class="stat"><div class="stat-label">Text Channel</div>' +
            '<div class="stat-value"><span class="dot dot-green"></span>#' + (data.radio.channel_idx || "?") + '</div></div>' +
          '<div class="stat"><div class="stat-label">Data Channel</div>' +
            '<div class="stat-value">' +
              (data.radio.data_channel_idx != null
                ? '<span class="dot dot-green"></span>#' + data.radio.data_channel_idx
                : '<span class="dot dot-gray"></span>Off') +
            '</div></div>' +
          '<div class="stat"><div class="stat-label">EMWIN Products</div>' +
            '<div class="stat-value">' + data.store.product_count + '</div></div>' +
          '<div class="stat"><div class="stat-label">Known Contacts</div>' +
            '<div class="stat-value">' + data.contacts.known + '</div></div>';

        document.getElementById("overview-status-time").textContent =
          "Updated " + new Date().toLocaleTimeString();
      }).catch(function (e) {
        document.getElementById("overview-status-time").textContent = "Load failed: " + e.message;
      });

      this.loadActivity();
      this.loadStats();
    },

    _activitySSE: null,
    _activityCount: 0,
    _MAX_ACTIVITY_ROWS: 200,

    _renderActivityRow: function (e) {
      var ts = new Date((e.ts || 0) * 1000);
      var timeStr = ts.toLocaleTimeString();
      var dirBadge = e.direction === "in"
        ? '<span class="badge badge-success">IN</span>'
        : '<span class="badge badge-muted">OUT</span>';
      var typeLabel = {
        v2_request: "Data Request",
        v2_response: "Response",
        v1_refresh: "Refresh",
        broadcast: "Broadcast",
        throttled: "Throttled",
      }[e.event_type] || e.event_type;
      return '<tr>' +
        '<td class="text-small text-muted">' + timeStr + '</td>' +
        '<td>' + dirBadge + '</td>' +
        '<td class="text-small">' + escapeHtml(typeLabel) + '</td>' +
        '<td class="text-small">' + escapeHtml(e.summary) + '</td></tr>';
    },

    loadActivity: function () {
      var self = this;
      // Load the backlog via REST — real-time updates come from the
      // global activityPanel SSE stream which feeds BOTH the panel
      // AND this Overview section's activity table.
      fetch("/api/activity?limit=100").then(function (r) { return r.json(); }).then(function (data) {
        var tbody = document.getElementById("activity-body");
        var events = data.events || [];
        document.getElementById("activity-log-count").textContent =
          events.length + " events (live)";
        if (!events.length) {
          tbody.innerHTML = '<tr><td colspan="4" class="text-muted">No activity yet — waiting for events...</td></tr>';
        } else {
          tbody.innerHTML = events.map(function (e) {
            return self._renderActivityRow(e);
          }).join("");
        }
      }).catch(function () {
        document.getElementById("activity-body").innerHTML =
          '<tr><td colspan="4" class="text-muted">Load failed</td></tr>';
      });
    },

    loadStats: function () {
      fetch("/api/stats").then(function (r) { return r.json(); }).then(function (data) {
        var el = document.getElementById("overview-stats-windows");
        var stats = data.stats || [];
        el.innerHTML = stats.map(function (s) {
          var label = s.window_minutes < 60
            ? s.window_minutes + "m"
            : (s.window_minutes / 60) + "h";
          var kb = s.bytes >= 1024
            ? (s.bytes / 1024).toFixed(1) + " KB"
            : s.bytes + " B";
          return '<div class="stat">' +
            '<div class="stat-label">Last ' + label + '</div>' +
            '<div class="stat-value">' + s.messages + '</div>' +
            '<div class="stat-hint">' + kb + '</div></div>';
        }).join("");
      });
    },
  },

  // -- Broadcast Control section --------------------------------------------

  broadcast: {
    _jobsLoaded: false,
    _metaLoaded: false,
    _refreshTimer: null,
    _meta: null,

    onEnter: function () {
      if (!this._metaLoaded) this.loadMeta();
      if (!this._jobsLoaded) this.loadJobs();
      this._startAutoRefresh();
    },

    switchTab: function (tab) {
      document.querySelectorAll("#broadcast-tabs .sub-tab").forEach(function (btn) {
        btn.classList.toggle("active", btn.dataset.tab === tab);
      });
      document.getElementById("broadcast-tab-jobs").classList.toggle("active", tab === "jobs");
      document.getElementById("broadcast-tab-products").classList.toggle("active", tab === "products");
      if (tab === "products") Portal.products.onEnter();
    },

    _startAutoRefresh: function () {
      this._stopAutoRefresh();
      var self = this;
      this._refreshTimer = setInterval(function () { self.loadJobs(); }, 30000);
    },

    _stopAutoRefresh: function () {
      if (this._refreshTimer) { clearInterval(this._refreshTimer); this._refreshTimer = null; }
    },

    loadMeta: function () {
      var self = this;
      fetch("/api/schedule/meta").then(function (r) { return r.json(); }).then(function (data) {
        self._meta = data;
        self._metaLoaded = true;
      });
    },

    _productLabel: function (key) {
      var meta = this._meta || {};
      var info = (meta.product_info || {})[key];
      return info ? info.label : key;
    },

    _locationLabel: function (key) {
      var meta = this._meta || {};
      var info = (meta.location_info || {})[key];
      return info ? info.label : key;
    },

    loadJobs: function () {
      var self = this;
      fetch("/api/schedule/jobs").then(function (r) {
        if (!r.ok) throw new Error(r.status);
        return r.json();
      }).then(function (data) {
        self._jobsLoaded = true;
        var tbody = document.getElementById("jobs-body");
        if (!data.jobs || data.jobs.length === 0) {
          tbody.innerHTML = '<tr><td colspan="9" class="text-muted">No broadcast jobs configured.</td></tr>';
          return;
        }
        tbody.innerHTML = data.jobs.map(function (j) {
          var prodLabel = self._productLabel(j.product);
          var locLabel = self._locationLabel(j.location_type);
          var locDetail = j.location_id ? ": " + escapeHtml(j.location_id) : "";
          return '<tr>' +
            '<td><strong>' + escapeHtml(j.name) + '</strong><br><code class="text-muted">' + escapeHtml(j.id) + '</code></td>' +
            '<td>' + escapeHtml(prodLabel) + '</td>' +
            '<td>' + escapeHtml(locLabel) + locDetail + '</td>' +
            '<td>' + j.interval_minutes + 'm</td>' +
            '<td>' + (j.last_run_seconds_ago != null ? Portal.ui.formatAgo(j.last_run_seconds_ago) + " ago" : "never") + '</td>' +
            '<td>' + (j.next_run_in_seconds != null ? "in " + Portal.ui.formatAgo(j.next_run_in_seconds) : "next tick") + '</td>' +
            '<td>' + j.last_bytes + ' <span class="text-muted">(' + j.last_msg_count + ' msg)</span></td>' +
            '<td><button class="btn-mini" onclick="Portal.broadcast.toggleJob(\'' + j.id + '\')">' +
              (j.enabled ? "on" : "off") + '</button></td>' +
            '<td class="actions">' +
              '<button class="btn-mini" onclick="Portal.broadcast.runNow(\'' + j.id + '\')">Run now</button> ' +
              '<button class="btn-mini" onclick="Portal.broadcast.editJob(\'' + j.id + '\')">Edit</button> ' +
              '<button class="btn-mini danger" onclick="Portal.broadcast.deleteJob(\'' + j.id + '\')">Delete</button>' +
            '</td></tr>';
        }).join("");
      }).catch(function (e) {
        Portal.ui.showToast("Failed to load jobs: " + e.message, false);
      });
    },

    openJobModal: function (mode, job) {
      var meta = this._meta || {};
      var pInfo = meta.product_info || {};
      var lInfo = meta.location_info || {};
      var isEdit = mode === "edit" && job;

      var editLocId = isEdit ? (job.location_id || "") : "";

      var selectedProduct = isEdit ? job.product : (meta.products || [])[0] || "";
      var productOpts = (meta.products || []).map(function (p) {
        var info = pInfo[p] || {};
        var label = info.label || p;
        return '<option value="' + p + '"' + (p === selectedProduct ? ' selected' : '') +
          '>' + escapeHtml(label) + '</option>';
      }).join("");

      // Location options filtered by selected product
      var validLocs = (pInfo[selectedProduct] || {}).locations || meta.location_types || [];
      var selectedLoc = isEdit ? job.location_type : validLocs[0] || "";
      var locOpts = validLocs.map(function (t) {
        var info = lInfo[t] || {};
        return '<option value="' + t + '"' + (t === selectedLoc ? ' selected' : '') +
          '>' + escapeHtml(info.label || t) + '</option>';
      }).join("");

      var locPlaceholder = (lInfo[selectedLoc] || {}).placeholder || "";
      var showLocId = selectedLoc !== "coverage";

      var html =
        '<h2>' + (isEdit ? "Edit broadcast job" : "New broadcast job") + '</h2>' +
        '<form onsubmit="Portal.broadcast.saveJob(event)">' +
          '<input type="hidden" id="jf-mode" value="' + mode + '">' +
          '<input type="hidden" id="jf-original-id" value="' + (isEdit ? job.id : "") + '">' +

          '<label>ID (slug)' +
            '<input type="text" id="jf-id" required pattern="[a-z0-9_-]+" maxlength="64"' +
            ' value="' + (isEdit ? escapeHtml(job.id) : "") + '"' + (isEdit ? ' readonly' : '') + '>' +
          '</label>' +

          '<label>Display name' +
            '<input type="text" id="jf-name" required maxlength="120"' +
            ' value="' + (isEdit ? escapeHtml(job.name) : "") + '">' +
          '</label>' +

          '<label>Product' +
            '<select id="jf-product" required onchange="Portal.broadcast._onProductChange()">' + productOpts + '</select>' +
            '<span class="form-hint" id="jf-product-desc">' + escapeHtml((pInfo[selectedProduct] || {}).desc || "") + '</span>' +
          '</label>' +

          '<label>Location' +
            '<select id="jf-loctype" required onchange="Portal.broadcast._onLocTypeChange()">' + locOpts + '</select>' +
          '</label>' +

          '<div id="jf-locid-group"' + (showLocId ? '' : ' style="display:none"') + '>' +
            '<label>Location ID' +
              '<input type="text" id="jf-locid" placeholder="' + escapeHtml(locPlaceholder) + '"' +
              ' value="' + escapeHtml(editLocId) + '">' +
            '</label>' +
          '</div>' +

          '<label>Interval (minutes)' +
            '<input type="number" id="jf-interval" required min="1" max="10080"' +
            ' value="' + (isEdit ? job.interval_minutes : 60) + '">' +
          '</label>' +

          '<label class="checkbox"><input type="checkbox" id="jf-enabled"' +
            (isEdit ? (job.enabled ? " checked" : "") : " checked") + '> Enabled</label>' +

          '<div class="flex gap-2 mt-4">' +
            '<button type="submit" class="btn btn-primary">Save</button>' +
            '<button type="button" class="btn" onclick="Portal.ui.closeModal()">Cancel</button>' +
          '</div>' +
        '</form>';

      Portal.ui.openModal(html);
    },

    _onProductChange: function () {
      var meta = this._meta || {};
      var pInfo = meta.product_info || {};
      var lInfo = meta.location_info || {};
      var product = document.getElementById("jf-product").value;
      var info = pInfo[product] || {};

      // Update product description
      document.getElementById("jf-product-desc").textContent = info.desc || "";

      // Rebuild location dropdown with valid options for this product
      var validLocs = info.locations || meta.location_types || [];
      var locSel = document.getElementById("jf-loctype");
      var currentLoc = locSel.value;
      locSel.innerHTML = validLocs.map(function (t) {
        var li = lInfo[t] || {};
        return '<option value="' + t + '">' + escapeHtml(li.label || t) + '</option>';
      }).join("");
      // Keep current selection if still valid
      if (validLocs.indexOf(currentLoc) !== -1) {
        locSel.value = currentLoc;
      }

      this._onLocTypeChange();
    },

    _onLocTypeChange: function () {
      var meta = this._meta || {};
      var lInfo = meta.location_info || {};
      var locType = document.getElementById("jf-loctype").value;
      var info = lInfo[locType] || {};

      // Show/hide location ID field
      var locGroup = document.getElementById("jf-locid-group");
      locGroup.style.display = locType === "coverage" ? "none" : "";

      // Update placeholder
      var locInput = document.getElementById("jf-locid");
      locInput.placeholder = info.placeholder || "";
    },

    saveJob: function (ev) {
      ev.preventDefault();
      var mode = document.getElementById("jf-mode").value;
      var product = document.getElementById("jf-product").value;
      var locId = document.getElementById("jf-locid").value.trim();

      var body = {
        id: document.getElementById("jf-id").value.trim(),
        name: document.getElementById("jf-name").value.trim(),
        product: product,
        location_type: document.getElementById("jf-loctype").value,
        location_id: locId,
        interval_minutes: parseInt(document.getElementById("jf-interval").value, 10),
        enabled: document.getElementById("jf-enabled").checked,
      };
      var origId = document.getElementById("jf-original-id").value;
      var url = mode === "edit"
        ? "/api/schedule/jobs/" + encodeURIComponent(origId)
        : "/api/schedule/jobs";
      var method = mode === "edit" ? "PUT" : "POST";
      var self = this;

      fetch(url, {
        method: method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }).then(function (r) {
        if (!r.ok) return r.json().then(function (d) { throw new Error(d.detail || r.status); });
        return r.json();
      }).then(function () {
        Portal.ui.showToast("Job saved");
        Portal.ui.closeModal();
        self.loadJobs();
      }).catch(function (e) {
        Portal.ui.showToast("Save failed: " + e.message, false);
      });
    },

    toggleJob: function (id) {
      var self = this;
      fetch("/api/schedule/jobs/" + encodeURIComponent(id) + "/toggle", { method: "POST" })
        .then(function (r) { if (!r.ok) throw new Error(r.status); self.loadJobs(); })
        .catch(function () { Portal.ui.showToast("Toggle failed", false); });
    },

    runNow: function (id) {
      var self = this;
      fetch("/api/schedule/jobs/" + encodeURIComponent(id) + "/run-now", { method: "POST" })
        .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
        .then(function (data) {
          Portal.ui.showToast("Job ran: " + data.messages_sent + " message(s) sent");
          setTimeout(function () { self.loadJobs(); }, 500);
        })
        .catch(function () { Portal.ui.showToast("Run failed", false); });
    },

    editJob: function (id) {
      var self = this;
      fetch("/api/schedule/jobs").then(function (r) { return r.json(); }).then(function (data) {
        var job = data.jobs.find(function (j) { return j.id === id; });
        if (!job) { Portal.ui.showToast("Job not found", false); return; }
        self.openJobModal("edit", job);
      });
    },

    deleteJob: function (id) {
      if (!confirm("Delete job " + id + "?")) return;
      var self = this;
      fetch("/api/schedule/jobs/" + encodeURIComponent(id), { method: "DELETE" })
        .then(function (r) { if (!r.ok) throw new Error(r.status); Portal.ui.showToast("Job deleted"); self.loadJobs(); })
        .catch(function () { Portal.ui.showToast("Delete failed", false); });
    },
  },

  // -- Products sub-tab -----------------------------------------------------

  products: {
    _filtersLoaded: false,
    _loadTimer: null,

    onEnter: function () {
      if (!this._filtersLoaded) this.loadFilters();
      this.load();
    },

    loadFilters: function () {
      var self = this;
      fetch("/api/products/filters").then(function (r) { return r.json(); }).then(function (data) {
        self._populateSelect("filter-type", data.types);
        self._populateSelect("filter-office", data.offices);
        self._populateSelect("filter-state", data.states);
        self._filtersLoaded = true;
      });
    },

    _populateSelect: function (id, items) {
      var sel = document.getElementById(id);
      var current = sel.value;
      // Keep the "All" option, replace the rest
      sel.innerHTML = '<option value="">' + sel.options[0].textContent + '</option>';
      items.forEach(function (item) {
        var opt = document.createElement("option");
        opt.value = item;
        opt.textContent = item;
        sel.appendChild(opt);
      });
      sel.value = current;
    },

    debouncedLoad: function () {
      clearTimeout(this._loadTimer);
      var self = this;
      this._loadTimer = setTimeout(function () { self.load(); }, 300);
    },

    load: function () {
      var params = new URLSearchParams({
        type: document.getElementById("filter-type").value,
        office: document.getElementById("filter-office").value,
        state: document.getElementById("filter-state").value,
        q: document.getElementById("filter-q").value,
      });
      var tbody = document.getElementById("products-tbody");
      fetch("/api/products?" + params).then(function (r) { return r.json(); }).then(function (data) {
        if (!data.products.length) {
          tbody.innerHTML = '<tr><td colspan="5"><div class="empty-state">No products match the filters.</div></td></tr>';
          document.getElementById("products-summary").textContent = "";
          return;
        }
        tbody.innerHTML = data.products.map(function (p) {
          var ts = new Date(p.timestamp);
          var tsStr = ts.toISOString().slice(0, 16).replace("T", " ");
          return '<tr onclick="Portal.products.openProduct(\'' + escapeHtml(p.filename) + '\')">' +
            '<td class="text-mono"><strong>' + escapeHtml(p.product_type) + '</strong></td>' +
            '<td class="text-mono">' + escapeHtml(p.office || "") + '</td>' +
            '<td class="text-mono">' + escapeHtml(p.state || "") + '</td>' +
            '<td class="text-small text-muted">' + tsStr + ' UTC</td>' +
            '<td class="text-small">' + escapeHtml(p.preview || "") + '</td></tr>';
        }).join("");
        document.getElementById("products-summary").textContent =
          "Showing " + data.products.length + " of possibly more (limit 100). Apply filters to narrow.";
      }).catch(function (e) {
        tbody.innerHTML = '<tr><td colspan="5">Failed: ' + escapeHtml(e.message) + '</td></tr>';
      });
    },

    openProduct: function (filename) {
      fetch("/api/products/" + encodeURIComponent(filename))
        .then(function (r) { return r.json(); })
        .then(function (data) {
          var html =
            '<div class="card-header"><div class="card-title">' +
              escapeHtml(data.emwin_id) + ' \u2014 ' + escapeHtml(data.product_type) +
            '</div><button class="btn" onclick="Portal.ui.closeModal()">Close</button></div>' +
            '<pre class="text-mono" style="white-space:pre-wrap;background:var(--color-bg);padding:var(--space-4);border-radius:var(--radius-sm);max-height:60vh;overflow:auto;">' +
              escapeHtml(data.raw_text) + '</pre>';
          Portal.ui.openModal(html, true);
        })
        .catch(function (e) { Portal.ui.showToast("Failed: " + e.message, false); });
    },
  },

  // -- Weather Map section --------------------------------------------------

  weatherMap: {
    _map: null,
    _mapReady: false,
    _refreshTimer: null,
    _firstLoad: true,

    init: function () {
      // Lazy — MapLibre throws if container has 0 dimensions (hidden section).
      // Actual creation deferred to first onEnter().
    },

    _ensureMap: function () {
      if (this._map) return;
      this._map = createWeatherMap("map");
      var self = this;
      this._map.on("load", function () {
        self._mapReady = true;
        var boot = window.__BOOT__;
        if (boot._coverageBbox) addCoverageLayer(self._map, boot._coverageBbox);
        self.loadAndRender();
      });
    },

    onEnter: function () {
      this._ensureMap();
      if (this._map) {
        requestAnimationFrame(function () {
          Portal.weatherMap._map.resize();
          // Load data after resize if map was already ready
          if (Portal.weatherMap._mapReady) Portal.weatherMap.loadAndRender();
        });
      }
      this._startAutoRefresh();
    },

    onLeave: function () {
      this._stopAutoRefresh();
    },

    _startAutoRefresh: function () {
      this._stopAutoRefresh();
      var self = this;
      this._refreshTimer = setInterval(function () { self.loadAndRender(); }, 60000);
    },

    _stopAutoRefresh: function () {
      if (this._refreshTimer) { clearInterval(this._refreshTimer); this._refreshTimer = null; }
    },

    loadAndRender: function () {
      if (!this._mapReady) return;
      var self = this;
      fetch("/api/warnings").then(function (r) { return r.json(); }).then(function (data) {
        addWarningsLayer(self._map, data.warnings);

        // Build legend
        var typesPresent = [];
        var seen = {};
        data.warnings.forEach(function (w) {
          if (!seen[w.warning_type]) { seen[w.warning_type] = true; typesPresent.push(w.warning_type); }
        });
        typesPresent.sort(function (a, b) { return a - b; });

        var legendHtml = '<div class="legend-title">Active Warnings</div>';
        if (typesPresent.length === 0) {
          legendHtml = '<div class="legend-title">No Active Warnings</div>';
        } else {
          typesPresent.forEach(function (t) {
            legendHtml += '<div class="legend-item">' +
              '<span class="legend-swatch" style="background:' + (WARNING_COLORS[t] || "#9ca3af") + '"></span>' +
              (WARNING_TYPE_NAMES[t] || "Unknown") + '</div>';
          });
        }
        document.getElementById("map-legend").innerHTML = legendHtml;

        var inCov = data.warnings.filter(function (w) { return w.in_coverage; }).length;
        document.getElementById("map-status").innerHTML =
          data.count + " active &middot; <strong>" + inCov + "</strong> in coverage";

        // On first load, fit to coverage bbox if available
        if (self._firstLoad) {
          self._firstLoad = false;
          var boot = window.__BOOT__;
          if (boot._coverageBbox) {
            var b = boot._coverageBbox;
            self._map.fitBounds([[b[2], b[1]], [b[3], b[0]]], { padding: 40, maxZoom: 6, duration: 0 });
          }
        }
      }).catch(function (e) {
        document.getElementById("map-status").textContent = "Load failed";
        console.error(e);
      });
    },
  },

  // -- System section -------------------------------------------------------

  system: {
    _previewMap: null,
    _previewReady: false,
    _initialized: false,

    onEnter: function () {
      if (!this._initialized) {
        this._initialized = true;
        this.render(window.__BOOT__);
        this.initPreviewMap();
      }
    },

    render: function (boot) {
      var src = boot.coverage_sources || {};
      this._renderTags("sys-cities", src.cities || []);
      this._renderTags("sys-states", src.states || []);
      this._renderTags("sys-wfos", src.wfos || []);
      document.getElementById("sys-coverage-summary").textContent =
        boot.coverage_summary || "No coverage filter set \u2014 broadcasting for the entire CONUS.";
    },

    _renderTags: function (elId, items) {
      var el = document.getElementById(elId);
      if (!items.length) {
        el.innerHTML = '<span class="text-muted text-small" style="padding:4px 8px;">None</span>';
        return;
      }
      el.innerHTML = items.map(function (item) {
        return '<span class="tag">' + escapeHtml(item) + '</span>';
      }).join("");
    },

    initPreviewMap: function () {
      this._previewMap = createWeatherMap("sys-preview-map", { zoom: 3, center: [-96, 38] });
      var self = this;
      this._previewMap.on("load", function () {
        self._previewReady = true;
        self.loadCoveragePreview();
      });
    },

    loadCoveragePreview: function () {
      if (!this._previewReady) return;
      var boot = window.__BOOT__;
      var src = boot.coverage_sources || {};
      var params = new URLSearchParams({
        cities: (src.cities || []).join(","),
        states: (src.states || []).join(","),
        wfos: (src.wfos || []).join(","),
      });
      var self = this;
      fetch("/api/coverage/preview?" + params).then(function (r) { return r.json(); }).then(function (data) {
        document.getElementById("sys-preview-summary").textContent = data.summary;
        if (data.bbox) {
          addCoverageLayer(self._previewMap, data.bbox);
          var b = data.bbox;
          self._previewMap.fitBounds([[b[2], b[1]], [b[3], b[0]]], { padding: 40, maxZoom: 6, duration: 400 });
          // Store for weather map too
          window.__BOOT__._coverageBbox = data.bbox;
        }
      }).catch(function () {
        document.getElementById("sys-preview-summary").textContent = "Preview failed";
      });
    },

    loadChannels: function () {
      // Live truth from the radio API: configured names + the slot each role sits on.
      apiJson("/api/radio").then(function (d) {
        var cfg = d.configured_channels || {};
        var set = function (id, v) { var el = document.getElementById(id); if (document.activeElement !== el) el.value = v || ""; };
        set("sys-ch-text", cfg.text); set("sys-ch-data", cfg.data); set("sys-ch-discover", cfg.discover);
        var slots = (d.info && d.info.channels) || {};
        var parts = ["text", "data", "discover"].map(function (r) {
          return r + ": " + (slots[r] != null ? "slot " + slots[r] : (cfg[r] ? "not on node" : "off"));
        });
        document.getElementById("sys-ch-status").textContent = d.connected ? "On the node — " + parts.join(", ") : "Radio not connected; names apply when it connects";
      }).catch(function () {});
    },

    saveChannels: function (btn) {
      var statusEl = document.getElementById("sys-ch-status");
      statusEl.textContent = "Saving\u2026";
      btn.disabled = true;
      var body = {
        text_channel: document.getElementById("sys-ch-text").value.trim(),
        data_channel: document.getElementById("sys-ch-data").value.trim(),
        discover_channel: document.getElementById("sys-ch-discover").value.trim(),
      };
      fetch("/api/settings/channels", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }).then(function (r) {
        if (!r.ok) return r.json().then(function (d) { throw new Error(d.detail || r.status); });
        return r.json();
      }).then(function (d) {
        statusEl.innerHTML = '<span style="color:var(--color-success);">' + escapeHtml(d.note || "Saved") + '</span>';
        Portal.ui.showToast(d.note || "Saved", true);
        Portal.system.loadChannels();
      }).catch(function (e) {
        statusEl.innerHTML = '<span style="color:var(--color-danger);">' + escapeHtml(e.message) + '</span>';
      }).finally(function () { btn.disabled = false; });
    },
  },

  // -- Shared actions -------------------------------------------------------

  actions: {
    broadcast: function (btn) {
      btn.disabled = true;
      var resultEls = [
        document.getElementById("overview-action-result"),
        document.getElementById("sys-action-result"),
      ].filter(Boolean);

      resultEls.forEach(function (el) { el.textContent = "Running\u2026"; });

      fetch("/api/actions/broadcast", { method: "POST" }).then(function (r) {
        if (r.ok) {
          resultEls.forEach(function (el) {
            el.innerHTML = '<span style="color:var(--color-success);">Done</span>';
          });
          Portal.ui.showToast("Broadcast triggered");
        } else {
          return r.json().then(function (d) { throw new Error(d.detail || r.statusText); });
        }
      }).catch(function (e) {
        resultEls.forEach(function (el) {
          el.innerHTML = '<span style="color:var(--color-danger);">' + escapeHtml(e.message) + '</span>';
        });
      }).finally(function () { btn.disabled = false; });
    },

    v2Request: function (btn) {
      btn.disabled = true;
      var resultEl = document.getElementById("sys-action-result");
      resultEl.textContent = "Sending\u2026";

      fetch("/api/actions/v2-request", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          data_type: document.getElementById("v2-data-type").value,
          location: document.getElementById("v2-location").value.trim(),
        }),
      }).then(function (r) {
        if (!r.ok) return r.json().then(function (d) { throw new Error(d.detail || r.status); });
        return r.json();
      }).then(function (data) {
        resultEl.innerHTML = '<span style="color:var(--color-success);">Sent ' +
          escapeHtml(data.data_type) + ' for ' + escapeHtml(JSON.stringify(data.location)) + '</span>';
      }).catch(function (e) {
        resultEl.innerHTML = '<span style="color:var(--color-danger);">' + escapeHtml(e.message) + '</span>';
      }).finally(function () { btn.disabled = false; });
    },
  },

  // -- Init -----------------------------------------------------------------

  // -- Persistent Activity Panel (visible on all pages) ----------------------

  activityPanel: {
    _sse: null,
    _count: 0,
    _MAX_ROWS: 150,

    init: function () {
      var self = this;
      // Load backlog then start SSE
      fetch("/api/activity?limit=50").then(function (r) { return r.json(); }).then(function (data) {
        var events = data.events || [];
        var tbody = document.getElementById("panel-activity-body");
        self._count = events.length;
        if (!events.length) {
          tbody.innerHTML = '<tr><td colspan="4" class="text-muted">Waiting for events...</td></tr>';
        } else {
          tbody.innerHTML = events.map(function (e) { return self._row(e); }).join("");
        }
        self._updateCount();
        self._startSSE();
      }).catch(function () {});
    },

    toggle: function () {
      var panel = document.getElementById("activity-panel");
      if (panel.classList.contains("expanded")) {
        panel.classList.remove("expanded");
        panel.classList.add("collapsed");
      } else {
        panel.classList.remove("collapsed");
        panel.classList.add("expanded");
      }
    },

    _row: function (e) {
      var ts = new Date((e.ts || 0) * 1000);
      var time = ts.toLocaleTimeString();
      var dir = e.direction === "in"
        ? '<span class="badge badge-success">IN</span>'
        : '<span class="badge badge-muted">OUT</span>';
      var labels = {
        v2_request: "Request", v2_response: "Response", v1_refresh: "Refresh",
        broadcast: "Broadcast", throttled: "Throttled", send_fail: "Send Fail",
      };
      var type = labels[e.event_type] || e.event_type;
      return '<tr><td class="text-muted">' + time + '</td><td>' + dir +
        '</td><td>' + escapeHtml(type) + '</td><td>' + escapeHtml(e.summary) + '</td></tr>';
    },

    _updateCount: function () {
      var el = document.getElementById("panel-event-count");
      if (el) el.textContent = "(" + this._count + " events)";
    },

    _startSSE: function () {
      if (this._sse) { this._sse.close(); this._sse = null; }
      var self = this;
      var es = new EventSource("/api/activity/stream");
      this._sse = es;

      es.onmessage = function (msg) {
        try {
          var e = JSON.parse(msg.data);
          var tbody = document.getElementById("panel-activity-body");
          if (!tbody) return;
          // Remove placeholder
          var ph = tbody.querySelector("td[colspan]");
          if (ph) tbody.innerHTML = "";
          // Prepend
          var tmp = document.createElement("div");
          tmp.innerHTML = '<table><tbody>' + self._row(e) + '</tbody></table>';
          var tr = tmp.querySelector("tr");
          if (tr) {
            tr.classList.add("row-new");
            tbody.insertBefore(tr, tbody.firstChild);
          }
          self._count++;
          self._updateCount();
          // Trim
          while (tbody.children.length > self._MAX_ROWS) tbody.removeChild(tbody.lastChild);

          // Also update the Overview page's activity log if it exists
          var overviewBody = document.getElementById("activity-body");
          if (overviewBody && overviewBody !== tbody) {
            var tmp2 = document.createElement("div");
            tmp2.innerHTML = '<table><tbody>' + Portal.overview._renderActivityRow(e) + '</tbody></table>';
            var tr2 = tmp2.querySelector("tr");
            if (tr2) {
              tr2.style.backgroundColor = "rgba(0, 150, 255, 0.15)";
              overviewBody.insertBefore(tr2, overviewBody.firstChild);
              setTimeout(function () { tr2.style.backgroundColor = ""; }, 1500);
            }
            while (overviewBody.children.length > 200) overviewBody.removeChild(overviewBody.lastChild);
            var countEl = document.getElementById("activity-log-count");
            if (countEl) countEl.textContent = overviewBody.children.length + " events (live)";
          }
        } catch (err) {}
      };

      es.onerror = function () {
        var el = document.getElementById("panel-event-count");
        if (el) el.textContent = "(reconnecting...)";
      };
      es.onopen = function () { self._updateCount(); };
    },
  },

  init: function () {
    var boot = window.__BOOT__ || {};

    // Render overview from boot data immediately
    this.overview.render(boot);

    // Start the persistent activity panel SSE stream
    this.activityPanel.init();

    // Map init is lazy — created on first visit to #map section
    // (MapLibre throws if container has 0 dimensions while hidden)

    // Keyboard shortcut: Escape closes modal
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") Portal.ui.closeModal();
    });

    // Start router (reads hash, activates correct section)
    this.router.init();
  },
};

// Boot
document.addEventListener("DOMContentLoaded", function () { Portal.init(); });

// ---------------------------------------------------------------------------
// Admin modules: radio, text bot console, satellite receiver, system info
// ---------------------------------------------------------------------------

function apiJson(url, opts) {
  opts = opts || {};
  if (opts.body && typeof opts.body !== "string") {
    opts.headers = Object.assign({ "Content-Type": "application/json" }, opts.headers || {});
    opts.body = JSON.stringify(opts.body);
  }
  return fetch(url, opts).then(function (r) {
    return r.json().catch(function () { return {}; }).then(function (d) {
      if (!r.ok) throw new Error(d.detail || ("HTTP " + r.status));
      return d;
    });
  });
}

function statCard(label, value, hint, cls) {
  return '<div class="stat"><div class="stat-label">' + escapeHtml(label) + '</div>' +
    '<div class="stat-value ' + (cls || "") + '">' + value + '</div>' +
    '<div class="stat-hint">' + escapeHtml(hint || "") + '</div></div>';
}

function fmtAgeS(s) {
  if (s == null) return "–";
  if (s < 90) return s + "s";
  if (s < 5400) return Math.round(s / 60) + "m";
  if (s < 172800) return Math.round(s / 3600) + "h";
  return Math.round(s / 86400) + "d";
}

Portal.radio = {
  _state: null,
  onEnter: function () { this.load(); },

  load: function () {
    var self = this;
    apiJson("/api/radio").then(function (d) {
      self._state = d;
      self.render(d);
    }).catch(function (e) { Portal.ui.showToast("Radio: " + e.message, false); });
  },

  render: function (d) {
    var info = d.info || {};
    var conn = d.connected;
    document.getElementById("radio-offline-card").style.display = conn ? "none" : "";
    document.getElementById("radio-offline-reason").textContent = conn ? "" : (d.error || "") + " (" + d.serial_port + " @ " + d.serial_baud + ")";
    var stats =
      statCard("Link", conn ? "up" : "down", d.serial_port, conn ? "" : "text-muted") +
      statCard("Node", conn ? escapeHtml(info.name || "?") : "–", info.public_key ? info.public_key.slice(0, 12) + "…" : "") +
      statCard("Frequency", info.radio_freq != null ? info.radio_freq + " MHz" : "–",
        info.radio_bw != null ? "BW " + info.radio_bw + " kHz · SF" + info.radio_sf + " · CR" + info.radio_cr : "") +
      statCard("TX power", info.tx_power != null ? info.tx_power + " dBm" : "–", info.max_tx_power != null ? "max " + info.max_tx_power : "") +
      statCard("Battery", info.battery_mv ? (info.battery_mv / 1000).toFixed(2) + " V" : "–", d.tx_enabled ? "transmit ON" : "receive-only");
    document.getElementById("radio-stats").innerHTML = stats;

    var set = function (id, v) { var el = document.getElementById(id); if (el && document.activeElement !== el) el.value = v == null ? "" : v; };
    set("radio-name", info.name); set("radio-lat", info.adv_lat); set("radio-lon", info.adv_lon);
    set("radio-freq", info.radio_freq); set("radio-bw", info.radio_bw); set("radio-sf", info.radio_sf); set("radio-cr", info.radio_cr);
    set("radio-txpower", info.tx_power);
    document.getElementById("radio-pubkey").textContent = info.public_key ? "public key " + info.public_key : "";
    var badge = document.getElementById("radio-tx-badge");
    badge.textContent = d.tx_enabled ? "ON" : "OFF";
    badge.className = "badge " + (d.tx_enabled ? "badge-danger" : "badge-success");
    document.getElementById("radio-tx-toggle").textContent = d.tx_enabled ? "Disable transmit" : "Enable transmit";

    var sel = document.getElementById("radio-preset");
    sel.innerHTML = Object.keys(d.presets || {}).map(function (k) {
      var p = d.presets[k];
      return '<option value="' + k + '">' + escapeHtml(p.label) + " — " + p.freq_mhz + "/" + p.bw_khz + "/SF" + p.sf + "/CR" + p.cr + '</option>';
    }).join("");

    var tb = document.getElementById("radio-channels");
    if (!conn) { tb.innerHTML = '<tr><td colspan="5" class="text-muted">Radio not connected</td></tr>'; return; }
    var rows = [];
    for (var i = 0; i < 8; i++) {
      var ch = (d.channels || []).filter(function (c) { return c.idx === i; })[0] || { idx: i, name: "", role: null };
      var roleBadge = ch.role ? '<span class="badge badge-success">' + ch.role + '</span>' : "";
      var cfgMatch = "";
      if (!ch.role && ch.name) {
        var cfg = d.configured_channels || {};
        if ([cfg.text, cfg.data, cfg.discover].indexOf(ch.name) !== -1) cfgMatch = '<span class="badge badge-warning">configured, not resolved</span>';
      }
      rows.push('<tr><td>' + i + (i === 0 ? ' <span class="text-muted">(public)</span>' : "") + '</td>' +
        '<td>' + (i === 0 ? escapeHtml(ch.name || "public") :
          '<input class="input" style="max-width:260px" id="radio-ch-' + i + '" value="' + escapeHtml(ch.name || "") + '" placeholder="(empty slot)">') + '</td>' +
        '<td>' + roleBadge + cfgMatch + '</td>' +
        '<td class="text-mono text-small text-muted">' + (ch.secret ? ch.secret.slice(0, 8) + "…" : "") + '</td>' +
        '<td>' + (i === 0 ? "" :
          '<button class="btn btn-mini" onclick="Portal.radio.saveChannel(' + i + ')">Save</button> ' +
          (ch.name && !ch.role ? '<button class="btn btn-mini btn-danger" onclick="Portal.radio.clearChannel(' + i + ')">Clear</button>' : "")) + '</td></tr>');
    }
    tb.innerHTML = rows.join("");
  },

  _act: function (promise, okMsg) {
    var self = this;
    var out = document.getElementById("radio-action-result");
    return promise.then(function (d) {
      Portal.ui.showToast(okMsg || "Done", true);
      if (d && d.note) out.textContent = d.note; else out.textContent = "";
      self.load();
    }).catch(function (e) { Portal.ui.showToast(e.message, false); out.textContent = e.message; });
  },
  saveName: function () { this._act(apiJson("/api/radio/name", { method: "POST", body: { name: document.getElementById("radio-name").value } }), "Name saved"); },
  saveCoords: function () {
    this._act(apiJson("/api/radio/coords", { method: "POST", body: { lat: parseFloat(document.getElementById("radio-lat").value), lon: parseFloat(document.getElementById("radio-lon").value) } }), "Location saved");
  },
  saveParams: function () {
    var body = { freq_mhz: parseFloat(document.getElementById("radio-freq").value), bw_khz: parseFloat(document.getElementById("radio-bw").value),
      sf: parseInt(document.getElementById("radio-sf").value, 10), cr: parseInt(document.getElementById("radio-cr").value, 10) };
    if (!confirm("Change LoRa parameters to " + body.freq_mhz + " MHz / " + body.bw_khz + " kHz / SF" + body.sf + " / CR" + body.cr + "? Every node on the mesh must use the same values.")) return;
    this._act(apiJson("/api/radio/params", { method: "POST", body: body }), "Radio parameters applied");
  },
  applyPreset: function () {
    var k = document.getElementById("radio-preset").value;
    var p = (this._state && this._state.presets || {})[k];
    if (!p) return;
    ["freq", "bw", "sf", "cr"].forEach(function (f) { document.getElementById("radio-" + f).value = p[{ freq: "freq_mhz", bw: "bw_khz", sf: "sf", cr: "cr" }[f]]; });
  },
  saveTxPower: function () { this._act(apiJson("/api/radio/txpower", { method: "POST", body: { dbm: parseInt(document.getElementById("radio-txpower").value, 10) } }), "TX power set"); },
  toggleTx: function () {
    var on = !(this._state && this._state.tx_enabled);
    if (on && !confirm("Enable transmit? The bot will send adverts, DM replies and scheduled broadcasts on air.")) return;
    this._act(apiJson("/api/radio/tx", { method: "POST", body: { enabled: on } }), on ? "Transmit enabled" : "Transmit disabled");
  },
  advert: function () { this._act(apiJson("/api/radio/advert", { method: "POST" }), "Advert requested"); },
  reboot: function () { if (confirm("Reboot the radio node?")) this._act(apiJson("/api/radio/reboot", { method: "POST" }), "Rebooting"); },
  saveChannel: function (i) {
    var name = document.getElementById("radio-ch-" + i).value.trim();
    if (!name) return this.clearChannel(i);
    this._act(apiJson("/api/radio/channel", { method: "POST", body: { idx: i, name: name } }), "Channel " + i + " saved");
  },
  clearChannel: function (i) { if (confirm("Clear channel slot " + i + "?")) this._act(apiJson("/api/radio/channel/" + i, { method: "DELETE" }), "Channel " + i + " cleared"); },
  loadContacts: function () {
    apiJson("/api/radio/contacts").then(function (d) {
      var tb = document.getElementById("radio-contacts");
      if (!d.contacts.length) { tb.innerHTML = '<tr><td colspan="5" class="text-muted">No contacts</td></tr>'; return; }
      var now = Date.now() / 1000;
      tb.innerHTML = d.contacts.map(function (c) {
        return '<tr><td>' + escapeHtml(c.name || "?") + '</td><td>' + ({ 1: "client", 2: "repeater", 3: "room" }[c.type] || c.type || "") + '</td>' +
          '<td>' + (c.last_advert ? fmtAgeS(Math.round(now - c.last_advert)) + " ago" : "–") + '</td>' +
          '<td>' + (c.out_path_len != null && c.out_path_len >= 0 ? c.out_path_len + " hops" : "flood") + '</td>' +
          '<td class="text-mono text-small text-muted">' + (c.public_key || "").slice(0, 12) + '</td></tr>';
      }).join("");
    }).catch(function (e) { Portal.ui.showToast(e.message, false); });
  },
};

Portal.textbot = {
  _loaded: false,
  onEnter: function () {
    if (!this._loaded) {
      this._loaded = true;
      var ex = ["wx round rock tx", "forecast austin", "warn TX", "storm TX", "space", "metar KAUS", "help"];
      document.getElementById("console-examples").innerHTML = ex.map(function (t) {
        return '<button class="btn btn-mini" onclick="Portal.textbot.send(' + JSON.stringify(t).replace(/"/g, "&quot;") + ')">' + escapeHtml(t) + '</button>';
      }).join("");
      apiJson("/api/console/help").then(function (d) { document.getElementById("console-help").textContent = d.help; });
    }
    Portal.system.loadChannels();
    this.loadReplyMode();
  },
  loadReplyMode: function () {
    apiJson("/api/radio").then(function (d) {
      var set = function (id, v) { var el = document.getElementById(id); if (document.activeElement !== el && v != null) el.value = v; };
      set("reply-mode", d.reply_mode); set("reply-max-hops", d.channel_reply_max_hops);
      apiJson("/api/system").then(function (sy) { set("advert-hours", (sy.settings || {}).advert_interval_hours); });
      var peers = d.peer_bots || [];
      document.getElementById("peer-bots").textContent = peers.length ? peers.map(function (p) { return p.name + " (" + p.lat.toFixed(2) + "," + p.lon.toFixed(2) + ")"; }).join(", ") : "none";
    }).catch(function () {});
  },
  saveReplyMode: function () {
    var body = { MCW_REPLY_MODE: document.getElementById("reply-mode").value,
      MCW_CHANNEL_REPLY_MAX_HOPS: document.getElementById("reply-max-hops").value,
      MCW_ADVERT_INTERVAL_HOURS: document.getElementById("advert-hours").value };
    var st = document.getElementById("reply-mode-status");
    apiJson("/api/settings/env", { method: "POST", body: body }).then(function (d) { st.textContent = d.note; Portal.ui.showToast("Reply mode applied", true); })
      .catch(function (e) { st.textContent = e.message; Portal.ui.showToast(e.message, false); });
  },
  send: function (preset) {
    var input = document.getElementById("console-input");
    var text = (preset || input.value).trim();
    if (!text) return;
    if (!preset) input.value = "";
    var log = document.getElementById("console-log");
    var id = "c" + Date.now();
    log.insertAdjacentHTML("beforeend", '<div><span class="text-muted">you&gt;</span> ' + escapeHtml(text) + '</div><div id="' + id + '" class="text-muted">…</div>');
    log.scrollTop = log.scrollHeight;
    apiJson("/api/console", { method: "POST", body: { text: text } }).then(function (d) {
      var el = document.getElementById(id);
      var meta = '<span class="text-muted">[' + escapeHtml(d.command) + (d.location ? " · " + escapeHtml(d.location) : "") + " · " + d.ms + "ms]</span> ";
      var chunks = d.chunks && d.chunks.length ? d.chunks : [d.reply || "(no reply)"];
      el.className = "";
      el.innerHTML = chunks.map(function (c, i) {
        return '<div style="margin:2px 0 8px 0"><span class="badge badge-muted">DM ' + (i + 1) + "/" + chunks.length + " · " + c.length + "ch</span> " + escapeHtml(c) + "</div>";
      }).join("") + meta;
      log.scrollTop = log.scrollHeight;
    }).catch(function (e) { document.getElementById(id).textContent = "error: " + e.message; });
  },
};

Portal.sdr = {
  _timer: null,
  onEnter: function () { this.load(); this.loadHistory(); var s = this; this._timer = setInterval(function () { s.load(); s.loadHistory(); }, 10000); },
  onLeave: function () { if (this._timer) clearInterval(this._timer); this._timer = null; },

  load: function () {
    apiJson("/api/sdr").then(function (d) {
      var r = d.receiver || {}, st = r.stats || {}, feed = d.feed || {};
      var err = r._error;
      document.getElementById("sdr-dashboard-link").href = d.dashboard_url;
      document.getElementById("sdr-stats").innerHTML =
        statCard("Lock", err ? "?" : (st.locked ? "locked" : "no lock"), err ? "dashboard unreachable" : (st.lock_since ? "since " + fmtAgeS(Math.round(Date.now() / 1000 - st.lock_since)) + " ago" : ""), st.locked ? "" : "text-muted") +
        statCard("Viterbi", st.vit_avg != null ? st.vit_avg : "–", "errors/frame, lower is better") +
        statCard("Drops", st.drops != null ? st.drops : "–", "last interval") +
        statCard("Feed", feed.products_last_hour != null ? feed.products_last_hour : "–", "products in the last hour") +
        statCard("Newest file", fmtAgeS(feed.newest_age_s), feed.source === "sdr" ? "from the dish" : "internet feed");
      document.getElementById("sdr-signal-sub").textContent = err ? err :
        ("mode " + (r.mode || "?") + " · gain " + (st.gain != null ? st.gain.toFixed(1) : "?") + " · freq offset " + (st.freq != null ? Math.round(st.freq) + " Hz" : "?"));
      document.getElementById("sdr-mode-point").className = "btn" + (r.mode === "point" ? " btn-primary" : "");
      document.getElementById("sdr-mode-receive").className = "btn" + (r.mode === "receive" ? " btn-primary" : "");
      var tot = st.totals || {};
      document.getElementById("sdr-totals").innerHTML =
        statCard("Packets", tot.packets != null ? tot.packets.toLocaleString() : "–", "since goesrecv start") +
        statCard("Dropped", tot.drops != null ? tot.drops.toLocaleString() : "–", tot.packets ? (tot.drops * 100 / tot.packets).toFixed(2) + "%" : "") +
        statCard("RS corrected", tot.rs_errors != null ? tot.rs_errors.toLocaleString() : "–", "bytes");
      document.getElementById("sdr-feed-sub").textContent = feed.products_total + " products in the store · " + feed.warnings_last_hour + " warning-class in the last hour" + (feed.directory ? " · " + feed.directory : "");
      document.getElementById("sdr-feed-types").innerHTML = (feed.top_types_last_hour || []).map(function (t) {
        return '<tr><td class="text-mono">' + escapeHtml(t[0]) + '</td><td>' + t[1] + '</td></tr>';
      }).join("") || '<tr><td colspan="2" class="text-muted">nothing in the last hour</td></tr>';
      var svc = (r.status || {}).services || {};
      var stt = r.status || {};
      document.getElementById("sdr-services").innerHTML =
        statCard("goesrecv", svc.goesrecv || "?", stt.goesrecv_up_s ? "up " + fmtAgeS(stt.goesrecv_up_s) : "") +
        statCard("goesproc", svc.goesproc || "?", stt.counts ? (stt.counts.emwin_today || 0) + " EMWIN files today" : "") +
        statCard("Pi", stt.temp ? stt.temp.toFixed(0) + "°C" : "–", stt.disk_free_gb ? stt.disk_free_gb + " GB free · load " + stt.load : "");
    }).catch(function (e) { Portal.ui.showToast("Satellite: " + e.message, false); });
  },

  loadHistory: function () {
    apiJson("/api/sdr/history").then(function (d) {
      var h = d.history; if (!Array.isArray(h) || !h.length) return;
      var c = document.getElementById("sdr-chart"); var ctx = c.getContext("2d");
      var W = c.width = c.clientWidth || 600, H = c.height;
      ctx.clearRect(0, 0, W, H);
      var vit = h.map(function (r) { return r[1]; }), drops = h.map(function (r) { return r[3]; });
      var vmax = Math.max(400, Math.max.apply(null, vit)), dmax = Math.max(5, Math.max.apply(null, drops));
      var n = h.length, dx = W / n;
      ctx.fillStyle = "rgba(225,29,72,0.6)";
      drops.forEach(function (v, i) { if (v > 0) { var bh = v / dmax * (H - 20); ctx.fillRect(i * dx, H - bh, Math.max(1, dx), bh); } });
      ctx.strokeStyle = "#06b6d4"; ctx.lineWidth = 1.5; ctx.beginPath();
      vit.forEach(function (v, i) { var y = H - 10 - (v / vmax) * (H - 20); if (i === 0) ctx.moveTo(0, y); else ctx.lineTo(i * dx, y); });
      ctx.stroke();
      ctx.fillStyle = "#9ca3af"; ctx.font = "11px sans-serif";
      ctx.fillText("vit " + vit[vit.length - 1] + " · drops " + drops[drops.length - 1], 6, 12);
    }).catch(function () {});
  },
  setMode: function (mode) {
    var self = this;
    apiJson("/api/sdr/mode", { method: "POST", body: { mode: mode } }).then(function () { Portal.ui.showToast("Receiver in " + mode + " mode", true); self.load(); })
      .catch(function (e) { Portal.ui.showToast(e.message, false); });
  },
};

Portal.sysinfo = {
  onEnter: function () { this.load(); },
  load: function () {
    apiJson("/api/system").then(function (d) {
      var b = d.bot || {}, h = d.host || {}, s = d.settings || {};
      document.getElementById("sysinfo-sub").textContent = h.hostname + (b.git ? " · commit " + b.git : "");
      document.getElementById("sysinfo-stats").innerHTML =
        statCard("Bot up", fmtAgeS(b.uptime_s), b.radio_connected ? "radio connected" : "no radio") +
        statCard("Host up", fmtAgeS(h.uptime_s), h.load ? "load " + h.load.join(" / ") : "") +
        statCard("Memory", h.mem_available_mb != null ? h.mem_available_mb + " MB" : "–", "available of " + h.mem_total_mb) +
        statCard("Disk", h.disk_free_gb != null ? h.disk_free_gb + " GB" : "–", "free · " + h.disk_used_pct + "% used") +
        statCard("CPU temp", h.temp_c != null ? h.temp_c + "°C" : "–", b.products + " products");
      var map = { MCW_SERIAL_PORT: "serial_port", MCW_HOME_CITIES: "home_cities", MCW_HOME_RADIUS_KM: "home_radius_km", MCW_TIMEZONE: "timezone",
        MCW_EMWIN_SOURCE: "emwin_source", MCW_SDR_EMWIN_DIR: "sdr_emwin_dir", MCW_LOG_LEVEL: "log_level" };
      Object.keys(map).forEach(function (k) { var el = document.getElementById("env-" + k); if (el && document.activeElement !== el && s[map[k]] != null) el.value = s[map[k]]; });
    }).catch(function (e) { Portal.ui.showToast(e.message, false); });
  },
  saveEnv: function () {
    var keys = ["MCW_SERIAL_PORT", "MCW_HOME_CITIES", "MCW_HOME_RADIUS_KM", "MCW_TIMEZONE", "MCW_EMWIN_SOURCE", "MCW_SDR_EMWIN_DIR", "MCW_LOG_LEVEL"];
    var body = {}; keys.forEach(function (k) { body[k] = document.getElementById("env-" + k).value; });
    var st = document.getElementById("env-status"); st.textContent = "Saving…";
    apiJson("/api/settings/env", { method: "POST", body: body }).then(function (d) { st.textContent = d.note; Portal.ui.showToast(d.restart_needed && d.restart_needed.length ? "Saved; some settings need a restart" : "Settings applied", true); })
      .catch(function (e) { st.textContent = e.message; });
  },
  restartBot: function () {
    if (!confirm("Restart the bot now? It is back in about 40 seconds; the radio link drops briefly.")) return;
    apiJson("/api/system/restart", { method: "POST" }).then(function (d) { Portal.ui.showToast(d.note, true); })
      .catch(function (e) { Portal.ui.showToast(e.message, false); });
  },
};

// ---------------------------------------------------------------------------
// Console: one live stream for satellite / radio / bot, filterable
// ---------------------------------------------------------------------------

Portal.console = {
  _lines: [], _max: 3000, _cat: "all", _paused: false, _sse: null, _pending: 0, _lastId: 0,

  onEnter: function () {
    var self = this;
    apiJson("/api/logs?n=800").then(function (d) {
      self._lines = d.lines || [];
      self._lastId = self._lines.length ? self._lines[self._lines.length - 1].id : 0;
      self._counts(d.counts);
      self.render(true);
      self._connect();
    }).catch(function (e) { Portal.ui.showToast("Console: " + e.message, false); });
  },
  onLeave: function () { if (this._sse) { this._sse.close(); this._sse = null; } },

  _connect: function () {
    var self = this;
    if (this._sse) this._sse.close();
    var es = new EventSource("/api/logs/stream");
    this._sse = es;
    es.onmessage = function (m) {
      var l; try { l = JSON.parse(m.data); } catch (e) { return; }
      if (!l || l.hello) return;
      if (l.id && l.id <= self._lastId) return;
      self._lastId = l.id || self._lastId;
      self._lines.push(l);
      if (self._lines.length > self._max) self._lines.splice(0, self._lines.length - self._max);
      if (self._paused) { self._pending++; self._status(); return; }
      if (self._show(l)) self._append(l, true);
    };
    es.onerror = function () { self._status("reconnecting…"); };
    es.onopen = function () { self._status(); };
  },

  _counts: function (c) {
    if (!c) return;
    ["satellite", "radio", "bot"].forEach(function (k) {
      var el = document.getElementById("console-n-" + k); if (el) el.textContent = c[k] || 0;
    });
  },
  _status: function (extra) {
    var el = document.getElementById("console-status");
    el.textContent = (this._paused ? "paused" + (this._pending ? " · " + this._pending + " new" : "") : "live") + (extra ? " · " + extra : "");
  },
  setCat: function (cat) {
    this._cat = cat;
    document.querySelectorAll("#console-cats .sub-tab").forEach(function (b) { b.classList.toggle("active", b.dataset.cat === cat); });
    this.render(true);
  },
  togglePause: function () {
    this._paused = !this._paused;
    document.getElementById("console-pause").textContent = this._paused ? "Resume" : "Pause";
    if (!this._paused) { this._pending = 0; this.render(true); }
    this._status();
  },
  clear: function () { this._lines = []; this.render(true); },

  _show: function (l) {
    if (this._cat !== "all" && l.cat !== this._cat) return false;
    var lv = document.getElementById("console-level").value;
    var order = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"];
    if (lv && order.indexOf(l.level) < order.indexOf(lv)) return false;
    var q = document.getElementById("console-q").value.trim().toLowerCase();
    if (q && l.msg.toLowerCase().indexOf(q) === -1) return false;
    return true;
  },
  _fmt: function (l) {
    var d = new Date(l.t * 1000);
    var ts = d.toTimeString().slice(0, 8);
    var lc = l.level === "ERROR" || l.level === "CRITICAL" ? "badge-danger" : l.level === "WARNING" ? "badge-warning" : "badge-muted";
    var cc = { satellite: "#06b6d4", radio: "#a855f7", bot: "#9ca3af" }[l.cat] || "#9ca3af";
    return '<div class="console-line"><span class="text-muted">' + ts + '</span> ' +
      '<span style="color:' + cc + ';display:inline-block;min-width:64px">' + l.cat + '</span>' +
      '<span class="badge ' + lc + '">' + l.level.slice(0, 4) + '</span> ' + escapeHtml(l.msg) + '</div>';
  },
  _append: function (l, scroll) {
    var el = document.getElementById("console-body");
    var atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
    el.insertAdjacentHTML("beforeend", this._fmt(l));
    if (el.childElementCount > this._max) el.removeChild(el.firstChild);
    if (scroll && atBottom) el.scrollTop = el.scrollHeight;
  },
  render: function (scroll) {
    var self = this;
    var el = document.getElementById("console-body");
    var html = [];
    this._lines.forEach(function (l) { if (self._show(l)) html.push(self._fmt(l)); });
    el.innerHTML = html.join("") || '<div class="text-muted">nothing matches</div>';
    if (scroll !== false) el.scrollTop = el.scrollHeight;
    this._status();
  },
};
