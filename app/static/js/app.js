/* Shared by every page: talking to the API, and building DOM safely.
 *
 * Configuration comes from data attributes on <body>, so this file is the same
 * for every deployment and every request and can be cached as such.
 */
(() => {
  "use strict";

  // The path prefix a reverse proxy mounts the app under, "" at the root.
  const base = document.body.dataset.basePath || "";

  /* Fetch JSON. A failure becomes an Error carrying the server's own message.
     401 is marked apart from the rest because it is the one failure a page can
     do something about: it means "sign in", not "something broke". */
  async function api(path, options = {}) {
    const response = await fetch(base + path, { credentials: "same-origin", ...options });
    if (response.ok) return response.status === 204 ? null : response.json();

    const body = await response.json().catch(() => ({}));
    const detail = typeof body.detail === "string" ? body.detail : null;
    const error = new Error(detail || `Request failed (${response.status})`);
    error.status = response.status;
    if (Array.isArray(body.problems)) error.problems = body.problems;
    error.unauthorized = response.status === 401;
    throw error;
  }

  /* Build an element. Text goes in as textContent and never as HTML: every
     string these pages show comes out of a library file, not out of this code. */
  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function link(path, className, text) {
    const node = el("a", className, text);
    node.href = base + path;
    return node;
  }

  function button(className, text, onClick) {
    const node = el("button", className, text);
    node.type = "button";
    if (onClick) node.addEventListener("click", onClick);
    return node;
  }

  /* A message standing where content would be, with optional actions under it. */
  function stateBox(title, hint, actions = []) {
    const box = el("div", "state");
    box.append(el("p", "state__title", title));
    if (hint) box.append(el("p", "state__hint", hint));
    box.append(...actions);
    return box;
  }

  const plural = (count, one, many = `${one}s`) => `${count} ${count === 1 ? one : many}`;

  /* Whether this page is inside KiCad's panel and not an ordinary browser.
     KiCad's WebView hands the page a way to send it messages, and which one
     depends on the platform: the same three the bridge looks for. */
  function inKicad() {
    const { webkit, chrome, external } = window;
    return Boolean(
      (webkit && webkit.messageHandlers && webkit.messageHandlers.kicad) ||
        (chrome && chrome.webview) ||
        (external && typeof external.invoke === "function")
    );
  }

  window.App = { base, api, el, link, button, stateBox, plural, inKicad };
})();
