/* Marks the page when it is inside KiCad's panel, before anything is drawn.
 *
 * Loaded in the head and not deferred, so that what the panel does not show
 * is never on screen even for a moment. KiCad's WebView hands the page a way
 * to send it messages, and which one depends on the platform: the same three
 * the bridge looks for.
 *
 * What is hidden is whatever carries the class `web-only`: the panel is for
 * finding a part and placing it, and the rest of the app is for a browser.
 */
(() => {
  "use strict";

  const { webkit, chrome, external } = window;
  const inside = Boolean(
    (webkit && webkit.messageHandlers && webkit.messageHandlers.kicad) ||
      (chrome && chrome.webview) ||
      (external && typeof external.invoke === "function")
  );

  if (inside) document.documentElement.classList.add("in-kicad");
})();
