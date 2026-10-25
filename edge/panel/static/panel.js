// Live refresh, the mark-normal swap and navigation are done by htmx (see the
// hx-* attributes in the templates). This file only guards the polling and shows
// times in the viewer's local time zone (the engine sends UTC), gives the mark-normal
// button its in-flight state, and shows how long the engine has been unreachable.
(function () {
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
  var pointerDown = false;
  document.addEventListener("pointerdown", function () { pointerDown = true; }, true);
  document.addEventListener("pointerup", function () { setTimeout(function () { pointerDown = false; }, 0); }, true);
  // Polling guard (used in hx-trigger): never swap while the viewer is pressing,
  // has keyboard focus in a form, or the tab is hidden.
  window.refreshAllowed = function () {
    var active = document.activeElement;
    return !(pointerDown || document.hidden || (active && active.closest && active.closest("form")));
  };
  document.addEventListener("htmx:afterSwap", function () { localize(document); renderConn(); });
  localize(document);

  // In-flight state of the mark-normal button (htmx disables it via hx-disabled-elt).
  function isMark(el) { return el && el.matches && el.matches("button.btn[hx-post]"); }
  document.addEventListener("htmx:beforeRequest", function (e) {
    var btn = e.detail.elt;
    if (!isMark(btn)) return;
    btn.dataset.label = btn.textContent;
    btn.textContent = "Marcando…";
    btn.setAttribute("aria-busy", "true");
  });
  document.addEventListener("htmx:afterRequest", function (e) {
    var btn = e.detail.elt;
    if (!isMark(btn) || !btn.isConnected) return;
    btn.textContent = btn.dataset.label || "Esto era normal";
    btn.removeAttribute("aria-busy");
  });

  // Engine connection: when it is down, say how long since the last good update.
  var lastOk = null;
  function ago(s) { return s < 60 ? s + " s" : s < 3600 ? Math.floor(s / 60) + " min" : Math.floor(s / 3600) + " h"; }
  function renderConn() {
    var conn = document.getElementById("conn");
    if (!conn) return;
    if (!conn.classList.contains("down")) { lastOk = Date.now(); return; }
    var label = conn.querySelector("span:last-child");
    if (!label || lastOk === null) return;
    label.textContent = "Motor sin conexión · último dato hace " + ago(Math.max(0, Math.round((Date.now() - lastOk) / 1000)));
  }
  renderConn();
  setInterval(renderConn, 1000);
})();
