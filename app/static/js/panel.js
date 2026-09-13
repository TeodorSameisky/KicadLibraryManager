/* The catalog panel KiCad embeds.
 *
 * Reads its configuration from data attributes on <body> so the file stays
 * static and cacheable rather than being regenerated per request.
 */
(function () {
  "use strict";

  var cfg = document.body.dataset;
  var BASE = cfg.basePath || "";
  var AUTH = cfg.authConfigured === "true";

  var els = {
    search: document.getElementById("search"),
    results: document.getElementById("results"),
    count: document.getElementById("count"),
    status: document.getElementById("index-status"),
    sessionBadge: document.getElementById("session-badge"),
    sessionAction: document.getElementById("session-action"),
    log: document.getElementById("log"),
    logWrap: document.getElementById("log-wrap"),
  };

  var signedIn = cfg.signedIn === "true";
  var searchTimer = null;
  var pollTimer = null;

  // ------------------------------------------------------------- helpers

  function api(path, options) {
    return fetch(BASE + path, Object.assign({ credentials: "same-origin" }, options))
      .then(function (response) {
        if (!response.ok) {
          return response.json()
            .catch(function () { return {}; })
            .then(function (body) {
              throw new Error(body.detail || "Request failed (" + response.status + ")");
            });
        }
        return response.status === 204 ? null : response.json();
      });
  }

  function log(message) {
    if (!els.log) return;
    var stamp = new Date().toLocaleTimeString();
    els.log.textContent = "[" + stamp + "] " + message + "\n" + els.log.textContent;
    if (els.logWrap) els.logWrap.hidden = false;
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function showState(title, hint) {
    els.results.replaceChildren();
    var box = el("div", "state");
    box.appendChild(el("p", "state__title", title));
    if (hint) box.appendChild(el("p", "state__hint", hint));
    els.results.appendChild(box);
  }

  function showSkeleton() {
    els.results.replaceChildren();
    var list = el("ul", "skeleton");
    list.setAttribute("aria-hidden", "true");
    for (var i = 0; i < 5; i++) {
      var row = document.createElement("li");
      row.appendChild(el("span"));
      row.appendChild(el("span"));
      row.appendChild(el("span"));
      list.appendChild(row);
    }
    els.results.appendChild(list);
  }

  // -------------------------------------------------------------- parts

  function partRow(part) {
    var row = el("li", "part");

    var thumb = el("span", "part__thumb");
    var img = document.createElement("img");
    img.loading = "lazy";
    img.alt = "";
    img.src = BASE + "/api/v1/parts/" + encodeURIComponent(part.ipn) + "/symbol.svg";
    // A part whose symbol cannot be built still belongs in the list.
    img.addEventListener("error", function () { thumb.replaceChildren(); });
    thumb.appendChild(img);
    row.appendChild(thumb);

    var link = el("a", "part__ipn", part.ipn);
    link.href = BASE + "/ipn/" + encodeURIComponent(part.ipn);
    row.appendChild(link);

    var desc = el("span", "part__desc", part.description || "");
    desc.title = part.description || "";
    row.appendChild(desc);

    var source = part.preferred_mpn
      ? part.preferred_mpn + (part.mpn_count > 1 ? "  +" + (part.mpn_count - 1) : "")
      : "no source";
    row.appendChild(el("span", "part__source", source));

    var action = el("span", "part__action");
    var place = el("button", "button button--primary", "Place");
    place.type = "button";
    place.setAttribute("aria-label", "Place " + part.ipn + " into KiCad");
    place.addEventListener("click", function () { place_(part.ipn, place); });
    action.appendChild(place);
    row.appendChild(action);

    return row;
  }

  function render(data) {
    if (data.state === "syncing") {
      showSkeleton();
      if (!pollTimer) pollTimer = setTimeout(function () { pollTimer = null; load(); }, 2000);
      return;
    }
    if (data.state === "empty") {
      showState("No library configured",
        "Set LIBRARY_SOURCES to a git repository holding the part library.");
      return;
    }
    if (!data.parts.length) {
      var searching = els.search && els.search.value.trim();
      showState(
        searching ? "No matching parts" : "The catalog is empty",
        searching
          ? "Try a value, a package or a manufacturer part number."
          : "Parts appear here once the library repository contains them."
      );
      els.count.textContent = data.total ? data.total + " in catalog" : "";
      return;
    }

    var list = el("ul", "parts");
    data.parts.forEach(function (part) { list.appendChild(partRow(part)); });
    els.results.replaceChildren(list);
    els.count.textContent = data.returned === data.total
      ? data.total + " parts"
      : data.returned + " of " + data.total;
  }

  function load() {
    var q = els.search ? els.search.value : "";
    api("/api/v1/parts?q=" + encodeURIComponent(q))
      .then(render)
      .catch(function (err) {
        showState("Could not load parts", err.message);
      });
  }

  // -------------------------------------------------------------- place

  function place_(ipn, button) {
    button.disabled = true;
    var original = button.textContent;
    button.textContent = "Placing";
    log("Fetching assets for " + ipn);

    api("/api/v1/parts/" + encodeURIComponent(ipn) + "/assets")
      .then(function (bundle) {
        (bundle.warnings || []).forEach(function (w) { log("Warning: " + w); });

        // Sent in the order the server returns them: footprint and 3D model
        // first with mode SAVE, the symbol last with mode PLACE.
        return bundle.assets.reduce(function (chain, asset) {
          return chain.then(function () {
            log("Sending " + asset.label);
            return window.KicadBridge.send(asset.command, asset.parameters, asset.data);
          });
        }, Promise.resolve());
      })
      .then(function () { log("Placed " + ipn); })
      .catch(function (err) { log("Could not place " + ipn + ": " + err.message); })
      .then(function () {
        button.disabled = false;
        button.textContent = original;
      });
  }

  // ------------------------------------------------------------ session

  function refreshSession() {
    if (!AUTH) return Promise.resolve();
    return api("/api/v1/session/me").then(function (me) {
      signedIn = !!me.authenticated;
      if (els.sessionBadge) {
        els.sessionBadge.textContent = signedIn ? me.display_name : "Not signed in";
        els.sessionBadge.className = "badge " + (signedIn ? "badge--ok" : "");
      }
      if (els.sessionAction) {
        els.sessionAction.textContent = signedIn ? "Sign out" : "Sign in";
      }
    });
  }

  function onSessionAction() {
    els.sessionAction.disabled = true;
    var done = function () { els.sessionAction.disabled = false; };

    if (signedIn) {
      log("Signing out");
      api("/api/v1/session/logout", { method: "POST" })
        .then(refreshSession)
        .then(function () {
          log("Signed out");
          return window.KicadBridge.logout().catch(function () {});
        })
        .then(done, done);
      return;
    }

    log("Requesting sign-in");
    window.KicadBridge.login()
      .then(refreshSession)
      .then(function () { log(signedIn ? "Signed in" : "Sign-in did not create a session"); })
      .catch(function (err) { log("Sign-in failed: " + err.message); })
      .then(done, done);
  }

  // ------------------------------------------------------------- status

  function refreshStatus() {
    api("/api/v1/status").then(function (status) {
      if (!els.status) return;
      els.status.hidden = false;

      if (status.state === "ready" && status.errors === 0) {
        els.status.className = "badge badge--ok";
        els.status.textContent = status.parts + " parts";
        return;
      }
      if (status.state === "ready") {
        els.status.className = "badge badge--warn";
        els.status.textContent = status.errors + " unresolved";
        els.status.title = "Some references in the library do not resolve";
        return;
      }
      els.status.className = "badge";
      els.status.textContent = status.state;
      if (status.error) els.status.title = status.error;
    }).catch(function () { /* the list already reports failure */ });
  }

  // --------------------------------------------------------------- init

  if (!window.KicadBridge.isEmbedded()) {
    log("Not running inside KiCad; parts can be browsed but not placed.");
  }

  window.KicadBridge.onMessage(function (env) {
    if (env.command === "NEW_SESSION") log("Session established with KiCad");
  });

  if (els.search) {
    els.search.addEventListener("input", function () {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(load, 150);
    });
  }

  if (els.sessionAction) {
    els.sessionAction.addEventListener("click", onSessionAction);
  }

  load();
  refreshStatus();
})();
