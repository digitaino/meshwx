// Meshcore Weather admin portal: one page, hash routing, vanilla JS.
//
// Sections: overview, textbot, broadcasts, radio, satellite, system (logs |
// settings). One poller feeds the header strip and the Overview; every other
// section loads on enter and stops its timers and streams on leave.

// Every state-changing request carries a header a cross-site page cannot
// add without a CORS preflight (which the server never grants). The server
// refuses POST/PUT/DELETE without it.
(function () {
  var nativeFetch = window.fetch;
  window.fetch = function (url, opts) {
    opts = opts || {};
    var method = (opts.method || "GET").toUpperCase();
    if (method !== "GET" && method !== "HEAD" && typeof url === "string" && url.charAt(0) === "/") {
      var h = new Headers(opts.headers || {});
      h.set("X-Requested-With", "meshcore-portal");
      opts.headers = h;
    }
    return nativeFetch.call(window, url, opts);
  };
})();

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
  });
}

function api(url, opts) {
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

function tile(label, value, hint, cls, href) {
  var inner = '<div class="stat-label">' + esc(label) + '</div>' +
    '<div class="stat-value ' + (cls || "") + '">' + value + '</div>' +
    '<div class="stat-hint" title="' + esc(hint || "") + '">' + esc(hint || "") + '</div>';
  return href ? '<a class="stat" href="' + href + '">' + inner + '</a>' : '<div class="stat">' + inner + '</div>';
}

function ago(s) {
  if (s == null || isNaN(s)) return "–";
  s = Math.max(0, Math.round(s));
  if (s < 90) return s + "s";
  if (s < 5400) return Math.round(s / 60) + "m";
  if (s < 172800) return Math.round(s / 3600) + "h";
  return Math.round(s / 86400) + "d";
}

function agoAt(t) { return t ? ago(Date.now() / 1000 - t) : "–"; }

function deliveryBadge(d) {
  if (!d) return "";
  var b = "";
  if (d.acked) b += ' <span class="badge badge-success" title="the recipient acknowledged it">ack ' + (d.rtt_ms != null ? (d.rtt_ms / 1000).toFixed(1) + " s" : "") + "</span>";
  else if (d.echo) {
    var tip = "a repeater repeated it, timed from the transmission that was echoed";
    if (d.resent && d.echo_total_ms != null) tip += ". " + (d.echo_total_ms / 1000).toFixed(1) + " s since the first send; the resend is byte-identical so we cannot tell which copy came back";
    b += ' <span class="badge badge-success" title="' + tip + '">echo ' + (d.echo_ms != null ? (d.echo_ms / 1000).toFixed(1) + " s" : "") + (d.via ? " via " + esc(d.via) : "") + "</span>";
  }
  else if (d.result === "skipped") b += ' <span class="badge badge-warning">no echo · not resent: ' + esc(d.skipped) + "</span>";
  else b += ' <span class="badge badge-danger">' + (d.result === "no_ack" ? "no ack" : "no echo") + "</span>";
  if (d.resent) b += ' <span class="badge badge-warning">resent ×' + d.resent + "</span>";
  // Only the settled CoreScope reading lands here. The probe taken to decide
  // the resend is seconds old and used to render as "direct only: 1" on
  // exactly the replies the mesh had in fact carried.
  if (d.observed_by) {
    var rep = d.observed_repeats || 0, direct = d.observed_by - rep;
    b += rep ? ' <span class="badge ' + (rep >= 2 ? "badge-success" : "badge-muted") + '" title="CoreScope: observers whose copy came through a repeater' + (d.observed_paths ? ": " + esc(d.observed_paths.join(" | ")) : "") + '">repeat heard by ' + rep + " observer" + (rep === 1 ? "" : "s") + "</span>" : "";
    if (direct) b += ' <span class="badge badge-muted" title="CoreScope: heard us at zero hops, proves no repeat">direct only: ' + direct + "</span>";
  }
  return b;
}

function nextRun(s) { return s == null ? "nothing due" : s === 0 ? "due at the next tick" : "next in " + ago(s); }

function hhmmss(t) { return new Date(t * 1000).toTimeString().slice(0, 8); }

function $(id) { return document.getElementById(id); }

function setVal(id, v) {
  var el = $(id);
  if (el && document.activeElement !== el) el.value = v == null ? "" : v;
}

function setDot(id, cls) { var el = $(id); if (el) el.className = "dot " + cls; }

// A live stream: hello frame → green dot, error → red dot, items → onEvent.
function liveStream(url, onEvent, dotId) {
  var es = new EventSource(url);
  setDot(dotId, "dot-gray");
  es.onopen = function () { setDot(dotId, "dot-green"); };
  es.onerror = function () { setDot(dotId, "dot-red"); };
  es.onmessage = function (m) {
    var d; try { d = JSON.parse(m.data); } catch (e) { return; }
    if (!d || d.hello) { setDot(dotId, "dot-green"); return; }
    onEvent(d);
  };
  return es;
}

function appendLine(el, html, max) {
  var atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  el.insertAdjacentHTML("beforeend", html);
  while (el.childElementCount > max) el.removeChild(el.firstChild);
  if (atBottom) el.scrollTop = el.scrollHeight;
}

// ---------------------------------------------------------------------------
// The portal
// ---------------------------------------------------------------------------

var Portal = {

  // -- Router: #section or #system/tab ----------------------------------------

  router: {
    sections: ["overview", "textbot", "broadcasts", "radio", "satellite", "system"],
    legacy: { console: "system/logs", sdr: "satellite", broadcast: "broadcasts", map: "overview" },
    current: null,

    init: function () {
      var self = this;
      window.addEventListener("hashchange", function () { self.apply(); });
      this.apply();
    },

    go: function (target) { window.location.hash = "#" + target; },

    apply: function () {
      var raw = (window.location.hash || "#overview").slice(1);
      if (this.legacy[raw]) raw = this.legacy[raw];
      var parts = raw.split("/"), sec = parts[0], sub = parts[1] || null;
      if (this.sections.indexOf(sec) === -1) { sec = "overview"; sub = null; }
      if (sec !== this.current) {
        var prev = this.current;
        if (prev && Portal[prev].onLeave) Portal[prev].onLeave();
        this.current = sec;
        document.querySelectorAll("#main-nav a[data-section]").forEach(function (a) {
          a.classList.toggle("active", a.dataset.section === sec);
        });
        document.querySelectorAll(".section").forEach(function (s) {
          s.classList.toggle("active", s.id === "section-" + sec);
        });
        window.scrollTo(0, 0);
        if (Portal[sec].onEnter) Portal[sec].onEnter();
      }
      if (sec === "system") Portal.system.showTab(sub || "logs");
    },
  },

  // -- UI -----------------------------------------------------------------------

  ui: {
    _toastTimer: null,
    toast: function (msg, ok) {
      var el = $("toast");
      el.textContent = msg;
      el.className = "toast" + (ok === false ? " err" : "");
      el.hidden = false;
      clearTimeout(this._toastTimer);
      this._toastTimer = setTimeout(function () { el.hidden = true; }, 3500);
    },
    openModal: function (html, wide) {
      var c = $("modal-content");
      c.innerHTML = html;
      c.className = "modal-content" + (wide ? " modal-wide" : "");
      $("modal-overlay").hidden = false;
    },
    closeModal: function () { $("modal-overlay").hidden = true; $("modal-content").innerHTML = ""; },
  },

  // -- One poller for the header strip and the Overview -------------------------

  poll: {
    _timer: null, last: null,
    start: function () {
      var self = this;
      this.tick();
      this._timer = setInterval(function () { self.tick(); }, 15000);
    },
    tick: function () {
      var self = this;
      api("/api/overview").then(function (d) {
        self.last = d;
        Portal.header.render(d);
        if (Portal.router.current === "overview") Portal.overview.render(d);
      }).catch(function (e) {
        Portal.header.offline(e.message);
        if (Portal.router.current === "overview") $("ov-updated").textContent = "Portal unreachable: " + e.message;
      });
    },
  },

  header: {
    render: function (d) {
      var sat = d.satellite || {}, r = d.radio || {};
      var s = $("strip-sat"), ra = $("strip-radio"), tx = $("strip-tx");
      s.className = "strip-item" + (sat.locked ? "" : sat.reachable ? " warn" : " bad");
      s.innerHTML = '<span class="dot ' + (sat.locked ? "dot-green" : sat.reachable ? "dot-yellow" : "dot-red") + '"></span>' +
        (sat.locked ? "dish locked" : sat.reachable ? "no lock" : "dish ?");
      var h = r.health || {}, hv = h.verdict, unwell = r.connected && (hv === "tx_suspect" || hv === "rx_silent" || r.pending_adoption);
      ra.className = "strip-item" + (r.connected ? (unwell ? " warn" : "") : " bad");
      ra.innerHTML = '<span class="dot ' + (r.connected ? (unwell ? "dot-yellow" : "dot-green") : "dot-red") + '"></span>' +
        (r.connected ? esc(r.name || "radio up") + (r.pending_adoption ? " · different radio" : hv === "tx_suspect" ? " · TX suspect" : hv === "rx_silent" ? " · hearing nothing" : "") : "radio down");
      ra.title = h.reason || "";
      var mode = r.reply_mode;
      var txCls = !r.tx_enabled ? "" : mode === "channel" ? " bad" : "";
      tx.className = "strip-item" + txCls;
      tx.innerHTML = '<span class="dot ' + (!r.tx_enabled ? "dot-gray" : mode === "channel" ? "dot-red" : "dot-green") + '"></span>' +
        (!r.tx_enabled ? "TX off" : mode === "channel" ? "TX on · CHANNEL replies" : mode === "dm_only" ? "TX on · DM only" : "TX on · DM");
    },
    offline: function (msg) {
      ["strip-sat", "strip-radio", "strip-tx"].forEach(function (id) {
        var el = $(id); el.className = "strip-item bad"; el.innerHTML = '<span class="dot dot-red"></span>' + esc(id === "strip-sat" ? "portal ?" : "");
      });
    },
  },

  // -- Overview -----------------------------------------------------------------

  overview: {
    onEnter: function () {
      if (Portal.poll.last) this.render(Portal.poll.last);
      Portal.poll.tick();
    },
    render: function (d) {
      var sat = d.satellite || {}, feed = d.feed || {}, r = d.radio || {}, tb = d.textbot || {}, bc = d.broadcasts || {};
      var pr = d.problems || {}, au = d.audit || {}, host = d.host || {};
      var satVal = !sat.reachable ? "unreachable" : sat.locked ? "locked" : "no lock";
      var satCls = !sat.reachable ? "bad" : sat.locked ? "ok" : "warn";
      var satHint = !sat.reachable ? (sat.error || "goestools dashboard down") :
        "vit " + (sat.vit_avg != null ? sat.vit_avg : "?") + " · drops " + (sat.drops != null ? sat.drops : "?") + " · " + (sat.mode || "") + " mode";
      var feedAge = feed.newest_age_s;
      var feedCls = feedAge == null ? "bad" : feedAge > 1800 ? "warn" : "";
      var mode = r.reply_mode;
      var txCls = !r.tx_enabled ? "dim" : mode === "channel" ? "bad" : "ok";
      var txHint = !r.tx_enabled ? "receive only: no replies, no broadcasts" :
        mode === "channel" ? "reply mode CHANNEL: every reply floods the mesh" : mode === "dm_only" ? "replies by DM only" : "replies by DM; a close stranger gets one channel reply";
      var lastReq = tb.last_request_at ? agoAt(tb.last_request_at) + " ago" : "no requests yet";
      var answering = tb.requests_1h ? tb.replies_1h + " / " + tb.requests_1h : (tb.requests_24h ? tb.replies_24h + " / " + tb.requests_24h : "quiet");
      var bcVal = !bc.running ? "off" : bc.jobs_enabled + " of " + bc.jobs_total + " jobs";
      var bcHint = !bc.running ? "no data channel configured" :
        nextRun(bc.next_run_in_s) + " · " + ((bc["24h"] || {}).messages || 0) + " msgs / 24 h";
      var auVal = !au.available ? "–" : au.passed + " / " + (au.passed + au.failed);
      var auCls = !au.available ? "dim" : au.failed ? "bad" : "ok";
      var auHint = !au.available ? "no audit result yet (hourly timer)" : "passed · " + (au.at ? new Date(au.at).toLocaleTimeString() : "");
      $("ov-tiles").innerHTML =
        tile("Satellite", satVal, satHint, satCls, "#satellite") +
        tile("Feed", feedAge != null ? ago(feedAge) + " old" : "no products", (feed.products_last_hour || 0) + " products/h · " + (feed.warnings_last_hour || 0) + " warnings/h · " + (feed.products_total || 0) + " in store", feedCls, "#satellite") +
        tile("Radio", r.connected ? "up" : "down", r.connected ? (r.name || "") + " · " + (r.freq_mhz != null ? r.freq_mhz + " MHz" : "") + (r.battery_mv ? " · " + (r.battery_mv / 1000).toFixed(2) + " V" : "") : (r.error || ""), r.connected ? "ok" : "bad", "#radio") +
        tile("Transmit", r.tx_enabled ? "ON" : "OFF", txHint, txCls, "#textbot") +
        tile("Answering", answering, (tb.requests_1h ? "replies / requests, last hour" : "replies / requests, 24 h") + " · last request " + lastReq + (tb.dropped_24h ? " · " + tb.dropped_24h + " not answered / 24 h" : ""), tb.dropped_1h ? "warn" : "", "#textbot") +
        tile("Broadcasts", bcVal, bcHint, bc.running ? "" : "dim", "#broadcasts") +
        tile("Problems", pr.last_hour || 0, "warnings and errors in the log, last hour", pr.last_hour ? "warn" : "ok", "#system/logs") +
        tile("Audit", auVal, auHint, auCls, "#overview") +
        tile("Host", host.disk_used_pct != null ? host.disk_used_pct + "% disk" : "–", (host.temp_c != null ? host.temp_c + "°C · " : "") + (host.mem_available_mb != null ? host.mem_available_mb + " MB free" : "") + (host.load ? " · load " + host.load[0] : ""), host.disk_used_pct > 85 ? "bad" : host.disk_used_pct > 70 ? "warn" : "", "#system/settings") +
        tile("Bot", ago((d.bot || {}).uptime_s) + " up", "commit " + ((d.bot || {}).git || "?") + " · host up " + ago(host.uptime_s), "", "#system/settings");
      $("ov-updated").textContent = "Updated " + new Date().toLocaleTimeString();

      var rec = $("ov-recent");
      var lines = (d.recent || []).map(function (ev) { return Portal.traffic.fmt(ev); });
      rec.innerHTML = lines.join("") || '<div class="text-muted">Nothing yet. A request on the channel or a DM shows up here.</div>';
      rec.scrollTop = rec.scrollHeight;

      var el = $("ov-audit");
      if (!au.available) { el.textContent = "No audit result yet. The audit timer writes data/audit.json every hour."; return; }
      var parts = Object.keys(au.by_check || {}).map(function (k) {
        var c = au.by_check[k];
        return '<span class="badge ' + (c.fail ? "badge-danger" : "badge-success") + '">' + esc(k) + " " + c.pass + "/" + (c.pass + c.fail) + '</span>';
      }).join(" ");
      var html = '<div class="mb-2">' + esc(new Date(au.at).toLocaleString()) + ": <strong>" + au.passed + " passed, " + au.failed + ' failed</strong></div><div class="chips mb-2">' + parts + "</div>";
      (au.failures || []).forEach(function (f) {
        html += '<div><span class="badge badge-danger">fail</span> ' + esc(f.check + " " + f.subject) + ': <span class="text-muted">' + esc(f.detail) + "</span></div>";
      });
      el.innerHTML = html;
    },
  },

  // -- Text bot: traffic feed and stats ------------------------------------------

  traffic: {
    _events: [], _max: 1500, _kind: "all", _sse: null, _lastId: 0, _timer: null,
    _filters: {
      all: null,
      channel: ["channel_in", "reply_channel", "peer"],
      dm: ["dm_in", "reply_dm", "dm_failed", "admin"],
      dropped: ["dropped", "dm_failed"],
      adverts: ["advert", "advert_out", "link_test"],
      apps: ["data_request", "reply_data"],
    },

    start: function () {
      var self = this;
      api("/api/traffic?n=400").then(function (d) {
        self._events = d.events || [];
        self._lastId = self._events.length ? self._events[self._events.length - 1].id : 0;
        self.renderStats(d.stats);
        self.render();
        self._connect();
      }).catch(function (e) { Portal.ui.toast("Traffic: " + e.message, false); });
      if (this._timer) clearInterval(this._timer);
      this._timer = setInterval(function () {
        api("/api/traffic?n=1").then(function (d) { self.renderStats(d.stats); }).catch(function () {});
      }, 30000);
    },
    stop: function () {
      if (this._sse) { this._sse.close(); this._sse = null; }
      if (this._timer) { clearInterval(this._timer); this._timer = null; }
    },
    _connect: function () {
      var self = this;
      if (this._sse) this._sse.close();
      this._sse = liveStream("/api/traffic/stream", function (ev) {
        if (ev.id && ev.id <= self._lastId) {          // an update to a line we have (delivery outcome)
          for (var i = self._events.length - 1; i >= 0; i--) if (self._events[i].id === ev.id) { self._events[i] = ev; break; }
          var line = $("traffic-body").querySelector('[data-id="' + ev.id + '"]');
          if (line) line.outerHTML = self.fmt(ev);
          return;
        }
        self._lastId = ev.id || self._lastId;
        self._events.push(ev);
        if (self._events.length > self._max) self._events.splice(0, self._events.length - self._max);
        if (self._show(ev)) appendLine($("traffic-body"), self.fmt(ev), self._max);
        self._status();
      }, "traffic-dot");
    },
    _status: function () { $("traffic-status").textContent = this._events.length + " events"; },
    setKind: function (k) {
      this._kind = k;
      document.querySelectorAll("#traffic-kinds .sub-tab").forEach(function (b) { b.classList.toggle("active", b.dataset.k === k); });
      this.render();
    },
    _show: function (ev) { var f = this._filters[this._kind]; return !f || f.indexOf(ev.kind) !== -1; },
    renderStats: function (st) {
      if (!st) return;
      var w24 = st.windows["24h"], w1 = st.windows["1h"], lt = st.lifetime, lat = st.latency || {};
      var top = Object.keys(w24.by_command || {}).sort(function (a, b) { return w24.by_command[b] - w24.by_command[a]; }).slice(0, 3)
        .map(function (k) { return k + " " + w24.by_command[k]; }).join(", ");
      var since = lt.since ? new Date(lt.since * 1000).toLocaleDateString() : "";
      var dv = ((st.delivery || {}).windows || {})["24h"] || {};
      var dvVal = dv.sent ? dv.heard_pct + "%" : "–";
      var dvHint = dv.sent ? dv.heard + " of " + dv.sent + " replies heard back" + (dv.echo_median_ms != null ? " · echo " + (dv.echo_median_ms / 1000).toFixed(1) + " s" : "") + (dv.resent ? " · " + dv.resent + " resent" : "") : "no replies yet";
      $("traffic-stats").innerHTML =
        tile("Requests · 24 h", w24.requests, w1.requests + " in the last hour" + (top ? " · " + top : "") + " · since " + since + ": " + lt.requests) +
        tile("Heard back · 24 h", dvVal, dvHint, dv.sent ? (dv.heard_pct >= 80 ? "ok" : dv.heard_pct >= 50 ? "warn" : "bad") : "dim") +
        tile("Replies · 24 h", w24.replies, w24.dm_replies + " by DM · " + w24.channel_replies + " on the channel · " + w24.chars_sent + " chars") +
        tile("Not answered · 24 h", w24.dropped, "rate limits, hop gate, budgets, nearer bot", w24.dropped ? "warn" : "") +
        tile("Senders · 24 h", w24.senders, w24.senders ? "distinct nodes" : "nobody yet") +
        tile("Reply time", lat.median_ms != null ? lat.median_ms + " ms" : "–", lat.p90_ms != null ? "median · p90 " + lat.p90_ms + " ms" : "receipt to send");
    },
    fmt: function (ev) {
      var ts = hhmmss(ev.t);
      var arrow = ev.dir === "in" ? '<span class="in">← in </span>' : '<span class="out">→ out</span>';
      var tr = ev.transport === "channel" ? "CH" : ev.transport === "channel_data" ? "DGM" : ev.transport === "dm" ? "DM" : ev.transport === "console" ? "WEB" : (ev.kind || "").indexOf("advert") === 0 ? "ADV" : "";
      var who = ev.sender ? esc(ev.sender) : (ev.key ? '<span class="text-muted">' + esc(ev.key) + "</span>" : "");
      var body = "", cls = "badge-muted";
      var cmd = ev.command ? ' <span class="text-muted">[' + esc(ev.command) + (ev.location ? " · " + esc(ev.location) : "") + "]</span>" : "";
      switch (ev.kind) {
        case "channel_in": case "dm_in":
          body = esc(ev.text) + cmd + (ev.hops != null ? ' <span class="text-muted">' + ev.hops + " hops</span>" : "");
          break;
        case "reply_dm": case "reply_channel":
          cls = "badge-success";
          body = esc(ev.text) + ' <span class="text-muted">' + (ev.chars || 0) + " ch" + (ev.ms != null ? " · " + ev.ms + " ms" : "") + (ev.ok === false ? " · TX off" : "") + "</span>" + deliveryBadge(ev.delivery);
          break;
        case "dropped":
          cls = "badge-warning";
          body = '<span class="warn-text">not answered: ' + esc(ev.reason) + "</span>" + cmd;
          break;
        case "dm_failed": cls = "badge-danger"; body = '<span class="bad-text">DM failed</span> ' + esc(ev.text); break;
        case "peer": body = '<span class="text-muted">peer bot, ignored:</span> ' + esc(ev.text); break;
        case "advert": body = '<span class="text-muted">advert heard</span>'; break;
        case "advert_out": body = '<span class="text-muted">our advert (flood)</span>'; break;
        case "link_test": body = '<span class="text-muted">link test datagram (6 B)</span>' + deliveryBadge(ev.delivery); break;
        case "reply_data":
          cls = "badge-success";
          body = '<span class="text-muted">app answer</span> ' + esc(ev.text || "") +
            (ev.ms != null ? ' <span class="text-muted">· ' + ev.ms + " ms</span>" : "") +
            (ev.ok === false ? ' <span class="text-muted">· TX off</span>' : "") + deliveryBadge(ev.delivery);
          break;
        case "data_request":
          body = '<span class="text-muted">app data request</span> ' + esc(ev.text) +
            (ev.transport === "channel_data" ? ' <span class="text-muted">datagram' + (ev.hops != null ? " · " + ev.hops + " hops" : "") + "</span>" : "");
          break;
        case "admin": body = '<span class="text-muted">admin command:</span> ' + esc(ev.command); break;
        case "console": body = esc(ev.text) + ' <span class="text-muted">[' + esc(ev.command) + (ev.location ? " · " + esc(ev.location) : "") + " · " + (ev.chars || 0) + " ch]</span>"; break;
        default: body = esc(ev.text || ev.reason || "");
      }
      return '<div class="console-line" data-id="' + (ev.id || "") + '"><span class="text-muted">' + ts + "</span> " + arrow + " " +
        '<span class="badge ' + cls + '" style="min-width:34px;text-align:center">' + (tr || esc(ev.kind)) + "</span> " +
        (who ? "<b>" + who + "</b> " : "") + body + "</div>";
    },
    render: function () {
      var self = this, el = $("traffic-body"), html = [];
      this._events.forEach(function (ev) { if (self._show(ev)) html.push(self.fmt(ev)); });
      el.innerHTML = html.join("") || '<div class="text-muted">Nothing yet. A request on the channel or a DM will show up here.</div>';
      el.scrollTop = el.scrollHeight;
      this._status();
    },
  },

  // -- Text bot: command tester, behaviour, channels line -------------------------

  // Every gate between a request and an answer: where it stands, and when it
  // opens. The bot rations itself in five places because it spends everyone's
  // airtime; until this card they were invisible, so a refused request and a
  // lost one looked the same from here.
  limits: {
    _timer: null, _rows: [], _at: 0,

    start: function () {
      var self = this;
      this.refresh();
      if (this._timer) clearInterval(this._timer);
      this._timer = setInterval(function () { self.tick(); }, 1000);
    },
    stop: function () { if (this._timer) { clearInterval(this._timer); this._timer = null; } },

    refresh: function () {
      var self = this;
      api("/api/limits").then(function (d) {
        self._rows = d.rows || [];
        self._at = Date.now();
        self.render();
      }).catch(function () {});
    },

    // The countdowns run in the browser between polls, so the card ticks down
    // a second at a time instead of jumping every fifteen.
    tick: function () {
      var elapsed = (Date.now() - this._at) / 1000;
      var stale = false;
      this._rows.forEach(function (r) {
        if (r.opens_in_s > 0 && r.opens_in_s - elapsed <= 0) stale = true;
      });
      if (stale || elapsed > 15) { this.refresh(); return; }
      this.render(elapsed);
    },

    render: function (elapsed) {
      var body = $("limits-body");
      if (!body) return;
      elapsed = elapsed || 0;
      body.innerHTML = this._rows.map(function (r) {
        var left = Math.max(0, Math.round((r.opens_in_s || 0) - elapsed));
        var dot = left > 0 ? "dot-yellow" : (r.state === "spent" ? "dot-red" : "dot-green");
        var when = left > 0
          ? "in " + (left >= 60 ? Math.floor(left / 60) + "m " + ("0" + (left % 60)).slice(-2) + "s" : left + "s")
          : "ready";
        var btn = r.resettable
          ? '<button class="btn btn-mini" onclick="Portal.limits.reset(' + JSON.stringify(r.id).replace(/"/g, "&quot;") + ')">Reset</button>'
          : "";
        return '<div class="limit-row">' +
          '<div><span class="dot ' + dot + '"></span><span class="limit-name">' + esc(r.name) + "</span>" +
          '<div class="text-small text-muted">' + esc(r.rule) + "</div></div>" +
          '<div class="text-small">' + esc(r.detail) + "</div>" +
          '<div class="text-small text-mono">' + esc(when) + "</div>" +
          "<div>" + btn + "</div></div>";
      }).join("");
    },

    // Resetting spends airtime the rule was holding back, so it says so.
    reset: function (id) {
      var self = this;
      api("/api/limits/reset", { method: "POST", body: { id: id } }).then(function (d) {
        self._rows = d.rows || [];
        self._at = Date.now();
        self.render();
        Portal.ui.toast("Limit cleared: the next request is answered", true);
      }).catch(function (e) { Portal.ui.toast("Reset: " + e.message, false); });
    },
  },

  textbot: {
    _loaded: false, _orig: {},
    _keys: ["MCW_REPLY_MODE", "MCW_CHANNEL_REPLY_MAX_HOPS", "MCW_ADVERT_INTERVAL_HOURS", "MCW_PEER_BOT_PREFIX",
            "MCW_RETRANSMIT_MAX", "MCW_ECHO_WINDOW_S", "MCW_RETRANSMIT_PER_HOUR"],

    onEnter: function () {
      if (!this._loaded) {
        this._loaded = true;
        var ex = ["wx round rock tx", "forecast austin", "warn TX", "storm TX", "space", "metar KAUS", "more", "help"];
        $("try-examples").innerHTML = ex.map(function (t) {
          return '<button class="btn btn-mini" onclick="Portal.textbot.send(' + JSON.stringify(t).replace(/"/g, "&quot;") + ')">' + esc(t) + "</button>";
        }).join("");
        api("/api/console/help").then(function (d) { $("help-text").textContent = d.help; }).catch(function () {});
      }
      this.loadBehaviour();
      Portal.traffic.start();
      Portal.limits.start();
    },
    onLeave: function () { Portal.traffic.stop(); Portal.limits.stop(); },

    loadBehaviour: function () {
      var self = this;
      api("/api/radio").then(function (d) {
        var cfg = d.configured_channels || {}, slots = (d.info && d.info.channels) || {};
        $("traffic-title").textContent = cfg.text || "the channel";
        var line = function (role, label) {
          var name = cfg[role];
          if (!name) return "<div><b>" + label + ":</b> off</div>";
          var where = !d.connected ? "applied when the radio connects" : slots[role] != null ? "slot " + slots[role] : "not resolved on the node";
          return "<div><b>" + label + ":</b> " + esc(name) + ' <span class="text-muted">(' + where + ")</span></div>";
        };
        $("listening").innerHTML = line("text", "Text commands") + line("data", "Data");
        var peers = d.peer_bots || [];
        $("peer-bots").textContent = "Peer bots heard: " + (peers.length ? peers.map(function (p) { return p.name + " (" + p.lat.toFixed(2) + "," + p.lon.toFixed(2) + ")"; }).join(", ") : "none") +
          ". A request that names a place is answered only by the nearest bot.";
        self._orig.MCW_REPLY_MODE = d.reply_mode; setVal("env-MCW_REPLY_MODE", d.reply_mode);
        self._orig.MCW_CHANNEL_REPLY_MAX_HOPS = String(d.channel_reply_max_hops); setVal("env-MCW_CHANNEL_REPLY_MAX_HOPS", d.channel_reply_max_hops);
      }).catch(function (e) { $("listening").textContent = "Radio state unavailable: " + e.message; });
      api("/api/system").then(function (sy) {
        var s = sy.settings || {};
        self._orig.MCW_ADVERT_INTERVAL_HOURS = String(s.advert_interval_hours); setVal("env-MCW_ADVERT_INTERVAL_HOURS", s.advert_interval_hours);
        self._orig.MCW_PEER_BOT_PREFIX = s.peer_bot_prefix || ""; setVal("env-MCW_PEER_BOT_PREFIX", s.peer_bot_prefix);
        ["MCW_RETRANSMIT_MAX", "MCW_ECHO_WINDOW_S", "MCW_RETRANSMIT_PER_HOUR"].forEach(function (k) {
          var v = s[k.slice(4).toLowerCase()]; self._orig[k] = v == null ? "" : String(v); setVal("env-" + k, v);
        });
      }).catch(function () {});
    },
    saveBehaviour: function (btn) {
      var self = this, body = {};
      this._keys.forEach(function (k) { var v = $("env-" + k).value.trim(); if (v !== (self._orig[k] == null ? "" : self._orig[k])) body[k] = v; });
      var st = $("behaviour-status");
      if (!Object.keys(body).length) { st.textContent = "Nothing changed"; return; }
      if (body.MCW_REPLY_MODE === "channel" && !confirm("Switch to CHANNEL replies? Every answer will flood the mesh through every repeater in range. Use this for testing only.")) {
        setVal("env-MCW_REPLY_MODE", this._orig.MCW_REPLY_MODE); return;
      }
      btn.disabled = true; st.textContent = "Saving…";
      api("/api/settings/env", { method: "POST", body: body }).then(function (d) {
        st.textContent = d.note; Portal.ui.toast("Behaviour saved"); self.loadBehaviour(); Portal.poll.tick();
      }).catch(function (e) { st.textContent = e.message; Portal.ui.toast(e.message, false); })
        .finally(function () { btn.disabled = false; });
    },
    send: function (preset) {
      var input = $("try-input");
      var text = (preset || input.value).trim();
      if (!text) return;
      if (!preset) input.value = "";
      var log = $("try-log"), id = "try" + Date.now();
      log.insertAdjacentHTML("beforeend", '<div><span class="text-muted">you&gt;</span> ' + esc(text) + '</div><div id="' + id + '" class="text-muted">…</div>');
      log.scrollTop = log.scrollHeight;
      api("/api/console", { method: "POST", body: { text: text } }).then(function (d) {
        var el = $(id);
        el.className = "";
        el.innerHTML = '<div style="margin:2px 0 8px 0"><span class="badge badge-muted">DM · ' + d.chars + ' ch</span> ' + esc(d.reply || "(no reply)") +
          (d.has_more ? ' <span class="badge badge-warning">more pages: send "more"</span>' : "") + "</div>" +
          '<span class="text-muted">[' + esc(d.command) + (d.location ? " · " + esc(d.location) : "") + " · " + d.ms + " ms]</span>";
        log.scrollTop = log.scrollHeight;
      }).catch(function (e) { $(id).textContent = "error: " + e.message; });
    },
  },

  // -- Broadcasts: jobs, counters, broadcast log ---------------------------------

  broadcasts: {
    _meta: null, _timer: null, _sse: null, _n: 0,
    _labels: { broadcast: "Broadcast", beacon: "Discovery beacon", app_request: "App request", app_response: "App reply",
               refresh: "App refresh", throttled: "Throttled" },

    onEnter: function () {
      var self = this;
      if (!this._meta) api("/api/schedule/meta").then(function (d) { self._meta = d; }).catch(function () {});
      this.loadJobs(); this.loadStats();
      this._timer = setInterval(function () { self.loadJobs(); self.loadStats(); }, 30000);
      api("/api/activity?limit=150").then(function (d) {
        var el = $("bclog-body");
        var ev = d.events || [];
        self._n = ev.length;
        el.innerHTML = ev.slice().reverse().map(function (e) { return self.fmt(e); }).join("") || '<div class="text-muted">Nothing sent on the data channel yet.</div>';
        el.scrollTop = el.scrollHeight;
        self._status();
        if (self._sse) self._sse.close();
        self._sse = liveStream("/api/activity/stream", function (e) {
          appendLine(el, self.fmt(e), 500); self._n++; self._status();
        }, "bclog-dot");
      }).catch(function (e) { $("bclog-body").textContent = "Broadcast log unavailable: " + e.message; });
    },
    onLeave: function () {
      if (this._timer) { clearInterval(this._timer); this._timer = null; }
      if (this._sse) { this._sse.close(); this._sse = null; }
    },
    _status: function () { $("bclog-status").textContent = this._n + " events"; },
    fmt: function (e) {
      var dir = e.direction === "in" ? '<span class="in">← in </span>' : '<span class="out">→ out</span>';
      var label = this._labels[e.event_type] || e.event_type;
      var cls = e.event_type === "throttled" ? "badge-warning" : e.direction === "out" ? "badge-success" : "badge-muted";
      return '<div class="console-line"><span class="text-muted">' + hhmmss(e.ts) + "</span> " + dir + ' <span class="badge ' + cls + '">' + esc(label) + "</span> " + esc(e.summary) + "</div>";
    },

    loadStats: function () {
      api("/api/stats").then(function (d) {
        var by = {}; (d.stats || []).forEach(function (s) { by[s.window_minutes] = s; });
        var h1 = by[60] || {}, d1 = by[1440] || {}, m15 = by[15] || {};
        var kb = function (b) { return b >= 1024 ? (b / 1024).toFixed(1) + " KB" : (b || 0) + " B"; };
        var ov = (Portal.poll.last || {}).broadcasts || {};
        $("bc-tiles").innerHTML =
          tile("Jobs", ov.running === false ? "off" : (ov.jobs_enabled != null ? ov.jobs_enabled + " of " + ov.jobs_total : "–"), ov.running === false ? "no data channel configured" : "enabled · " + nextRun(ov.next_run_in_s), ov.running === false ? "dim" : "", null) +
          tile("Last 15 min", m15.messages || 0, kb(m15.bytes) + " on the data channel") +
          tile("Last hour", h1.messages || 0, kb(h1.bytes)) +
          tile("Last 24 h", d1.messages || 0, kb(d1.bytes));
      }).catch(function () {});
    },
    _productLabel: function (k) { var i = ((this._meta || {}).product_info || {})[k]; return i ? i.label : k; },
    _locationLabel: function (k) { var i = ((this._meta || {}).location_info || {})[k]; return i ? i.label : k; },

    loadJobs: function () {
      var self = this;
      api("/api/schedule/jobs").then(function (data) {
        var tbody = $("jobs-body");
        if (!data.jobs || !data.jobs.length) { tbody.innerHTML = '<tr><td colspan="9" class="text-muted">No jobs configured.</td></tr>'; return; }
        tbody.innerHTML = data.jobs.map(function (j) {
          var id = esc(j.id);
          return "<tr" + (j.enabled ? "" : ' class="text-muted"') + '><td><strong>' + esc(j.name) + '</strong><br><code class="text-muted text-small">' + id + "</code></td>" +
            "<td>" + esc(self._productLabel(j.product)) + "</td>" +
            "<td>" + esc(self._locationLabel(j.location_type)) + (j.location_id ? ": " + esc(j.location_id) : "") + "</td>" +
            "<td>" + j.interval_minutes + " min</td>" +
            "<td>" + (j.last_run_seconds_ago != null ? ago(j.last_run_seconds_ago) + " ago" : "never") + "</td>" +
            "<td>" + (!j.enabled ? "–" : j.next_run_in_seconds != null && j.last_run_unix ? "in " + ago(j.next_run_in_seconds) : "next tick") + "</td>" +
            "<td>" + j.last_bytes + ' B <span class="text-muted">(' + j.last_msg_count + " msg)</span></td>" +
            '<td><button class="btn btn-mini" onclick="Portal.broadcasts.toggleJob(\'' + id + '\')">' + (j.enabled ? "on" : "off") + "</button></td>" +
            '<td class="actions"><button class="btn btn-mini" onclick="Portal.broadcasts.runNow(\'' + id + '\')">Run now</button> ' +
            '<button class="btn btn-mini" onclick="Portal.broadcasts.editJob(\'' + id + '\')">Edit</button> ' +
            '<button class="btn btn-mini" onclick="Portal.broadcasts.deleteJob(\'' + id + '\')">Delete</button></td></tr>';
        }).join("");
      }).catch(function (e) {
        $("jobs-body").innerHTML = '<tr><td colspan="9" class="text-muted">' + esc(e.message) + "</td></tr>";
      });
    },

    runDue: function (btn) {
      btn.disabled = true;
      var self = this;
      api("/api/actions/broadcast", { method: "POST" }).then(function (d) {
        Portal.ui.toast(d.messages_sent ? "Sent " + d.messages_sent + " message(s)" : "Nothing was due; use Run now on a job to force it");
        self.loadJobs();
      }).catch(function (e) { Portal.ui.toast(e.message, false); }).finally(function () { btn.disabled = false; });
    },

    openJobModal: function (mode, job) {
      var meta = this._meta || {}, pInfo = meta.product_info || {}, lInfo = meta.location_info || {};
      var isEdit = mode === "edit" && job;
      var selectedProduct = isEdit ? job.product : (meta.products || [])[0] || "";
      var productOpts = (meta.products || []).map(function (p) {
        return '<option value="' + p + '"' + (p === selectedProduct ? " selected" : "") + ">" + esc((pInfo[p] || {}).label || p) + "</option>";
      }).join("");
      var validLocs = (pInfo[selectedProduct] || {}).locations || meta.location_types || [];
      var selectedLoc = isEdit ? job.location_type : validLocs[0] || "";
      var locOpts = validLocs.map(function (t) {
        return '<option value="' + t + '"' + (t === selectedLoc ? " selected" : "") + ">" + esc((lInfo[t] || {}).label || t) + "</option>";
      }).join("");
      var html =
        "<h2>" + (isEdit ? "Edit job" : "New job") + "</h2>" +
        '<form onsubmit="Portal.broadcasts.saveJob(event)">' +
        '<input type="hidden" id="jf-mode" value="' + mode + '"><input type="hidden" id="jf-original-id" value="' + (isEdit ? esc(job.id) : "") + '">' +
        '<label>ID (slug)<input type="text" id="jf-id" required pattern="[a-z0-9_-]+" maxlength="64" value="' + (isEdit ? esc(job.id) : "") + '"' + (isEdit ? " readonly" : "") + "></label>" +
        '<label>Display name<input type="text" id="jf-name" required maxlength="120" value="' + (isEdit ? esc(job.name) : "") + '"></label>' +
        '<label>Product<select id="jf-product" required onchange="Portal.broadcasts._onProductChange()">' + productOpts + "</select>" +
        '<span class="form-hint" id="jf-product-desc">' + esc((pInfo[selectedProduct] || {}).desc || "") + "</span></label>" +
        '<label>Location<select id="jf-loctype" required onchange="Portal.broadcasts._onLocTypeChange()">' + locOpts + "</select></label>" +
        '<div id="jf-locid-group"' + (selectedLoc !== "coverage" ? "" : " hidden") + '><label>Location ID<input type="text" id="jf-locid" placeholder="' + esc((lInfo[selectedLoc] || {}).placeholder || "") + '" value="' + (isEdit ? esc(job.location_id || "") : "") + '"></label></div>' +
        '<label>Interval (minutes)<input type="number" id="jf-interval" required min="1" max="10080" value="' + (isEdit ? job.interval_minutes : 60) + '"></label>' +
        '<label class="checkbox"><input type="checkbox" id="jf-enabled"' + (isEdit ? (job.enabled ? " checked" : "") : " checked") + "> Enabled</label>" +
        '<div class="flex gap-2 mt-4"><button type="submit" class="btn btn-primary">Save</button><button type="button" class="btn" onclick="Portal.ui.closeModal()">Cancel</button></div></form>';
      Portal.ui.openModal(html);
    },
    _onProductChange: function () {
      var meta = this._meta || {}, pInfo = meta.product_info || {}, lInfo = meta.location_info || {};
      var info = pInfo[$("jf-product").value] || {};
      $("jf-product-desc").textContent = info.desc || "";
      var validLocs = info.locations || meta.location_types || [];
      var sel = $("jf-loctype"), cur = sel.value;
      sel.innerHTML = validLocs.map(function (t) { return '<option value="' + t + '">' + esc((lInfo[t] || {}).label || t) + "</option>"; }).join("");
      if (validLocs.indexOf(cur) !== -1) sel.value = cur;
      this._onLocTypeChange();
    },
    _onLocTypeChange: function () {
      var lInfo = ((this._meta || {}).location_info) || {};
      var lt = $("jf-loctype").value;
      $("jf-locid-group").hidden = lt === "coverage";
      $("jf-locid").placeholder = (lInfo[lt] || {}).placeholder || "";
    },
    saveJob: function (ev) {
      ev.preventDefault();
      var mode = $("jf-mode").value, origId = $("jf-original-id").value, self = this;
      var body = {
        id: $("jf-id").value.trim(), name: $("jf-name").value.trim(), product: $("jf-product").value,
        location_type: $("jf-loctype").value, location_id: $("jf-locid").value.trim(),
        interval_minutes: parseInt($("jf-interval").value, 10), enabled: $("jf-enabled").checked,
      };
      var url = mode === "edit" ? "/api/schedule/jobs/" + encodeURIComponent(origId) : "/api/schedule/jobs";
      api(url, { method: mode === "edit" ? "PUT" : "POST", body: body }).then(function () {
        Portal.ui.toast("Job saved"); Portal.ui.closeModal(); self.loadJobs();
      }).catch(function (e) { Portal.ui.toast("Save failed: " + e.message, false); });
    },
    toggleJob: function (id) {
      var self = this;
      api("/api/schedule/jobs/" + encodeURIComponent(id) + "/toggle", { method: "POST" })
        .then(function () { self.loadJobs(); Portal.poll.tick(); }).catch(function (e) { Portal.ui.toast(e.message, false); });
    },
    runNow: function (id) {
      var self = this;
      api("/api/schedule/jobs/" + encodeURIComponent(id) + "/run-now", { method: "POST" })
        .then(function (d) { Portal.ui.toast(d.messages_sent ? "Sent " + d.messages_sent + " message(s)" : "Job ran: no data to send right now"); setTimeout(function () { self.loadJobs(); self.loadStats(); }, 500); })
        .catch(function (e) { Portal.ui.toast(e.message, false); });
    },
    editJob: function (id) {
      var self = this;
      api("/api/schedule/jobs").then(function (d) {
        var job = (d.jobs || []).find(function (j) { return j.id === id; });
        if (!job) { Portal.ui.toast("Job not found", false); return; }
        self.openJobModal("edit", job);
      });
    },
    deleteJob: function (id) {
      if (!confirm("Delete job " + id + "?")) return;
      var self = this;
      api("/api/schedule/jobs/" + encodeURIComponent(id), { method: "DELETE" })
        .then(function () { Portal.ui.toast("Job deleted"); self.loadJobs(); }).catch(function (e) { Portal.ui.toast(e.message, false); });
    },
  },

  // -- Radio -----------------------------------------------------------------------

  radio: {
    _state: null,
    onEnter: function () { this.load(); this.loadContacts(); this.loadHealth(); },

    // -- health and hardware --
    loadHealth: function () {
      var self = this;
      api("/api/radio/health").then(function (d) { self.renderHealth(d); })
        .catch(function (e) { $("health-reason").textContent = e.message; });
    },
    renderHealth: function (d) {
      var h = d.health || {}, fw = d.firmware || {}, dev = d.device || {}, rs = (d.radio_stats || {}).radio || null;
      var w1 = ((d.delivery || {})["1h"]) || {}, w24 = ((d.delivery || {})["24h"]) || {}, lag = d.loop_lag || null;
      var labels = { ok: ["OK", "badge-success"], idle: ["idle", "badge-muted"], tx_off: ["TX off", "badge-muted"],
                     tx_suspect: ["TX suspect", "badge-danger"], rx_silent: ["hearing nothing", "badge-danger"], unknown: ["unclear", "badge-warning"] };
      var lb = labels[h.verdict] || ["?", "badge-muted"];
      var badge = $("health-badge"); badge.textContent = lb[0]; badge.className = "badge " + lb[1];
      $("health-reason").textContent = h.reason || "";
      var pct = function (w) { return w.heard_pct == null ? "–" : w.heard_pct + "%"; };
      $("health-tiles").innerHTML =
        tile("Heard", pct(w1), (w1.sent || 0) + " sent · " + (w1.resent || 0) + " resent, 1 h", w1.heard_pct == null ? "" : w1.heard_pct >= 70 ? "ok" : w1.heard_pct >= 40 ? "warn" : "bad") +
        tile("Echo", w1.echo_median_ms != null ? w1.echo_median_ms + " ms" : "–", "median · 24 h: " + pct(w24) + " of " + (w24.sent || 0)) +
        tile("Last heard", h.rx_age_s != null ? ago(h.rx_age_s) : "never", "any packet from anyone", h.verdict === "rx_silent" ? "bad" : "") +
        tile("Unheard streak", String(h.unheard_streak || 0), "sends in a row with no echo", (h.unheard_streak || 0) >= 3 ? "bad" : "") +
        tile("Noise floor", rs && rs.noise_floor != null ? rs.noise_floor + " dBm" : "–", rs ? "last RSSI " + rs.last_rssi + " · SNR " + rs.last_snr : "node counters unavailable",
          rs && rs.noise_floor != null && rs.noise_floor > -95 ? "warn" : "") +
        tile("Airtime", rs ? Math.round((rs.tx_air_secs || 0) / 60) + " min TX" : "–", rs ? Math.round((rs.rx_air_secs || 0) / 60) + " min RX since boot" : "") +
        tile("Loop lag", lag && lag.running ? lag.recent_worst_s + " s" : "–",
          lag && lag.running ? "worst stall in " + Math.round((lag.window_s || 300) / 60) + " min · late " + lag.pct + "% of it" : "not being measured",
          lag && lag.pct >= 2 ? "warn" : "");
      $("health-node").innerHTML = "Firmware " + esc(fw.ver || "?") + (fw.ok ? ' <span class="badge badge-success">GRP_DATA ok</span>' :
        ' <span class="badge badge-danger">needs ' + esc(fw.min || "1.15") + "+ for the app datagrams</span>") +
        (dev.model ? " · " + esc(dev.model) : "") + (dev.max_contacts ? " · " + dev.max_contacts + " contact slots" : "");
      this.renderHardware(d);
    },
    renderHardware: function (d) {
      var p = d.profile || {}, prof = p.profile, pend = p.pending, dev = d.device || {}, port = p.port || {};
      $("hw-board").innerHTML = "<strong>" + esc(dev.model || "unknown board") + "</strong>" + (dev.ver ? " · " + esc(dev.ver) : "") +
        (port.actual ? " · on " + esc(port.actual) : "") +
        (port.actual && port.configured && port.actual !== port.configured ? ' <span class="badge badge-warning">configured ' + esc(port.configured) + "</span>" : "");
      $("hw-pending").hidden = !pend;
      if (pend) {
        var r = pend.radio || {}, pr = pend.profile || {};
        $("hw-pending-text").innerHTML = "This radio is <strong>" + esc(r.name || "?") + "</strong> (" + esc((r.public_key || "").slice(0, 8)) + "…" +
          (r.model ? ", " + esc(r.model) : "") + "), not the saved node <strong>" + esc(pr.name || "?") + "</strong> (" + esc((pr.public_key || "").slice(0, 8)) + "…). " +
          (pend.why_not ? "It cannot be adopted: " + esc(pend.why_not) + "." : pend.mode === "auto" ? "Automatic adoption did not take (" + (pend.attempts || 0) + " attempts)." : pend.mode === "off" ? "Adoption is switched off in Settings." : "Nothing has been written to it.") +
          " Adverts are held until you decide: adopt it to make it " + esc(pr.name || "the bot") + ", or start a new profile to keep its own identity.";
      }
      var la = p.last_adoption;
      $("hw-profile").innerHTML = !prof ? "No profile saved yet" + (p.note ? " (" + esc(p.note) + ")" : "") + "." :
        "Profile: <strong>" + esc(prof.name || "?") + "</strong> " + esc((prof.public_key || "").slice(0, 8)) + "… · " +
        (prof.has_key ? "identity key saved" : '<span class="badge badge-danger">no identity key</span>') + " · " + (prof.contacts || 0) + " contacts · saved " + agoAt(prof.saved_at) +
        (prof.radio ? " · " + prof.radio.freq_mhz + " MHz / " + prof.radio.bw_khz + " kHz / SF" + prof.radio.sf + " / CR" + prof.radio.cr + " / " + prof.tx_power + " dBm" : "") +
        (p.matches === false ? "" : "") + (p.note ? "<br><span class='text-muted'>" + esc(p.note) + "</span>" : "") +
        (la ? "<br>Last adoption " + agoAt(la.t) + ": " + (la.ok ? "ok" : "failed") + (la.note ? " (" + esc(la.note) + ")" : "") + (la.steps && la.steps.length ? " · " + esc(la.steps.join("; ")) : "") : "") +
        " · mode <strong>" + esc(p.mode || "auto") + "</strong>";
    },
    testTx: function (btn) {
      var out = $("health-test-result"), self = this;
      btn.disabled = true; out.textContent = "Sent; waiting for an echo…";
      api("/api/radio/testtx", { method: "POST", body: {} }).then(function (d) {
        out.textContent = d.heard ? "Echo heard after " + d.echo_ms + " ms via " + (d.via || "?") + (d.snr != null ? " (SNR " + d.snr + ")" : "") + (d.attempts > 1 ? ", on attempt " + d.attempts : "")
          : "No echo" + (d.attempts > 1 ? " after " + d.attempts + " attempts" : "") + (d.skipped ? " (" + d.skipped + ")" : "") + ": nothing repeated this packet.";
        Portal.ui.toast(d.heard ? "Link test: echo heard" : "Link test: no echo", !!d.heard); self.loadHealth();
      }).catch(function (e) { out.textContent = e.message; Portal.ui.toast(e.message, false); }).finally(function () { btn.disabled = false; });
    },
    saveProfile: function (force) {
      if (force && !confirm("Start a new profile from this radio? The saved identity of the old node is discarded; phones will see a new bot.")) return;
      var out = $("hw-action-result"), self = this;
      api("/api/radio/profile/save", { method: "POST", body: { force: !!force } }).then(function (d) {
        out.textContent = "Profile saved: " + (d.profile.name || "?") + ", " + d.profile.contacts + " contacts" + (d.profile.has_key ? "" : " (no identity key: the firmware refused the export)");
        Portal.ui.toast("Profile saved"); self.loadHealth(); self.load();
      }).catch(function (e) { out.textContent = e.message; Portal.ui.toast(e.message, false); });
    },
    adopt: function (btn) {
      if (!confirm("Write the saved identity onto this radio and reboot it? Make sure the old radio is powered off: two nodes with one key confuse the mesh.")) return;
      var out = $("hw-action-result"), self = this;
      btn.disabled = true; out.textContent = "Writing the profile, rebooting the node, reconnecting… about 20 seconds.";
      api("/api/radio/profile/adopt", { method: "POST", body: {} }).then(function (d) {
        var la = (d.profile || {}).last_adoption || {};
        out.textContent = (la.ok ? "Adopted: " : "Adoption did not take: ") + (la.note || (d.steps || []).join("; ")) + (d.connected ? "" : " · radio not reconnected: " + (d.error || ""));
        Portal.ui.toast(la.ok ? "Radio adopted" : "Adoption failed", !!la.ok); self.load(); self.loadHealth(); self.loadContacts(); Portal.poll.tick();
      }).catch(function (e) { out.textContent = e.message; Portal.ui.toast(e.message, false); }).finally(function () { btn.disabled = false; });
    },

    load: function () {
      var self = this;
      api("/api/radio").then(function (d) { self._state = d; self.render(d); })
        .catch(function (e) { Portal.ui.toast("Radio: " + e.message, false); });
    },

    render: function (d) {
      var info = d.info || {}, conn = d.connected, dev = d.device || {};
      $("radio-offline-card").hidden = !!conn;
      $("radio-offline-reason").textContent = conn ? "" : (d.error || "") + " (" + d.serial_port + " @ " + d.serial_baud + ")";
      $("radio-stats").innerHTML =
        tile("Link", conn ? "up" : "down", d.serial_port, conn ? "ok" : "bad") +
        tile("Node", conn ? esc(info.name || "?") : "–", (dev.model ? dev.model + " · " : "") + (dev.ver || "")) +
        tile("Frequency", info.radio_freq != null ? info.radio_freq + " MHz" : "–",
          info.radio_bw != null ? "BW " + info.radio_bw + " kHz · SF" + info.radio_sf + " · CR" + info.radio_cr : "") +
        tile("TX power", info.tx_power != null ? info.tx_power + " dBm" : "–", info.max_tx_power != null ? "max " + info.max_tx_power + " dBm" : "") +
        tile("Battery", info.battery_mv ? (info.battery_mv / 1000).toFixed(2) + " V" : "–", d.tx_enabled ? "transmit on" : "receive only");

      setVal("radio-name", info.name); setVal("radio-lat", info.adv_lat); setVal("radio-lon", info.adv_lon);
      setVal("radio-freq", info.radio_freq); setVal("radio-bw", info.radio_bw); setVal("radio-sf", info.radio_sf); setVal("radio-cr", info.radio_cr);
      setVal("radio-txpower", info.tx_power);
      $("radio-pubkey").textContent = info.public_key ? "public key " + info.public_key : "";
      var badge = $("radio-tx-badge");
      badge.textContent = d.tx_enabled ? "ON" : "OFF";
      badge.className = "badge " + (d.tx_enabled ? "badge-danger" : "badge-success");
      $("radio-tx-toggle").textContent = d.tx_enabled ? "Disable transmit" : "Enable transmit";

      var sel = $("radio-preset"), presets = d.presets || {}, match = "";
      Object.keys(presets).forEach(function (k) {
        var p = presets[k];
        if (Math.abs((info.radio_freq || 0) - p.freq_mhz) < 0.001 && Math.abs((info.radio_bw || 0) - p.bw_khz) < 0.1 && info.radio_sf === p.sf && info.radio_cr === p.cr) match = k;
      });
      if (document.activeElement !== sel) {
        sel.innerHTML = '<option value="">custom</option>' + Object.keys(presets).map(function (k) {
          var p = presets[k];
          return '<option value="' + k + '">' + esc(p.label) + " · " + p.freq_mhz + " MHz / " + p.bw_khz + " kHz / SF" + p.sf + " / CR" + p.cr + "</option>";
        }).join("");
        sel.value = match;
      }

      var cfg = d.configured_channels || {};
      setVal("ch-text", cfg.text); setVal("ch-data", cfg.data);
      var slots = (info.channels) || {};
      $("ch-status").textContent = conn ? ["text", "data"].map(function (r) {
        return r + ": " + (slots[r] != null ? "slot " + slots[r] : (cfg[r] ? "not on the node" : "off"));
      }).join(" · ") : "Radio not connected: names apply when it connects";

      var tb = $("radio-channels");
      if (!conn) { tb.innerHTML = '<tr><td colspan="5" class="text-muted">Radio not connected</td></tr>'; }
      else {
        var rows = [];
        for (var i = 0; i < 8; i++) {
          var ch = (d.channels || []).filter(function (c) { return c.idx === i; })[0] || { idx: i, name: "", role: null };
          var roles = ch.roles || (ch.role ? [ch.role] : []);
          var roleBadge = roles.map(function (r) { return '<span class="badge badge-success">' + esc(r) + "</span>"; }).join(" ");
          if (!ch.role && ch.name && [cfg.text, cfg.data].indexOf(ch.name) !== -1) roleBadge = '<span class="badge badge-warning">configured, not resolved</span>';
          rows.push("<tr><td>" + i + (i === 0 ? ' <span class="text-muted">public</span>' : "") + "</td>" +
            "<td>" + (i === 0 ? esc(ch.name || "Public") : '<input class="input" style="max-width:260px" id="radio-ch-' + i + '" value="' + esc(ch.name || "") + '" placeholder="(empty slot)">') + "</td>" +
            "<td>" + roleBadge + "</td>" +
            '<td class="text-mono text-small text-muted">' + (ch.secret ? ch.secret.slice(0, 8) + "…" : "") + "</td>" +
            "<td>" + (i === 0 ? "" : '<button class="btn btn-mini" onclick="Portal.radio.saveSlot(' + i + ')">Save</button> ' +
              (ch.name && !ch.role ? '<button class="btn btn-mini" onclick="Portal.radio.clearSlot(' + i + ')">Clear</button>' : "")) + "</td></tr>");
        }
        tb.innerHTML = rows.join("");
      }
      this.renderHousekeeping(d.housekeeping);
    },

    renderHousekeeping: function (h) {
      if (!h) return;
      var cb = $("env-MCW_CONTACT_HOUSEKEEPING");
      if (document.activeElement !== cb) cb.checked = !!h.enabled;
      setVal("env-MCW_CONTACT_KEEP_FREE", h.keep_free);
      $("contacts-help").textContent = "The node holds " + h.slots + " contacts and can only DM someone it has stored. With housekeeping on, the node stores companions only; " +
        "repeaters, rooms and sensors already stored are removed after every refresh, and when people alone come within " + h.keep_free + " of the limit the ones heard longest ago go. The admin and other WX- bots are never removed.";
      $("contacts-sub").textContent = h.last && h.last.t ? "Housekeeping " + agoAt(h.last.t) + " ago: " + h.last.note :
        (h.enabled ? "Housekeeping on, not run yet" : "Housekeeping off: the firmware keeps whatever it hears");
    },

    _act: function (promise, okMsg) {
      var self = this, out = $("radio-action-result");
      return promise.then(function (d) {
        Portal.ui.toast(okMsg || "Done");
        out.textContent = (d && d.note) || "";
        self.load(); Portal.poll.tick();
      }).catch(function (e) { Portal.ui.toast(e.message, false); out.textContent = e.message; });
    },
    saveName: function () { this._act(api("/api/radio/name", { method: "POST", body: { name: $("radio-name").value } }), "Name saved"); },
    saveCoords: function () {
      this._act(api("/api/radio/coords", { method: "POST", body: { lat: parseFloat($("radio-lat").value), lon: parseFloat($("radio-lon").value) } }), "Location saved");
    },
    pickPreset: function () {
      var p = ((this._state || {}).presets || {})[$("radio-preset").value];
      if (!p) return;
      $("radio-freq").value = p.freq_mhz; $("radio-bw").value = p.bw_khz; $("radio-sf").value = p.sf; $("radio-cr").value = p.cr;
    },
    saveParams: function () {
      var body = { freq_mhz: parseFloat($("radio-freq").value), bw_khz: parseFloat($("radio-bw").value), sf: parseInt($("radio-sf").value, 10), cr: parseInt($("radio-cr").value, 10) };
      if (!confirm("Set the radio to " + body.freq_mhz + " MHz / " + body.bw_khz + " kHz / SF" + body.sf + " / CR" + body.cr + "? Every node on the mesh must use the same values.")) return;
      this._act(api("/api/radio/params", { method: "POST", body: body }), "Radio parameters applied");
    },
    saveTxPower: function () { this._act(api("/api/radio/txpower", { method: "POST", body: { dbm: parseInt($("radio-txpower").value, 10) } }), "TX power set"); },
    toggleTx: function () {
      var on = !(this._state && this._state.tx_enabled);
      if (on && !confirm("Enable transmit? The bot will send an advert now, then DM replies and scheduled broadcasts.")) return;
      this._act(api("/api/radio/tx", { method: "POST", body: { enabled: on } }), on ? "Transmit enabled" : "Transmit disabled");
    },
    advert: function () { this._act(api("/api/radio/advert", { method: "POST", body: {} }), "Advert requested"); },
    reboot: function () { if (confirm("Reboot the radio node? The bot reconnects on its own.")) this._act(api("/api/radio/reboot", { method: "POST", body: {} }), "Rebooting"); },
    reconnect: function (btn) {
      btn.disabled = true;
      var self = this;
      api("/api/radio/reconnect", { method: "POST", body: {} }).then(function (d) {
        Portal.ui.toast(d.connected ? "Radio connected" : "Still not connected: " + (d.error || ""), d.connected); self.load(); Portal.poll.tick();
      }).catch(function (e) { Portal.ui.toast(e.message, false); }).finally(function () { btn.disabled = false; });
    },
    saveRoles: function (btn) {
      var st = $("ch-status"), self = this;
      btn.disabled = true; st.textContent = "Saving…";
      api("/api/settings/channels", { method: "POST", body: { text_channel: $("ch-text").value.trim(), data_channel: $("ch-data").value.trim() } })
        .then(function (d) { Portal.ui.toast(d.note || "Saved"); self.load(); })
        .catch(function (e) { st.textContent = e.message; Portal.ui.toast(e.message, false); })
        .finally(function () { btn.disabled = false; });
    },
    saveSlot: function (i) {
      var name = $("radio-ch-" + i).value.trim();
      if (!name) return this.clearSlot(i);
      this._act(api("/api/radio/channel", { method: "POST", body: { idx: i, name: name } }), "Slot " + i + " saved");
    },
    clearSlot: function (i) { if (confirm("Clear channel slot " + i + "?")) this._act(api("/api/radio/channel/" + i, { method: "DELETE" }), "Slot " + i + " cleared"); },
    saveHousekeeping: function () {
      var on = $("env-MCW_CONTACT_HOUSEKEEPING").checked, keep = $("env-MCW_CONTACT_KEEP_FREE").value.trim();
      var body = { MCW_CONTACT_HOUSEKEEPING: on ? "true" : "false" };
      if (keep !== "") body.MCW_CONTACT_KEEP_FREE = keep;
      this._act(api("/api/settings/env", { method: "POST", body: body }), on ? "Housekeeping on" : "Housekeeping off");
    },
    housekeepNow: function () {
      var self = this;
      api("/api/radio/housekeep", { method: "POST", body: {} }).then(function (d) { Portal.ui.toast(d.note); self.load(); self.loadContacts(); })
        .catch(function (e) { Portal.ui.toast(e.message, false); });
    },
    loadContacts: function () {
      api("/api/radio/contacts").then(function (d) {
        var tb = $("radio-contacts");
        if (!d.contacts.length) { tb.innerHTML = '<tr><td colspan="5" class="text-muted">No contacts</td></tr>'; return; }
        tb.innerHTML = d.contacts.map(function (c) {
          return "<tr><td>" + esc(c.name || "?") + "</td><td>" + ({ 1: "companion", 2: "repeater", 3: "room", 4: "sensor" }[c.type] || c.type || "") + "</td>" +
            "<td>" + (c.heard ? agoAt(c.heard) + " ago" : "–") + "</td>" +
            "<td>" + (c.out_path_len != null && c.out_path_len >= 0 ? c.out_path_len + " hops" : "flood") + "</td>" +
            '<td class="text-mono text-small text-muted">' + (c.public_key || "").slice(0, 12) + "</td></tr>";
        }).join("");
      }).catch(function (e) { $("radio-contacts").innerHTML = '<tr><td colspan="5" class="text-muted">' + esc(e.message) + "</td></tr>"; });
    },
  },

  // -- Satellite and feed ------------------------------------------------------------

  satellite: {
    _timer: null,
    onEnter: function () {
      var s = this;
      this.load(); this.loadHistory();
      if (this._timer) clearInterval(this._timer);
      this._timer = setInterval(function () { s.load(); s.loadHistory(); }, 10000);
      Portal.products.onEnter();
    },
    onLeave: function () { if (this._timer) clearInterval(this._timer); this._timer = null; },

    load: function () {
      api("/api/sdr").then(function (d) {
        var r = d.receiver || {}, st = r.stats || {}, feed = d.feed || {}, err = r._error;
        $("sat-dashboard-link").href = d.dashboard_url;
        $("sat-stats").innerHTML =
          tile("Lock", err ? "?" : (st.locked ? "locked" : "no lock"), err ? "dashboard unreachable" : (st.lock_since ? "since " + agoAt(st.lock_since) + " ago" : ""), err ? "bad" : st.locked ? "ok" : "warn") +
          tile("Viterbi", st.vit_avg != null ? st.vit_avg : "–", "errors per frame, lower is better") +
          tile("Drops", st.drops != null ? st.drops : "–", "last interval", st.drops ? "warn" : "") +
          tile("Feed", feed.products_last_hour != null ? feed.products_last_hour : "–", "products in the last hour") +
          tile("Newest file", ago(feed.newest_age_s) + " old", feed.source === "sdr" ? "from the dish" : "internet feed");
        $("sat-signal-sub").textContent = err ? err :
          ("mode " + (r.mode || "?") + " · gain " + (st.gain != null ? st.gain.toFixed(1) : "?") + " · freq offset " + (st.freq != null ? Math.round(st.freq) + " Hz" : "?"));
        $("sat-mode-point").className = "btn" + (r.mode === "point" ? " btn-primary" : "");
        $("sat-mode-receive").className = "btn" + (r.mode === "receive" ? " btn-primary" : "");
        var tot = st.totals || {};
        $("sat-totals").innerHTML =
          tile("Packets", tot.packets != null ? tot.packets.toLocaleString() : "–", "since goesrecv start") +
          tile("Dropped", tot.drops != null ? tot.drops.toLocaleString() : "–", tot.packets ? (tot.drops * 100 / tot.packets).toFixed(2) + "%" : "") +
          tile("RS corrected", tot.rs_errors != null ? tot.rs_errors.toLocaleString() : "–", "bytes");
        $("sat-feed-sub").textContent = feed.products_total + " products in the store · " + feed.warnings_last_hour + " warning-class in the last hour" + (feed.directory ? " · " + feed.directory : "");
        $("sat-feed-types").innerHTML = (feed.top_types_last_hour || []).map(function (t) {
          return '<tr><td class="text-mono">' + esc(t[0]) + "</td><td>" + t[1] + "</td></tr>";
        }).join("") || '<tr><td colspan="2" class="text-muted">nothing in the last hour</td></tr>';
        var svc = (r.status || {}).services || {}, stt = r.status || {};
        $("sat-services").innerHTML =
          tile("goesrecv", svc.goesrecv || "?", stt.goesrecv_up_s ? "up " + ago(stt.goesrecv_up_s) : "", svc.goesrecv === "active" ? "ok" : "warn") +
          tile("goesproc", svc.goesproc || "?", stt.counts ? (stt.counts.emwin_today || 0) + " EMWIN files today" : "", svc.goesproc === "active" ? "ok" : "warn");
      }).catch(function (e) { Portal.ui.toast("Satellite: " + e.message, false); });
    },

    loadHistory: function () {
      api("/api/sdr/history").then(function (d) {
        var h = d.history; if (!Array.isArray(h) || !h.length) return;
        var c = $("sat-chart"), ctx = c.getContext("2d");
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
      if (mode === "point" && !confirm("Switch the receiver to pointing mode? EMWIN reception stops until it is back in receive mode.")) return;
      var self = this;
      api("/api/sdr/mode", { method: "POST", body: { mode: mode } }).then(function () { Portal.ui.toast("Receiver in " + mode + " mode"); self.load(); Portal.poll.tick(); })
        .catch(function (e) { Portal.ui.toast(e.message, false); });
    },
  },

  // -- Products browser (Satellite) ----------------------------------------------------

  products: {
    _filtersLoaded: false, _loadTimer: null,
    onEnter: function () { if (!this._filtersLoaded) this.loadFilters(); this.load(); },
    loadFilters: function () {
      var self = this;
      api("/api/products/filters").then(function (d) {
        self._fill("filter-type", d.types); self._fill("filter-office", d.offices); self._fill("filter-state", d.states);
        self._filtersLoaded = true;
      }).catch(function () {});
    },
    _fill: function (id, items) {
      var sel = $(id), current = sel.value;
      sel.innerHTML = '<option value="">' + sel.options[0].textContent + "</option>" + items.map(function (i) { return '<option value="' + esc(i) + '">' + esc(i) + "</option>"; }).join("");
      sel.value = current;
    },
    debouncedLoad: function () { clearTimeout(this._loadTimer); var s = this; this._loadTimer = setTimeout(function () { s.load(); }, 300); },
    load: function () {
      var params = new URLSearchParams({ type: $("filter-type").value, office: $("filter-office").value, state: $("filter-state").value, q: $("filter-q").value });
      var tbody = $("products-tbody");
      api("/api/products?" + params).then(function (d) {
        if (!d.products.length) { tbody.innerHTML = '<tr><td colspan="5" class="text-muted">No products match.</td></tr>'; $("products-summary").textContent = "No products match the filters"; return; }
        tbody.innerHTML = d.products.map(function (p) {
          return '<tr onclick="Portal.products.open(' + JSON.stringify(p.filename).replace(/"/g, "&quot;") + ')">' +
            '<td class="text-mono"><strong>' + esc(p.product_type) + "</strong></td><td class=\"text-mono\">" + esc(p.office) + '</td><td class="text-mono">' + esc(p.state) + "</td>" +
            '<td class="text-muted">' + esc(new Date(p.timestamp).toISOString().slice(0, 16).replace("T", " ")) + "</td><td>" + esc(p.preview) + "</td></tr>";
        }).join("");
        $("products-summary").textContent = "Newest " + d.products.length + (d.products.length >= d.limit ? " (limit " + d.limit + "; narrow the filters to see others)" : "");
      }).catch(function (e) { tbody.innerHTML = '<tr><td colspan="5">' + esc(e.message) + "</td></tr>"; });
    },
    open: function (filename) {
      api("/api/products/" + encodeURIComponent(filename)).then(function (d) {
        Portal.ui.openModal('<div class="card-header"><div class="card-title">' + esc(d.emwin_id) + " · " + esc(d.product_type) + '</div><button class="btn" onclick="Portal.ui.closeModal()">Close</button></div>' +
          '<pre class="text-mono pre" style="background:var(--color-bg);padding:12px;border-radius:6px;max-height:60vh;overflow:auto">' + esc(d.raw_text) + "</pre>", true);
      }).catch(function (e) { Portal.ui.toast(e.message, false); });
    },
  },

  // -- System: logs | settings ------------------------------------------------------------

  system: {
    _tab: null,
    onEnter: function () {},
    onLeave: function () { Portal.logs.stop(); this._tab = null; },
    showTab: function (tab) {
      if (tab !== "settings") tab = "logs";
      if (tab === this._tab) return;
      this._tab = tab;
      document.querySelectorAll("#system-tabs .sub-tab").forEach(function (a) { a.classList.toggle("active", a.dataset.tab === tab); });
      $("system-tab-logs").classList.toggle("active", tab === "logs");
      $("system-tab-settings").classList.toggle("active", tab === "settings");
      if (tab === "logs") Portal.logs.start(); else { Portal.logs.stop(); Portal.settings.load(); }
    },
  },

  logs: {
    _lines: [], _max: 3000, _cat: "all", _paused: false, _sse: null, _pending: 0, _lastId: 0,
    start: function () {
      var self = this;
      api("/api/logs?n=800").then(function (d) {
        self._lines = d.lines || [];
        self._lastId = self._lines.length ? self._lines[self._lines.length - 1].id : 0;
        self._counts(d.counts);
        self.render(true);
        self._connect();
      }).catch(function (e) { Portal.ui.toast("Logs: " + e.message, false); });
    },
    stop: function () { if (this._sse) { this._sse.close(); this._sse = null; } },
    _connect: function () {
      var self = this;
      if (this._sse) this._sse.close();
      this._sse = liveStream("/api/logs/stream", function (l) {
        if (l.id && l.id <= self._lastId) return;
        self._lastId = l.id || self._lastId;
        self._lines.push(l);
        if (self._lines.length > self._max) self._lines.splice(0, self._lines.length - self._max);
        if (self._paused) { self._pending++; self._status(); return; }
        if (self._show(l)) appendLine($("logs-body"), self._fmt(l), self._max);
      }, "logs-dot");
    },
    _counts: function (c) {
      if (!c) return;
      ["satellite", "radio", "bot"].forEach(function (k) { $("logs-n-" + k).textContent = c[k] || 0; });
      var p = $("logs-n-problems");
      p.hidden = !c.problems; p.textContent = c.problems || ""; p.className = "badge " + (c.problems ? "badge-warning" : "badge-muted");
    },
    _status: function () {
      $("logs-status").textContent = this._paused ? "paused" + (this._pending ? " · " + this._pending + " new" : "") : this._lines.length + " lines";
    },
    setCat: function (cat) {
      this._cat = cat;
      document.querySelectorAll("#logs-cats .sub-tab").forEach(function (b) { b.classList.toggle("active", b.dataset.cat === cat); });
      this.render(true);
    },
    togglePause: function () {
      this._paused = !this._paused;
      $("logs-pause").textContent = this._paused ? "Resume" : "Pause";
      if (!this._paused) { this._pending = 0; this.render(true); }
      this._status();
    },
    clear: function () { this._lines = []; this.render(true); },
    _show: function (l) {
      if (this._cat !== "all" && l.cat !== this._cat) return false;
      var lv = $("logs-level").value, order = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"];
      if (lv && order.indexOf(l.level) < order.indexOf(lv)) return false;
      var q = $("logs-q").value.trim().toLowerCase();
      return !q || l.msg.toLowerCase().indexOf(q) !== -1;
    },
    _fmt: function (l) {
      var lc = l.level === "ERROR" || l.level === "CRITICAL" ? "badge-danger" : l.level === "WARNING" ? "badge-warning" : "badge-muted";
      var cc = { satellite: "#06b6d4", radio: "#a855f7", bot: "#9ca3af" }[l.cat] || "#9ca3af";
      return '<div class="console-line"><span class="text-muted">' + hhmmss(l.t) + "</span> " +
        '<span style="color:' + cc + ';display:inline-block;min-width:64px">' + esc(l.cat) + "</span>" +
        '<span class="badge ' + lc + '">' + esc(l.level.slice(0, 4)) + "</span> " + esc(l.msg) + "</div>";
    },
    render: function (scroll) {
      var self = this, el = $("logs-body"), html = [];
      this._lines.forEach(function (l) { if (self._show(l)) html.push(self._fmt(l)); });
      el.innerHTML = html.join("") || '<div class="text-muted">nothing matches</div>';
      if (scroll !== false) el.scrollTop = el.scrollHeight;
      this._status();
    },
  },

  settings: {
    _orig: {},
    _groups: {
      coverage: ["MCW_HOME_CITIES", "MCW_HOME_RADIUS_KM", "MCW_HOME_STATES", "MCW_HOME_WFOS"],
      host: ["MCW_SERIAL_PORT", "MCW_SERIAL_BAUD", "MCW_EMWIN_SOURCE", "MCW_SDR_EMWIN_DIR", "MCW_SDR_POLL_INTERVAL", "MCW_SDR_DASHBOARD_URL", "MCW_TIMEZONE", "MCW_LOG_LEVEL",
             "MCW_SCOPE_URL", "MCW_SCOPE_MODE", "MCW_SCOPE_MIN_OBSERVERS", "MCW_RADIO_ADOPT", "MCW_RADIO_RX_SILENT_MIN"],
    },
    load: function () {
      var self = this;
      api("/api/system").then(function (d) {
        var b = d.bot || {}, h = d.host || {}, s = d.settings || {}, cov = d.coverage || {};
        $("sys-tiles").innerHTML =
          tile("Bot up", ago(b.uptime_s), (h.hostname || "") + (b.git ? " · commit " + b.git : "")) +
          tile("Host up", ago(h.uptime_s), h.load ? "load " + h.load.join(" / ") : "") +
          tile("Memory", h.mem_available_mb != null ? h.mem_available_mb + " MB" : "–", "free of " + h.mem_total_mb + " MB") +
          tile("Disk", h.disk_free_gb != null ? h.disk_free_gb + " GB" : "–", "free · " + h.disk_used_pct + "% used", h.disk_used_pct > 85 ? "bad" : h.disk_used_pct > 70 ? "warn" : "") +
          tile("CPU temp", h.temp_c != null ? h.temp_c + "°C" : "–", b.products + " products in store", h.temp_c > 75 ? "warn" : "");
        self._groups.coverage.concat(self._groups.host).forEach(function (k) {
          var v = s[k.slice(4).toLowerCase()];
          self._orig[k] = v == null ? "" : String(v);
          setVal("env-" + k, v);
        });
        $("coverage-summary").innerHTML = "<b>" + esc(cov.summary || "") + "</b>";
      }).catch(function (e) { Portal.ui.toast(e.message, false); });
    },
    save: function (btn, group) {
      var self = this, body = {}, st = $(group === "coverage" ? "coverage-status" : "host-status");
      this._groups[group].forEach(function (k) { var v = $("env-" + k).value.trim(); if (v !== self._orig[k]) body[k] = v; });
      if (!Object.keys(body).length) { st.textContent = "Nothing changed"; return; }
      btn.disabled = true; st.textContent = "Saving…";
      api("/api/settings/env", { method: "POST", body: body }).then(function (d) {
        st.textContent = d.note;
        Portal.ui.toast(d.restart_needed && d.restart_needed.length ? "Saved; restart the bot to apply " + d.restart_needed.join(", ") : "Applied");
        self.load(); Portal.poll.tick();
      }).catch(function (e) { st.textContent = e.message; Portal.ui.toast(e.message, false); })
        .finally(function () { btn.disabled = false; });
    },
    restart: function () {
      if (!confirm("Restart the bot now? It is back in about 90 seconds; the radio link drops briefly.")) return;
      api("/api/system/restart", { method: "POST", body: {} }).then(function (d) { Portal.ui.toast(d.note); })
        .catch(function (e) { Portal.ui.toast(e.message, false); });
    },
  },

  init: function () {
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") Portal.ui.closeModal(); });
    this.poll.start();
    this.router.init();
  },
};

document.addEventListener("DOMContentLoaded", function () { Portal.init(); });
