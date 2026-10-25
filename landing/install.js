(function () {
  var tabs = Array.prototype.slice.call(document.querySelectorAll('.codecard [role="tab"]'));
  function select(tab) {
    tabs.forEach(function (t) {
      var on = t === tab;
      t.setAttribute('aria-selected', on ? 'true' : 'false');
      t.tabIndex = on ? 0 : -1;
      document.getElementById(t.getAttribute('aria-controls')).hidden = !on;
    });
    tab.focus();
  }
  tabs.forEach(function (tab, i) {
    tab.addEventListener('click', function () { select(tab); });
    tab.addEventListener('keydown', function (e) {
      var j = null;
      if (e.key === 'ArrowRight') j = (i + 1) % tabs.length;
      else if (e.key === 'ArrowLeft') j = (i - 1 + tabs.length) % tabs.length;
      else if (e.key === 'Home') j = 0;
      else if (e.key === 'End') j = tabs.length - 1;
      if (j === null) return;
      e.preventDefault();
      select(tabs[j]);
    });
  });

  var status = document.getElementById('copy-status');
  function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
    return new Promise(function (resolve, reject) {
      var ta = document.createElement('textarea');
      ta.value = text;
      ta.setAttribute('readonly', '');
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      var ok = false;
      try { ok = document.execCommand('copy'); } catch (err) {}
      document.body.removeChild(ta);
      ok ? resolve() : reject(new Error('copy unavailable'));
    });
  }
  Array.prototype.forEach.call(document.querySelectorAll('.copy'), function (btn) {
    var timer;
    var label = btn.textContent;
    btn.addEventListener('click', function () {
      var text = btn.dataset.copy || document.getElementById(btn.dataset.copyFrom).textContent;
      copyText(text).then(function () { flash('Copiado', 'Copiado al portapapeles'); },
        function () { flash('No se pudo copiar', 'No se pudo copiar; selecciona el código a mano'); });
    });
    function flash(label, msg) {
      btn.textContent = label;
      if (status) status.textContent = msg;
      clearTimeout(timer);
      timer = setTimeout(function () { btn.textContent = label; if (status) status.textContent = ''; }, 1800);
    }
  });
})();
