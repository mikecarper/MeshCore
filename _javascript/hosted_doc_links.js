/* Source links help GitHub readers; the hosted page is already their destination. */
(function () {
  "use strict";

  const hostedHostname = "mikecarper.github.io";
  const bannerSelector = ".meshcore-hosted-doc-link";

  function hideHostedDocLinks(doc, hostname) {
    if (hostname !== hostedHostname) return 0;
    const banners = doc.querySelectorAll(bannerSelector);
    for (const banner of banners) banner.hidden = true;
    return banners.length;
  }

  if (typeof module === "object" && module.exports) {
    module.exports = { hideHostedDocLinks };
  }
  if (typeof window === "undefined" || typeof document === "undefined") return;

  const hide = function () {
    hideHostedDocLinks(document, window.location.hostname);
  };
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", hide, { once: true });
  } else {
    hide();
  }
  // Material can replace article content without loading the page again.
  if (typeof document$ !== "undefined" && document$ && typeof document$.subscribe === "function") {
    document$.subscribe(hide);
  }
})();
