/* ==========================================================================
   STS trading training app — small vanilla-JS helpers
   No framework, no dependencies. Everything is defensive: we feature-detect
   and silently do nothing when the expected elements are not on the page.

   Primary behavior (Req 6.8, 12.1):
   Disable the Confirm button once the confirm form is submitted, to reduce
   accidental double submission from an impatient double-click.

   IMPORTANT: this is only a UX safeguard. The server is the real guard against
   double execution — the confirm POST atomically claims the PENDING order, so a
   second request is rejected and the trade still executes at most once. The JS
   below just makes the common double-click less likely; it never replaces the
   server-side idempotency check.
   ========================================================================== */

(function () {
  "use strict";

  // Bail out early if the DOM APIs we rely on are unavailable.
  if (typeof document === "undefined" || !document.querySelectorAll) {
    return;
  }

  /**
   * Find the confirm form(s) on the page.
   *
   * We target the confirm control robustly, in priority order, so this works
   * no matter exactly how order_review.html marks it up:
   *   1. A form explicitly tagged with the `confirm-form` class (preferred
   *      convention — templates can opt in).
   *   2. Any form whose `action` points at a `/confirm` URL (the confirm POST
   *      goes to /orders/<id>/confirm per the design). This is the robust
   *      fallback that works even if the template is just a plain
   *      <form action=".../confirm" method="post"><button>Confirm</button>.
   *
   * We intentionally do NOT touch the Cancel form (/cancel), so cancelling
   * stays fully usable.
   *
   * @returns {HTMLFormElement[]} matching, de-duplicated forms
   */
  function findConfirmForms() {
    var forms = Array.prototype.slice.call(document.querySelectorAll("form"));
    var matched = [];

    forms.forEach(function (form) {
      var byClass = form.classList && form.classList.contains("confirm-form");

      // getAttribute keeps the raw action string; the resolved .action
      // property would also work but the attribute is simplest to test.
      var action = form.getAttribute("action") || "";
      var byAction = /\/confirm\/?($|\?)/.test(action);

      if (byClass || byAction) {
        matched.push(form);
      }
    });

    return matched;
  }

  /**
   * Return the submit control inside a form, if any.
   * Covers <button type="submit">, <button> (submit is the default), and
   * <input type="submit">.
   *
   * @param {HTMLFormElement} form
   * @returns {HTMLElement|null}
   */
  function findSubmitButton(form) {
    return (
      form.querySelector('button[type="submit"]') ||
      form.querySelector('input[type="submit"]') ||
      form.querySelector("button") ||
      null
    );
  }

  /**
   * Wire a single confirm form so its submit button disables on submit.
   * @param {HTMLFormElement} form
   */
  function wireConfirmForm(form) {
    form.addEventListener("submit", function () {
      var button = findSubmitButton(form);
      if (!button) {
        return; // Nothing to disable; let the submit proceed.
      }

      // Guard against double-firing if somehow submitted twice.
      if (button.disabled || button.classList.contains("is-disabled")) {
        return;
      }

      // Remember the original label so we could restore it if needed, then
      // give the user feedback that the action is in progress.
      var original = button.tagName === "INPUT" ? button.value : button.textContent;
      button.setAttribute("data-original-label", original);

      if (button.tagName === "INPUT") {
        button.value = "Confirming\u2026";
      } else {
        button.textContent = "Confirming\u2026";
      }

      // Add the class first (style hook), then disable on the next tick.
      // Disabling synchronously inside the submit handler can, in some
      // browsers, cancel the submission; deferring with setTimeout(0) lets
      // the form submit proceed and still disables the button immediately
      // after, blocking a second click.
      button.classList.add("is-disabled");
      button.setAttribute("aria-disabled", "true");
      window.setTimeout(function () {
        button.disabled = true;
      }, 0);
    });
  }

  /** Initialize all helpers once the DOM is ready. */
  function init() {
    findConfirmForms().forEach(wireConfirmForm);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    // Script loaded after DOMContentLoaded already fired — run now.
    init();
  }
})();
