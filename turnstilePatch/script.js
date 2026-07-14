// Turnstile helper — ported from HSJ-BanFan/grok-register-web.
// IMPORTANT: do NOT repeatedly click during verification — that resets the widget
// and forces another challenge (observed on managed Cloudflare challenges).
//
// Runs in every frame (all_frames). Only the Cloudflare challenge frame acts;
// the parent page only applies light stealth (no click loop).

(function () {
  "use strict";

  function isTurnstileFrame() {
    try {
      var href = String(window.location.href || "");
      return (
        href.indexOf("challenges.cloudflare.com") >= 0 ||
        href.indexOf("turnstile") >= 0 ||
        href.indexOf("cdn-cgi/challenge") >= 0
      );
    } catch (e) {
      return false;
    }
  }

  // Soft stealth on top-level + challenge frames (local Chromium; Roxy already
  // has its own fingerprint stack — this is harmless if properties are locked).
  try {
    Object.defineProperty(navigator, "webdriver", {
      get: function () {
        return undefined;
      },
      configurable: true,
    });
  } catch (e) {}
  try {
    if (!window.chrome) window.chrome = { runtime: {} };
  } catch (e) {}

  if (!isTurnstileFrame()) {
    return;
  }

  var clickedOnce = false;

  function alreadyChecked(box) {
    if (!box) return false;
    if (box.checked) return true;
    try {
      var aria = (box.getAttribute && box.getAttribute("aria-checked")) || "";
      return aria === "true";
    } catch (e) {
      return false;
    }
  }

  function autoSolve() {
    if (clickedOnce) return;
    var checkbox =
      document.querySelector('input[type="checkbox"]') ||
      document.querySelector(".cb-i") ||
      document.querySelector('[role="checkbox"]') ||
      document.querySelector(".mark");
    if (!checkbox || alreadyChecked(checkbox)) {
      return;
    }
    clickedOnce = true;
    try {
      checkbox.click();
    } catch (e) {}
  }

  // Single delayed attempt only — never re-click while Cloudflare is verifying.
  setTimeout(autoSolve, 1500);
})();
