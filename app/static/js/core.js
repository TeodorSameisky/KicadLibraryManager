/* Shared by every page.
 *
 * Configuration is read from data attributes on <body> so each script stays
 * static and cacheable rather than being regenerated per request.
 */
(function () {
  "use strict";

  var BASE = document.body.dataset.basePath || "";

  /* Fetch JSON, turning a failure into an Error carrying the server's own
     message. A 401 is distinguished because it is the one failure the page
     can do something about: it means sign in, not something broke. */
  function api(path, options) {
    return fetch(BASE + path, Object.assign({ credentials: "same-origin" }, options)).then(
      function (response) {
        if (response.ok) return response.status === 204 ? null : response.json();

        return response
          .json()
          .catch(function () { return {}; })
          .then(function (body) {
            var err = new Error(body.detail || "Request failed (" + response.status + ")");
            err.status = response.status;
            err.unauthorized = response.status === 401;
            throw err;
          });
      }
    );
  }

  /* Build an element. Text is set as textContent, never as HTML: every string
     here originates in a library file rather than from us. */
  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function clear(node) {
    node.replaceChildren();
    return node;
  }

  /* A centred message where a list would be. `actions` are appended below it. */
  function stateBox(title, hint, actions) {
    var box = el("div", "state");
    box.appendChild(el("p", "state__title", title));
    if (hint) box.appendChild(el("p", "state__hint", hint));
    (actions || []).forEach(function (node) { box.appendChild(node); });
    return box;
  }

  function link(href, className, text) {
    var node = el("a", className, text);
    node.href = BASE + href;
    return node;
  }

  function button(className, text, onClick) {
    var node = el("button", className, text);
    node.type = "button";
    if (onClick) node.addEventListener("click", onClick);
    return node;
  }

  function plural(count, one, many) {
    return count + " " + (count === 1 ? one : many || one + "s");
  }

  window.App = {
    base: BASE,
    api: api,
    el: el,
    clear: clear,
    link: link,
    button: button,
    stateBox: stateBox,
    plural: plural,
  };
})();
