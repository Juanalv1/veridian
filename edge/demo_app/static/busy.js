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

  // Forms with data-fetch are sent with fetch, like a customer's frontend would.
  // Veridian's middleware refuses a blocked request with 403 {"error": "blocked"};
  // the page then shows its own notice (data-blocked-notice) instead of raw JSON.
  function showPage(html) {
    var doc = new DOMParser().parseFromString(html, "text/html");
    var next = doc.querySelector("main");
    if (!next) {
      document.open();
      document.write(html);
      document.close();
      return;
    }
    document.querySelector("main").replaceWith(document.adoptNode(next));
    document.title = doc.title;
  }
  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (!form.hasAttribute("data-fetch") || !window.fetch || !window.DOMParser) return;
    e.preventDefault();
    var notice = document.getElementById(form.dataset.blockedNotice);
    var btn = form.querySelector("button[data-busy]");
    if (notice) notice.hidden = true;
    fetch(form.action, {
      method: "POST",
      body: new URLSearchParams(new FormData(form)),
      headers: { Accept: "text/html" },
      credentials: "same-origin",
    }).then(function (r) {
      if (r.status === 403) {
        return r.clone().json().catch(function () { return null; }).then(function (body) {
          if (body && body.error === "blocked" && notice) {
            // With self-unlock the SDK adds a message (check your inbox); otherwise the generic text stays.
            if (!notice.dataset.generic) notice.dataset.generic = notice.textContent;
            notice.textContent = body.message || notice.dataset.generic;
            notice.hidden = false;
            if (btn) reset(btn);
            return;
          }
          return r.text().then(showPage);
        });
      }
      if (r.redirected) {
        window.location.assign(r.url);
        return;
      }
      return r.text().then(showPage);
    }).catch(function () {
      // Network failure: fall back to the plain form post (submit() fires no submit event).
      form.submit();
    });
  });
})();
