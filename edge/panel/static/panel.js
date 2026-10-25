// Live refresh and the mark-normal swap are done by htmx (see the hx-* attributes in the
// templates). This file guards the polling, shows times in the viewer's local time zone (the
// engine sends UTC), gives the mark-normal button its in-flight state, shows how long the
// engine has been unreachable, and makes decision navigation instant: a click swaps only the
// detail region, from a small client cache when it has the page (filled by the htmx `preload`
// extension on hover/focus/press, and by pages already visited) or behind a skeleton when not.
// Without JS the links are plain links.
(function () {
  // Every htmx request carries the session's CSRF token (the server also checks Origin).
  document.addEventListener("htmx:configRequest", function (e) {
    var meta = document.querySelector('meta[name="csrf-token"]');
    if (meta && meta.content) e.detail.headers["X-CSRF-Token"] = meta.content;
  });
  function pad(n) { return String(n).padStart(2, "0"); }
  function localize(root) {
    root.querySelectorAll("time.ts[datetime]").forEach(function (el) {
      var d = new Date(el.getAttribute("datetime"));
      if (isNaN(d)) return;
      var hm = pad(d.getHours()) + ":" + pad(d.getMinutes());
      var text = el.hasAttribute("data-hm") ? hm : hm + ":" + pad(d.getSeconds());
      // "hoy" only when the local calendar day is today, decided here in the browser.
      if (el.hasAttribute("data-day") && d.toDateString() === new Date().toDateString()) text = "hoy " + text;
      el.textContent = text;
    });
    root.querySelectorAll("[data-utc-hours]").forEach(function (el) {
      var hours = el.getAttribute("data-utc-hours").split(",").map(function (h) {
        var d = new Date();
        d.setUTCHours(Number(h), 0, 0, 0);
        return pad(d.getHours()) + ":00";
      });
      el.textContent = hours.length === 2 ? hours[0] + " – " + hours[1] : hours.join(", ");
    });
  }
  // The one-time API key and the snippet: copy buttons are hidden until JS can make them work (the text is
  // selectable without it). `data-copy` holds the text; `data-copy-from` names the element whose text is copied.
  function wireCopy() {
    document.querySelectorAll("[data-copy], [data-copy-from]").forEach(function (btn) {
      if (!navigator.clipboard) return;
      btn.hidden = false;
      btn.addEventListener("click", function () {
        var from = btn.getAttribute("data-copy-from");
        var text = from ? document.getElementById(from).textContent : btn.getAttribute("data-copy");
        navigator.clipboard.writeText(text).then(function () {
          var old = btn.textContent;
          btn.textContent = "Copiada";
          setTimeout(function () { btn.textContent = old; }, 2000);
        }, function () { btn.textContent = "No se pudo copiar"; });
      });
    });
  }
  wireCopy();
  // Tabs of the SDK snippet (WAI-ARIA tabs: click, arrows, Home/End). Without JS the Python tab stays open.
  function wireTabs() {
    document.querySelectorAll('[role="tablist"]').forEach(function (list) {
      var tabs = Array.prototype.slice.call(list.querySelectorAll('[role="tab"]'));
      function select(tab) {
        tabs.forEach(function (t) {
          var on = t === tab;
          t.setAttribute("aria-selected", on ? "true" : "false");
          t.tabIndex = on ? 0 : -1;
          document.getElementById(t.getAttribute("aria-controls")).hidden = !on;
        });
        tab.focus();
      }
      tabs.forEach(function (tab, i) {
        tab.addEventListener("click", function () { select(tab); });
        tab.addEventListener("keydown", function (e) {
          var j = { ArrowRight: (i + 1) % tabs.length, ArrowLeft: (i - 1 + tabs.length) % tabs.length, Home: 0, End: tabs.length - 1 }[e.key];
          if (j === undefined) return;
          e.preventDefault();
          select(tabs[j]);
        });
      });
    });
  }
  wireTabs();
  var pointerDown = false;
  document.addEventListener("pointerdown", function () { pointerDown = true; }, true);
  document.addEventListener("pointerup", function () { setTimeout(function () { pointerDown = false; }, 0); }, true);
  // Polling guard (used in hx-trigger): never swap while the viewer is pressing,
  // has keyboard focus in a form, or the tab is hidden.
  window.refreshAllowed = function () {
    var active = document.activeElement;
    return !(navigating || pointerDown || document.hidden || document.querySelector(".chart .bar:hover") || (active && active.closest && active.closest("form")));
  };
  // New rows: remember the row ids on screen; after a swap, rows with an unseen id get
  // `is-new` (CSS animates them). The first page load only records, nothing animates.
  // Keyed by data-row, not id: htmx copies the old attributes onto same-id elements while it
  // settles, which would carry `is-new` into the next poll.
  var ROWS = "[data-row]";
  var seen = new Set();
  function markNew(animate) {
    document.querySelectorAll(ROWS).forEach(function (el) {
      if (seen.has(el.dataset.row)) return;
      seen.add(el.dataset.row);
      if (animate) el.classList.add("is-new");
    });
  }
  document.addEventListener("htmx:afterSwap", function () {
    localize(document); renderConn(); markNew(true);
    // A poll that changed the detail of this page makes its cached copy stale.
    var sig = detailSig();
    if (lastSig !== null && sig !== lastSig) cache.delete(currentKey);
    lastSig = sig;
  });
  localize(document);
  markNew(false);

  // ---- Instant navigation ----
  // Pages: resumen, actividad, usuarios, rutas (data-view of #live). Inside Actividad a click swaps only
  // the detail region (the feed stays live); any other change swaps the whole #live region.
  var FRESH_MS = 5000;      // a cached page is reused for clicks for this long
  var HISTORY_MS = 60000;   // back/forward accept older copies: the next poll refreshes them within 2 s
  var MAX_ENTRIES = 30;
  var ROW_LINKS = "#live nav a[data-row][href]";
  var SECTION_LINKS = "#side nav a[href]:not([data-full])";  // data-full: a page outside the live shell (Proyectos)
  var cache = new Map();    // "/path?query" -> record, or {promise} while a prefetch is in flight
  var generation = 0;       // bumped to drop everything (mark-normal): older answers are never stored
  var navToken = 0, navigating = false, navAbort = null;
  var currentKey = keyOf(location.href);
  var lastSig = null;

  function keyOf(url) { var u = new URL(url, location.href); return u.pathname + u.search; }
  function viewOf(key) {
    var p = key.split("?")[0];
    return p === "/" ? "resumen" : p === "/usuarios" ? "usuarios" : p === "/rutas" ? "rutas" : "actividad";
  }
  function detail() { return document.getElementById("detalle"); }
  function live() { return document.getElementById("live"); }
  function detailSig() { var d = detail(), l = live(); return d ? d.innerHTML : l ? l.innerHTML : ""; }
  lastSig = detailSig();
  function selectedRows(root) {
    var rows = [];
    root.querySelectorAll("[data-row][aria-current]").forEach(function (el) { rows.push(el.dataset.row); });
    return rows;
  }
  function record(l, d, rows, gen) {
    return { t: Date.now(), gen: gen, view: l.dataset.view, live: l.outerHTML.replace(/\bis-new\b/g, ""),
             detail: d ? d.innerHTML : null, current: rows, hxGet: l.getAttribute("hx-get") };
  }
  function snapshot() {
    var l = live(), d = detail();
    if (!l || l.getAttribute("aria-busy") === "true" || (d && d.getAttribute("aria-busy") === "true")) return null;
    return record(l, d, selectedRows(document), generation);
  }
  function parse(html, gen) {
    var doc = new DOMParser().parseFromString(html, "text/html");
    var l = doc.getElementById("live");
    return l ? record(l, doc.getElementById("detalle"), selectedRows(doc), gen) : null;
  }
  function store(key, rec) {
    if (!rec || rec.gen !== generation) return;
    cache.delete(key);
    cache.set(key, rec);
    while (cache.size > MAX_ENTRIES) cache.delete(cache.keys().next().value);
  }
  function lookup(key, ttl) {
    var e = cache.get(key);
    if (!e || e.gen !== generation) return null;
    return e.promise || Date.now() - e.t <= ttl ? e : null;
  }
  function clearCache() { generation++; cache.clear(); }

  // The preload extension sends the real request through htmx but swaps nothing; keep its answer.
  function tapPreload(e) {
    var xhr = e.detail.xhr, gen = generation;
    var key = keyOf(e.detail.pathInfo.finalRequestPath || e.detail.pathInfo.requestPath);
    var have = lookup(key, FRESH_MS);
    var entry = { gen: gen, t: Date.now() };
    entry.promise = new Promise(function (resolve, reject) {
      xhr.addEventListener("load", function () {
        var rec = xhr.status === 200 ? parse(xhr.responseText, gen) : null;
        rec ? resolve(rec) : reject();
      });
      ["error", "abort", "timeout"].forEach(function (n) { xhr.addEventListener(n, reject); });
    });
    entry.promise.then(function (rec) { store(key, rec); }, function () { if (cache.get(key) === entry) cache.delete(key); });
    if (!have) cache.set(key, entry);
  }
  function prefetch(e) {
    var a = e.target.closest && e.target.closest(ROW_LINKS + ", " + SECTION_LINKS);
    if (a) a.dispatchEvent(new Event("mouseenter"));  // the extension listens for it on each link
  }
  document.addEventListener("pointerdown", prefetch, true);
  document.addEventListener("focusin", prefetch);

  function setNav(view) {
    document.querySelectorAll("#side nav a[data-nav]").forEach(function (a) {
      if (a.dataset.nav === view) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
  }
  function showSkeleton() {
    var d = detail(), t = document.getElementById("detalle-skeleton");
    if (!d || !t) return;
    d.replaceChildren(t.content.cloneNode(true));
    d.setAttribute("aria-busy", "true");
  }
  function markBusy() {
    var l = live();
    if (l) { l.setAttribute("aria-busy", "true"); l.classList.add("is-loading"); }
  }
  function apply(rec) {
    var l = live(), d = detail();
    if (d && rec.detail !== null && l.dataset.view === rec.view) {
      d.innerHTML = rec.detail;
      d.removeAttribute("aria-busy");
      if (rec.hxGet) l.setAttribute("hx-get", rec.hxGet);  // the poll follows the selected row
      document.querySelectorAll(ROW_LINKS).forEach(function (el) {
        if (rec.current.indexOf(el.dataset.row) >= 0) el.setAttribute("aria-current", "true");
        else el.removeAttribute("aria-current");
      });
      htmx.process(l);  // wires the new mark-normal button and re-reads hx-get
      localize(d);
    } else {
      var t = document.createElement("template");
      t.innerHTML = rec.live;
      var nl = t.content.firstElementChild;
      l.replaceWith(nl);
      htmx.process(nl);
      localize(nl);
      markNew(false);
    }
    setNav(rec.view);
    lastSig = detailSig();
    navigating = false;
  }
  function fetchPage(key) {
    if (navAbort) navAbort.abort();
    navAbort = new AbortController();
    var gen = generation;
    return fetch(key, { credentials: "same-origin", signal: navAbort.signal }).then(function (r) {
      if (!r.ok) throw new Error(String(r.status));
      return r.text();
    }).then(function (html) {
      var rec = parse(html, gen);
      if (!rec) throw new Error("unexpected page");
      return rec;
    });
  }
  function navigate(href, opts) {
    var key = keyOf(href), token = ++navToken, view = viewOf(key);
    if (navAbort) { navAbort.abort(); navAbort = null; }
    store(currentKey, snapshot());  // so coming back is instant
    if (opts.push && key !== currentKey) history.pushState({ veridian: 1 }, "", href);
    currentKey = key;
    if (opts.link && opts.link.closest("#live")) {
      document.querySelectorAll(ROW_LINKS).forEach(function (el) { el.removeAttribute("aria-current"); });
      opts.link.setAttribute("aria-current", "true");
    }
    setNav(view);
    if (railBtn) syncRail();
    var hit = lookup(key, opts.ttl);
    if (hit && !hit.promise) return apply(hit);
    navigating = true;
    if (view === "actividad" && live().dataset.view === "actividad") showSkeleton(); else markBusy();
    (hit ? hit.promise.catch(function () { return fetchPage(key); }) : fetchPage(key)).then(function (rec) {
      if (token !== navToken) return;
      store(key, rec);
      apply(rec);
    }, function () {
      if (token === navToken && !(navAbort && navAbort.signal.aborted)) location.assign(href);
    });
  }
  // Capture phase: runs before htmx's boosted-link handler, which stays as the fallback.
  document.addEventListener("click", function (e) {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    var a = e.target.closest && e.target.closest(ROW_LINKS + ", " + SECTION_LINKS);
    if (!a || a.target) return;
    e.preventDefault();
    e.stopPropagation();
    closeMenu();
    navigate(a.getAttribute("href"), { push: true, ttl: FRESH_MS, link: a });
  }, true);
  window.addEventListener("popstate", function (e) {
    e.stopImmediatePropagation();
    navigate(location.pathname + location.search, { push: false, ttl: HISTORY_MS });
  }, true);

  // Menu button (narrow screens: the sidebar is a drawer from the left).
  var menuBtn = document.querySelector(".menu-btn"), side = document.getElementById("side");
  function setMenu(open, focusToggle) {
    if (!menuBtn || !side) return;
    if (open) side.setAttribute("data-open", ""); else side.removeAttribute("data-open");
    menuBtn.setAttribute("aria-expanded", open ? "true" : "false");
    menuBtn.setAttribute("aria-label", open ? "Cerrar menú" : "Abrir menú");
    if (!open && focusToggle) menuBtn.focus();
  }
  function closeMenu() { if (side && side.hasAttribute("data-open")) setMenu(false, true); }
  if (menuBtn && side) {
    menuBtn.addEventListener("click", function () { setMenu(!side.hasAttribute("data-open"), false); });
    var backdrop = document.querySelector(".backdrop");
    if (backdrop) backdrop.addEventListener("click", closeMenu);
    document.addEventListener("keydown", function (e) { if (e.key === "Escape") closeMenu(); });
  }

  // Rail toggle (wide screens). Without a saved choice Actividad starts collapsed and the rest expanded.
  var railBtn = document.querySelector(".rail-btn"), shell = document.querySelector(".shell");
  function railOn() {
    var r = shell.getAttribute("data-rail");
    return r ? r === "on" : !!document.querySelector('.nav-link[data-nav="actividad"][aria-current]');
  }
  function syncRail() {
    var label = railOn() ? "Expandir menú" : "Colapsar menú";
    railBtn.setAttribute("aria-label", label);
    railBtn.setAttribute("title", label);
    railBtn.setAttribute("aria-expanded", railOn() ? "false" : "true");
  }
  if (railBtn && shell) {
    railBtn.hidden = false;
    syncRail();
    railBtn.addEventListener("click", function () {
      var next = railOn() ? "off" : "on";
      shell.setAttribute("data-rail", next);
      try { localStorage.setItem("vrd-rail", next); } catch (e) {}
      syncRail();
    });
  }

  // The panel shows "today" in the viewer's time zone: tell the server the offset (minutes east of UTC).
  document.cookie = "vtz=" + (-new Date().getTimezoneOffset()) + "; path=/; max-age=31536000; samesite=lax";

  // In-flight state of the mark-normal button (htmx disables it via hx-disabled-elt).
  function isMark(el) { return el && el.matches && el.matches("button.btn[hx-post]"); }
  // A poll (or mark-normal) answer that was asked before a navigation began would swap the page we
  // left over the one we are on: drop it. (Aborting it instead makes htmx log an error.)
  document.addEventListener("htmx:beforeSwap", function (e) {
    var xhr = e.detail.xhr;
    if (xhr && xhr.navToken !== undefined && (xhr.navToken !== navToken || navigating)) e.detail.shouldSwap = false;
  });
  document.addEventListener("htmx:beforeRequest", function (e) {
    var cfg = e.detail.requestConfig;
    if (e.detail.xhr) e.detail.xhr.navToken = navToken;
    if (cfg && cfg.headers && cfg.headers["HX-Preloaded"] === "true") return tapPreload(e);
    var btn = e.detail.elt;
    if (!isMark(btn)) return;
    clearCache();
    btn.dataset.label = btn.textContent;
    btn.textContent = "Marcando…";
    btn.setAttribute("aria-busy", "true");
  });
  document.addEventListener("htmx:afterRequest", function (e) {
    var btn = e.detail.elt;
    if (isMark(btn)) clearCache();  // also drops prefetches that started while the request was in flight
    if (!isMark(btn) || !btn.isConnected) return;
    btn.textContent = btn.dataset.label || "Esto era normal";
    btn.removeAttribute("aria-busy");
  });

  // Engine connection: when it is down, say how long since the last good update.
  var lastOk = null;
  function ago(s) {
    return s < 60 ? s + " s" : s < 3600 ? Math.floor(s / 60) + " min" : s < 86400 ? Math.floor(s / 3600) + " h" : Math.floor(s / 86400) + " d";
  }
  function renderConn() {
    var conn = document.getElementById("conn");
    if (!conn) return;
    var label = conn.querySelector(".conn-label");
    if (!conn.classList.contains("down")) {
      lastOk = Date.now();
      // "último acceso hace N s" from the newest activity row the server reported.
      var ts = Date.parse(conn.getAttribute("data-last-access") || ""), span = conn.querySelector(".ago");
      if (span && !isNaN(ts)) span.textContent = ago(Math.max(0, Math.round((Date.now() - ts) / 1000)));
      return;
    }
    if (!label || lastOk === null) return;
    label.textContent = "Motor sin conexión · último dato hace " + ago(Math.max(0, Math.round((Date.now() - lastOk) / 1000)));
  }
  renderConn();
  setInterval(renderConn, 1000);
})();
