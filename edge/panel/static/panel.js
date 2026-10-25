// Refresh the data in place instead of reloading the page, so a click is
// never lost to a reload, and show times in the viewer's local time zone
// (the engine sends UTC).
(function () {
  var REFRESH_MS = Number(document.body.getAttribute("data-refresh-ms")) || 2000;
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
  var busy = false;
  document.addEventListener("pointerdown", function () { pointerDown = true; }, true);
  document.addEventListener("pointerup", function () { setTimeout(function () { pointerDown = false; }, 0); }, true);
  function swap(doc) {
    ["main.wrap", ".conn"].forEach(function (sel) {
      var current = document.querySelector(sel);
      var next = doc.querySelector(sel);
      if (current && next) current.replaceWith(next);
    });
    localize(document);
  }
  function tick() {
    var active = document.activeElement;
    // Never swap while the viewer is pressing or has keyboard focus in a form.
    if (busy || pointerDown || document.hidden || (active && active.closest && active.closest("form"))) return;
    busy = true;
    fetch(location.pathname, { cache: "no-store" })
      .then(function (r) { return r.text(); })
      .then(function (html) { swap(new DOMParser().parseFromString(html, "text/html")); })
      .catch(function () {})
      .finally(function () { busy = false; });
  }
  localize(document);
  setInterval(tick, REFRESH_MS);
})();
