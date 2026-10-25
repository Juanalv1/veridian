// Progressive enhancement: on submit, disable the form's button and show its
// data-busy label so the form cannot be sent twice. Without JS the form posts normally.
(function () {
  function reset(btn) {
    btn.disabled = false;
    btn.removeAttribute("aria-busy");
    if (btn.dataset.label) btn.textContent = btn.dataset.label;
  }
  document.addEventListener("submit", function (e) {
    var btn = e.target.querySelector("button[data-busy]");
    if (!btn || btn.disabled) return;
    btn.dataset.label = btn.textContent;
    // Defer so the browser reads the form data before the button is disabled.
    setTimeout(function () {
      btn.disabled = true;
      btn.setAttribute("aria-busy", "true");
      btn.textContent = btn.dataset.busy;
    }, 0);
  });
  // Back/forward cache can restore the page with the busy state still on.
  window.addEventListener("pageshow", function (e) {
    if (e.persisted) document.querySelectorAll("button[data-busy]").forEach(reset);
  });
})();
