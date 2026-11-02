/* Datumfält som alltid visar ÅÅÅÅ-MM-DD.
 *
 * Webbläsarens inbyggda datumfält visar datum i webbläsarens språk (t.ex. 11/02/2026 i en engelsk Chrome).
 * Här blir varje <input type="date"> ett textfält med samma namn och värde (värdet är redan ÅÅÅÅ-MM-DD),
 * och en kalenderknapp öppnar den inbyggda väljaren. Skrivs 20261102 blir det 2026-11-02.
 */
(function () {
  var ISO = /^\d{4}-\d{2}-\d{2}$/;

  function normalisera(v) {
    v = (v || "").trim();
    var m = v.match(/^(\d{4})-?(\d{1,2})-?(\d{1,2})$/);
    if (!m) return v;
    return m[1] + "-" + ("0" + m[2]).slice(-2) + "-" + ("0" + m[3]).slice(-2);
  }

  function gor(falt) {
    if (falt.dataset.datum) return;
    falt.dataset.datum = "1";
    var valjare = document.createElement("input");
    valjare.type = "date";
    valjare.tabIndex = -1;
    valjare.setAttribute("aria-hidden", "true");
    valjare.className = "datum-valjare";
    if (falt.min) valjare.min = falt.min;
    if (falt.max) valjare.max = falt.max;

    falt.type = "text";
    falt.inputMode = "numeric";
    falt.autocomplete = "off";
    falt.placeholder = "ÅÅÅÅ-MM-DD";
    falt.pattern = "\\d{4}-\\d{2}-\\d{2}";
    falt.title = falt.title || "Datum som ÅÅÅÅ-MM-DD";

    var knapp = document.createElement("button");
    knapp.type = "button";
    knapp.className = "datum-knapp";
    knapp.setAttribute("aria-label", "Välj datum");
    knapp.innerHTML = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2">' +
      '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/></svg>';

    var omslag = document.createElement("span");
    omslag.className = "datumfalt";
    falt.parentNode.insertBefore(omslag, falt);
    omslag.appendChild(falt);
    omslag.appendChild(knapp);
    omslag.appendChild(valjare);

    knapp.addEventListener("click", function () {
      valjare.value = ISO.test(falt.value) ? falt.value : "";
      if (valjare.showPicker) { try { valjare.showPicker(); return; } catch (e) { /* faller tillbaka */ } }
      valjare.focus(); valjare.click();
    });
    valjare.addEventListener("change", function () {
      falt.value = valjare.value;
      falt.dispatchEvent(new Event("input", { bubbles: true }));
      falt.dispatchEvent(new Event("change", { bubbles: true }));
    });
    falt.addEventListener("blur", function () {
      var ny = normalisera(falt.value);
      if (ny !== falt.value) {
        falt.value = ny;
        falt.dispatchEvent(new Event("change", { bubbles: true }));
      }
    });
  }

  function alla(rot) {
    (rot || document).querySelectorAll('input[type="date"]:not(.datum-valjare)').forEach(gor);
  }

  document.addEventListener("DOMContentLoaded", function () { alla(document); });
  document.addEventListener("htmx:afterSwap", function (e) { alla(e.target); });
})();
