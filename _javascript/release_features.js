/* Open the release feature list when its link is followed. */
(function () {
  "use strict";

  const featureHash = "#extra-features";

  function openReleaseFeatures(doc, hash) {
    if (hash !== featureHash) return false;
    const details = doc.getElementById("extra-features");
    if (!details || String(details.tagName).toLowerCase() !== "details") return false;
    details.open = true;
    details.scrollIntoView({ block: "start" });
    return true;
  }

  function followReleaseFeaturesLink(event, doc, location) {
    if (event.defaultPrevented || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey
        || (typeof event.button === "number" && event.button !== 0)) return false;
    let target = event.target;
    if (target && typeof target.closest !== "function") target = target.parentElement;
    const link = target && typeof target.closest === "function" ? target.closest("a[href]") : null;
    if (!link || link.hasAttribute("download")) return false;
    const linkTarget = (link.getAttribute("target") || "").toLowerCase();
    if (linkTarget && linkTarget !== "_self") return false;
    let destination;
    let current;
    try {
      current = new URL(location.href);
      destination = new URL(link.getAttribute("href"), doc.baseURI || current.href);
    } catch (_) {
      return false;
    }
    if (destination.origin !== current.origin || destination.pathname !== current.pathname
        || destination.search !== current.search) return false;
    // Do not prevent normal navigation. Opening before its default action also
    // handles a repeated click when this hash is already in the address bar.
    return openReleaseFeatures(doc, destination.hash);
  }

  if (typeof module === "object" && module.exports) {
    module.exports = { openReleaseFeatures, followReleaseFeaturesLink };
  }
  if (typeof window === "undefined" || typeof document === "undefined") return;

  const open = function () {
    openReleaseFeatures(document, window.location.hash);
  };
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", open, { once: true });
  } else {
    open();
  }
  window.addEventListener("hashchange", open);
  document.addEventListener("click", function (event) {
    followReleaseFeaturesLink(event, document, window.location);
  });
  // Material can replace the article without reloading this script.
  if (typeof document$ !== "undefined" && document$ && typeof document$.subscribe === "function") {
    document$.subscribe(open);
  }
})();
