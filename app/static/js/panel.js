/* The catalog panel KiCad embeds.
 *
 * Three things shape it. It renders in a WebView that is often narrow, so the
 * layout collapses rather than scrolling sideways. It is used from inside an
 * editor, so it is driveable from the keyboard without reaching for a mouse.
 * And placing a part can fail for reasons the user can act on -- a missing
 * footprint, a broken extends chain -- so failures are reported on the row
 * that produced them rather than in a log the user has to think to open.
 */
(function () {
  "use strict";

  var App = window.App;
  var cfg = document.body.dataset;
  var AUTH = cfg.authConfigured === "true";

  var els = {
    search: document.getElementById("search"),
    filters: document.getElementById("filters"),
    results: document.getElementById("results"),
    count: document.getElementById("count"),
    status: document.getElementById("index-status"),
    sessionBadge: document.getElementById("session-badge"),
    sessionAction: document.getElementById("session-action"),
    log: document.getElementById("log"),
    logWrap: document.getElementById("log-wrap"),
  };

  var signedIn = cfg.signedIn === "true";

  /* Filters can arrive in the URL so a part page can link back to "everything
     else in this category". */
  var params = new URLSearchParams(window.location.search);
  var filters = {
    status: params.get("status"),
    category: params.get("category"),
  };
  var facets = { statuses: [], categories: [] };
  var rows = [];
  var selected = -1;
  var searchTimer = null;
  var pollTimer = null;

  // ------------------------------------------------------------- helpers

  function log(message) {
    if (!els.log) return;
    var stamp = new Date().toLocaleTimeString();
    els.log.textContent = "[" + stamp + "] " + message + "\n" + els.log.textContent;
    if (els.logWrap) els.logWrap.hidden = false;
  }

  function showState(title, hint, actions) {
    rows = [];
    selected = -1;
    els.results.replaceChildren(App.stateBox(title, hint, actions));
  }

  function showSkeleton() {
    rows = [];
    selected = -1;
    var list = App.el("ul", "skeleton");
    list.setAttribute("aria-hidden", "true");
    for (var i = 0; i < 5; i++) {
      var row = document.createElement("li");
      row.appendChild(App.el("span"));
      row.appendChild(App.el("span"));
      row.appendChild(App.el("span"));
      list.appendChild(row);
    }
    els.results.replaceChildren(list);
  }

  // ------------------------------------------------------------- filters

  /* Filters are drawn from the index rather than hard-coded, so the panel
     offers what this library actually contains. A chip that could only ever
     return nothing is worse than no chip. */
  function renderFilters() {
    if (!els.filters) return;
    App.clear(els.filters);

    var groups = [
      { key: "status", label: "Status", options: facets.statuses },
      { key: "category", label: "Category", options: facets.categories },
    ];

    var any = false;
    groups.forEach(function (group) {
      if (group.options.length < 2) return; // one option filters nothing
      any = true;

      var row = App.el("div", "filter");
      row.appendChild(App.el("span", "filter__label", group.label));

      var list = App.el("div", "filter__chips");
      list.setAttribute("role", "group");
      list.setAttribute("aria-label", group.label);
      list.appendChild(chip(group.key, null, "All", null));
      group.options.forEach(function (option) {
        list.appendChild(chip(group.key, option.value, option.label, option.count));
      });

      row.appendChild(list);
      els.filters.appendChild(row);
    });

    els.filters.hidden = !any;
  }

  function chip(key, value, label, count) {
    var active = filters[key] === value;
    var node = App.button("chip" + (active ? " chip--on" : ""), label, function () {
      filters[key] = active ? null : value;
      renderFilters();
      load();
    });
    node.setAttribute("aria-pressed", active ? "true" : "false");
    if (count !== null && count !== undefined) {
      node.appendChild(App.el("span", "chip__count", count));
    }
    return node;
  }

  // -------------------------------------------------------------- parts

  function partRow(part) {
    var row = App.el("li", "part");
    row.tabIndex = -1;

    var thumb = App.el("span", "part__thumb");
    var img = document.createElement("img");
    img.loading = "lazy";
    img.alt = "";
    img.src = App.base + "/api/v1/parts/" + encodeURIComponent(part.ipn) + "/symbol.svg";
    // A part whose symbol cannot be built still belongs in the list.
    img.addEventListener("error", function () { thumb.replaceChildren(); });
    thumb.appendChild(img);
    row.appendChild(thumb);

    var name = App.el("span", "part__name");
    name.appendChild(App.link("/ipn/" + encodeURIComponent(part.ipn), "part__ipn", part.ipn));
    if (part.status && part.status !== "approved") {
      name.appendChild(App.el("span", "badge badge--warn", part.status));
    }
    row.appendChild(name);

    var desc = App.el("span", "part__desc", part.description || "");
    desc.title = part.description || "";
    row.appendChild(desc);

    var source = part.preferred_mpn
      ? part.preferred_mpn + (part.mpn_count > 1 ? "  +" + (part.mpn_count - 1) : "")
      : "no source";
    row.appendChild(App.el("span", "part__source", source));

    var place = App.button("button button--primary", "Place", function () {
      placePart(part.ipn, row, place);
    });
    place.setAttribute("aria-label", "Place " + part.ipn + " into KiCad");
    var action = App.el("span", "part__action");
    action.appendChild(place);
    row.appendChild(action);

    // Spans the row, so a message has the width to say something useful.
    var box = App.el("div", "part__note");
    box.hidden = true;
    row.appendChild(box);

    row._place = place;
    return row;
  }

  /* Report on the row that produced it. A failure in a collapsed log at the
     bottom of the page is a failure nobody reads. */
  function note(row, kind, message) {
    var box = row.querySelector(".part__note");
    if (!box) return;
    box.className = "part__note" + (kind ? " part__note--" + kind : "");
    box.textContent = message || "";
    box.hidden = !message;
  }

  function render(data) {
    if (data.state === "syncing") {
      showSkeleton();
      els.count.textContent = "indexing";
      if (!pollTimer) pollTimer = setTimeout(function () { pollTimer = null; load(); }, 2000);
      return;
    }
    if (data.state === "empty") {
      showState(
        "No library configured",
        "Set LIBRARY_SOURCES to a git repository holding the part library."
      );
      els.count.textContent = "";
      return;
    }

    if (!data.parts.length) {
      var narrowed = filtersActive();
      showState(
        narrowed ? "Nothing matches" : "The catalog is empty",
        narrowed
          ? "Try a value, a package or a manufacturer part number."
          : "Parts appear here once the library repository contains them.",
        narrowed ? [App.button("button", "Clear filters", resetFilters)] : []
      );
      els.count.textContent = data.total ? App.plural(data.total, "part") + " in catalog" : "";
      return;
    }

    var list = App.el("ul", "parts");
    rows = data.parts.map(function (part) {
      var row = partRow(part);
      row.dataset.ipn = part.ipn;
      list.appendChild(row);
      return row;
    });
    selected = -1;
    els.results.replaceChildren(list);
    els.count.textContent = describe(data);
  }

  /* "12 of 40 matches" beats "12 of 4000": the size of the catalog says
     nothing about the search that produced the list in front of you. With no
     filters on, though, the catalog IS the result, so it stays "parts". */
  function describe(data) {
    var noun = filtersActive() ? ["match", "matches"] : ["part", "parts"];
    if (data.returned < data.matched) {
      return data.returned + " of " + App.plural(data.matched, noun[0], noun[1]);
    }
    return App.plural(data.matched, noun[0], noun[1]);
  }

  function filtersActive() {
    return !!((els.search && els.search.value.trim()) || filters.status || filters.category);
  }

  function resetFilters() {
    filters = { status: null, category: null };
    if (els.search) els.search.value = "";
    renderFilters();
    load();
  }

  function query() {
    var parts = ["q=" + encodeURIComponent(els.search ? els.search.value : "")];
    if (filters.status) parts.push("status=" + encodeURIComponent(filters.status));
    if (filters.category) parts.push("category=" + encodeURIComponent(filters.category));
    return parts.join("&");
  }

  function load() {
    App.api("/api/v1/parts?" + query())
      .then(render)
      .catch(function (err) {
        if (err.unauthorized) {
          showState("Sign in to browse this library", "Use the button above.");
          els.count.textContent = "";
          return;
        }
        showState("Could not load parts", err.message);
      });
  }

  // -------------------------------------------------------------- place

  function placePart(ipn, row, place) {
    place.disabled = true;
    var original = place.textContent;
    place.textContent = "Placing";
    note(row, null, "");
    log("Fetching assets for " + ipn);

    App.api("/api/v1/parts/" + encodeURIComponent(ipn) + "/assets")
      .then(function (bundle) {
        var warnings = bundle.warnings || [];
        warnings.forEach(function (w) { log("Warning: " + w); });
        if (warnings.length) note(row, "warn", warnings.join(" "));

        // Sent in the order the server returns them: footprint and 3D model
        // first with mode SAVE, the symbol last with mode PLACE.
        return bundle.assets.reduce(function (chain, asset) {
          return chain.then(function () {
            log("Sending " + asset.label);
            return window.KicadBridge.send(asset.command, asset.parameters, asset.data);
          });
        }, Promise.resolve());
      })
      .then(function () {
        log("Placed " + ipn);
        place.textContent = "Placed";
        setTimeout(function () { place.textContent = original; }, 1500);
      })
      .catch(function (err) {
        log("Could not place " + ipn + ": " + err.message);
        note(row, "error", err.message);
        place.textContent = original;
      })
      .then(function () { place.disabled = false; });
  }

  // ----------------------------------------------------------- keyboard

  /* The panel lives inside an editor, where reaching for a mouse to place a
     resistor is the slow path. */
  function select(index) {
    if (!rows.length) return;
    if (selected >= 0 && rows[selected]) rows[selected].classList.remove("part--on");
    selected = Math.max(0, Math.min(rows.length - 1, index));
    var row = rows[selected];
    row.classList.add("part--on");
    row.scrollIntoView({ block: "nearest" });
  }

  function onKeyDown(event) {
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    var typing = document.activeElement === els.search;

    if (event.key === "/" && !typing) {
      event.preventDefault();
      if (els.search) els.search.focus();
      return;
    }
    if (event.key === "Escape") {
      if (typing && els.search.value) {
        els.search.value = "";
        load();
      } else if (els.search) {
        els.search.blur();
      }
      return;
    }
    if (event.key === "ArrowDown") {
      event.preventDefault();
      select(selected + 1);
      return;
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      select(selected <= 0 ? 0 : selected - 1);
      return;
    }
    if (event.key === "Enter" && selected >= 0 && rows[selected]) {
      event.preventDefault();
      var place = rows[selected]._place;
      if (place && !place.disabled) place.click();
    }
  }

  // ------------------------------------------------------------ session

  function refreshSession() {
    if (!AUTH) return Promise.resolve();
    return App.api("/api/v1/session/me").then(function (me) {
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

  /* The catalog is closed to a signed-out visitor, so both directions have to
     reload it: signing in opens a list that was refused, signing out closes
     one that is on screen. */
  function afterSessionChange() {
    load();
    refreshStatus();
  }

  function onSessionAction() {
    els.sessionAction.disabled = true;
    var done = function () { els.sessionAction.disabled = false; };

    if (signedIn) {
      log("Signing out");
      App.api("/api/v1/session/logout", { method: "POST" })
        .then(refreshSession)
        .then(function () {
          log("Signed out");
          afterSessionChange();
          return window.KicadBridge.logout().catch(function () {});
        })
        .then(done, done);
      return;
    }

    log("Requesting sign-in");
    window.KicadBridge.login()
      .then(refreshSession)
      .then(function () {
        log(signedIn ? "Signed in" : "Sign-in did not create a session");
        afterSessionChange();
      })
      .catch(function (err) { log("Sign-in failed: " + err.message); })
      .then(done, done);
  }

  // ------------------------------------------------------------- status

  function refreshStatus() {
    App.api("/api/v1/status")
      .then(function (status) {
        facets = status.facets || { statuses: [], categories: [] };
        renderFilters();
        if (!els.status) return;

        els.status.hidden = false;
        els.status.removeAttribute("href");
        els.status.title = "";

        if (status.state !== "ready") {
          els.status.className = "badge";
          els.status.textContent = status.state;
          if (status.error) els.status.title = status.error;
          return;
        }
        if (status.errors) {
          // A link rather than a tooltip: what it counts is readable.
          els.status.className = "badge badge--danger badge--link";
          els.status.textContent = App.plural(status.errors, "unresolved reference");
          els.status.href = App.base + "/issues";
          return;
        }
        if (status.warnings) {
          els.status.className = "badge badge--warn badge--link";
          els.status.textContent = App.plural(status.warnings, "warning");
          els.status.href = App.base + "/issues?severity=warning";
          return;
        }
        els.status.className = "badge badge--ok";
        els.status.textContent = App.plural(status.parts, "part");
      })
      .catch(function () {
        // Signed out, or the list already reported the failure.
        if (els.status) els.status.hidden = true;
      });
  }

  // --------------------------------------------------------------- init

  if (!window.KicadBridge.isEmbedded()) {
    log("Not running inside KiCad; parts can be browsed but not placed.");
  }

  window.KicadBridge.onMessage(function (env) {
    if (env.command === "NEW_SESSION") log("Session established with KiCad");
  });

  if (els.search) {
    els.search.value = params.get("q") || "";
    els.search.addEventListener("input", function () {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(load, 150);
    });
  }

  if (els.sessionAction) {
    els.sessionAction.addEventListener("click", onSessionAction);
  }

  document.addEventListener("keydown", onKeyDown);

  load();
  refreshStatus();
})();
