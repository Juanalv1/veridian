// Live refresh, the mark-normal swap and navigation are done by htmx (see the
// hx-* attributes in the templates). This file only guards the polling and shows
// times in the viewer's local time zone (the engine sends UTC).
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
  document.addEventListener("htmx:afterSwap", function () { localize(document); });
  localize(document);
})();
