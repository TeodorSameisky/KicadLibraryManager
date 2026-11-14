/* The catalog panel KiCad embeds.
 *
 * Three things shape it. It is drawn in a WebView that is often narrow, so the
 * layout folds rather than scrolling sideways. It is used from inside an
 * editor, so it can be driven from the keyboard. And placing a part can fail
 * for reasons the user can act on -- a missing footprint, a broken symbol --
 * so a failure is reported on the row that caused it, not in a log somebody
 * would have to think to open.
 */
(() => {
  "use strict";

  const { api, el, link, button, stateBox, plural, base } = window.App;
  const bridge = window.KicadBridge;
  const config = document.body.dataset;

  const SEARCH_DELAY_MS = 150;
  const INDEXING_POLL_MS = 2000;

  const dom = {
    search: document.getElementById("search"),
    count: document.getElementById("count"),
    filters: document.getElementById("filters"),
    results: document.getElementById("results"),
    indexStatus: document.getElementById("index-status"),
    sessionName: document.getElementById("session-name"),
    sessionAction: document.getElementById("session-action"),
    activity: document.getElementById("activity"),
    activityLog: document.getElementById("activity-log"),
  };

  // Filters can arrive in the address, so that a part's page can link back to
  // "everything else in this category".
  const params = new URLSearchParams(window.location.search);
  const filters = { status: params.get("status"), category: params.get("category") };
  dom.search.value = params.get("q") || "";

  let facets = { statuses: [], categories: [] };
  let rows = []; // {element, place} for each part on screen
  let selected = -1;
  let signedIn = config.signedIn === "true";
  let wasIndexing = false;
  let latestRequest = 0;
  let pollTimer = null;

  // ------------------------------------------------------------- activity

  function log(message) {
    const time = new Date().toLocaleTimeString();
    dom.activityLog.textContent = `[${time}] ${message}\n${dom.activityLog.textContent}`;
    dom.activity.hidden = false;
  }

  // -------------------------------------------------------------- filters

  /* Drawn from the index rather than written down here, so the panel offers
     what this library actually contains.

     The two are offered differently because they are different in kind.
     Status is a closed set of three that a library cannot add to, and stays a
     row of chips: one click, and what is on can be read without opening
     anything. Categories are declared by the library and keep being added to,
     so they fold into a dropdown -- thirty chips wrap into a block tall enough
     to push the parts they filter off the bottom of a panel that already
     shares its height with an editor. */
  function renderFilters() {
    const controls = [];
    // With fewer than two choices there is nothing to narrow.
    if (facets.statuses.length > 1) controls.push(statusChips());
    if (facets.categories.length > 1 || filters.category) controls.push(categorySelect());

    dom.filters.replaceChildren(...controls);
    dom.filters.hidden = controls.length === 0;
  }

  function statusChips() {
    const chips = el("div", "filter__chips");
    chips.setAttribute("role", "group");
    chips.setAttribute("aria-label", "Status");

    const options = [{ value: null, label: "All" }, ...facets.statuses];
    for (const option of options) {
      const on = filters.status === option.value;
      const chip = button(`chip${on ? " chip--on" : ""}`, option.label, () => {
        // Pressing the chip that is on takes it off again.
        filters.status = on ? null : option.value;
        renderFilters();
        load();
      });
      chip.setAttribute("aria-pressed", String(on));
      if (option.count !== undefined) chip.append(el("span", "chip__count", option.count));
      chips.append(chip);
    }

    const row = el("div", "filter");
    row.append(el("span", "filter__label", "Status"), chips);
    return row;
  }

  /* A native <select>, not a menu built here. The platform draws its popup,
     so it escapes the panel instead of being clipped by it, and it already
     scrolls, takes the arrow keys and jumps as you type. The counts ride in
     the option text because options cannot be styled; that is the price. */
  function categorySelect() {
    const select = el("select");
    select.id = "filter-category";
    select.append(new Option("All categories", ""));

    for (const option of facets.categories) {
      select.append(new Option(`${option.label}  (${option.count})`, option.value));
    }
    /* A category from the address that the catalog no longer has still
       narrows the list. The control owns up to it, rather than reading "All
       categories" above an empty result. */
    if (filters.category && !facets.categories.some((c) => c.value === filters.category)) {
      select.append(new Option(`${filters.category}  (0)`, filters.category));
    }
    select.value = filters.category || "";

    const wrap = el("span", `select${filters.category ? " select--on" : ""}`);
    wrap.append(select);

    /* Changed in place. Rendering the filters again here would rebuild the
       element under the cursor and drop the focus a keyboard user just spent
       three keys getting to. */
    select.addEventListener("change", () => {
      filters.category = select.value || null;
      wrap.classList.toggle("select--on", Boolean(filters.category));
      load();
    });

    const label = el("label", "filter__label", "Category");
    label.htmlFor = select.id;

    const row = el("div", "filter");
    row.append(label, wrap);
    return row;
  }

  const isNarrowed = () =>
    Boolean(dom.search.value.trim() || filters.status || filters.category);

  function clearFilters() {
    filters.status = null;
    filters.category = null;
    dom.search.value = "";
    renderFilters();
    load();
  }

  // ----------------------------------------------------------------- list

  function show(...nodes) {
    rows = [];
    selected = -1;
    dom.results.replaceChildren(...nodes);
  }

  function skeleton() {
    const list = el("ul", "skeleton");
    list.setAttribute("aria-hidden", "true");
    for (let i = 0; i < 5; i += 1) {
      const row = el("li");
      row.append(el("span"), el("span"), el("span"));
      list.append(row);
    }
    return list;
  }

  function partRow(part) {
    const ipn = encodeURIComponent(part.ipn);
    const row = el("li", "part");

    const thumb = el("span", "part__thumb");
    const image = el("img");
    image.loading = "lazy";
    image.alt = "";
    image.src = `${base}/api/v1/parts/${ipn}/symbol.svg`;
    // A part whose symbol cannot be drawn still belongs in the list.
    image.addEventListener("error", () => image.remove());
    thumb.append(image);

    const name = el("span", "part__name");
    name.append(link(`/ipn/${ipn}`, "part__ipn", part.ipn));
    if (part.status !== "approved") name.append(el("span", "badge badge--warn", part.status));

    const description = el("span", "part__desc", part.description);
    description.title = part.description;

    const others = part.mpn_count > 1 ? `  +${part.mpn_count - 1}` : "";
    const source = el(
      "span",
      "part__source",
      part.preferred_mpn ? part.preferred_mpn + others : "no source"
    );

    // Spans the row, so that a message has the width to say something useful.
    const note = el("div", "part__note");
    note.hidden = true;

    const place = button("button button--primary", "Place", () => placePart(part.ipn, place, note));
    place.setAttribute("aria-label", `Place ${part.ipn} into KiCad`);
    const action = el("span", "part__action");
    action.append(place);

    row.append(thumb, name, description, source, action, note);
    return { element: row, place };
  }

  /* "12 of 40 matches" beats "12 of 4000": the size of the catalog says
     nothing about the search that produced the list on screen. With nothing
     narrowing it, though, the catalog is the result, so those are "parts". */
  function describe(data) {
    const count = isNarrowed()
      ? plural(data.matched, "match", "matches")
      : plural(data.matched, "part");
    return data.returned < data.matched ? `${data.returned} of ${count}` : count;
  }

  function render(data) {
    const indexing = data.state === "syncing";
    if (indexing) {
      show(skeleton());
      dom.count.textContent = "indexing";
      clearTimeout(pollTimer);
      pollTimer = setTimeout(load, INDEXING_POLL_MS);
    } else if (data.state === "empty") {
      show(
        stateBox(
          "No library is configured",
          "Set LIBRARY_SOURCES to the git repository that holds the part library."
        )
      );
      dom.count.textContent = "";
    } else if (data.state === "error") {
      show(
        stateBox("The library could not be indexed", "The status page says what went wrong.", [
          link("/", "button", "Open the status page"),
        ])
      );
      dom.count.textContent = "";
    } else if (data.parts.length === 0) {
      const narrowed = isNarrowed();
      show(
        narrowed
          ? stateBox("Nothing matches", "Try a value, a package or a manufacturer part number.", [
              button("button", "Clear filters", clearFilters),
            ])
          : stateBox(
              "The catalog is empty",
              "Parts appear here once the library repository contains them."
            )
      );
      dom.count.textContent = data.total ? `${plural(data.total, "part")} in the catalog` : "";
    } else {
      const list = el("ul", "parts");
      const built = data.parts.map(partRow);
      list.append(...built.map((row) => row.element));
      show(list);
      rows = built;
      dom.count.textContent = describe(data);
    }

    // The filters and the status badge were drawn from an index that did not
    // exist yet; now that it does, they are fetched again.
    if (wasIndexing && !indexing) loadStatus();
    wasIndexing = indexing;
  }

  async function load() {
    const request = ++latestRequest;
    const query = new URLSearchParams({ q: dom.search.value });
    if (filters.status) query.set("status", filters.status);
    if (filters.category) query.set("category", filters.category);

    try {
      const data = await api(`/api/v1/parts?${query}`);
      // An answer to a search that has since been replaced is not shown:
      // replies can arrive out of the order they were asked in.
      if (request === latestRequest) render(data);
    } catch (error) {
      if (request !== latestRequest) return;
      dom.count.textContent = "";
      show(
        error.unauthorized
          ? stateBox("Sign in to browse this library", "Use the button at the top of the panel.")
          : stateBox("The parts could not be loaded", error.message, [
              button("button", "Try again", load),
            ])
      );
    }
  }

  // ---------------------------------------------------------------- place

  function setNote(note, kind, message) {
    note.className = `part__note${kind ? ` part__note--${kind}` : ""}`;
    note.textContent = message;
    note.hidden = !message;
  }

  async function placePart(ipn, place, note) {
    place.disabled = true;
    place.textContent = "Placing";
    setNote(note, null, "");

    try {
      if (!bridge.isEmbedded()) throw new Error("Parts can only be placed from inside KiCad.");

      log(`Fetching ${ipn}`);
      const bundle = await api(`/api/v1/parts/${encodeURIComponent(ipn)}/assets`);

      for (const warning of bundle.warnings) log(`Warning: ${warning}`);
      if (bundle.warnings.length) setNote(note, "warn", bundle.warnings.join(" "));

      // One at a time, in the order the server gives them: the footprint and
      // 3D model are saved first, and the symbol is placed last, so that what
      // it refers to is already there when it lands.
      await bridge.whenReady();
      for (const asset of bundle.assets) {
        log(`Sending ${asset.filename}`);
        await bridge.send(asset.command, asset.parameters, asset.data);
      }

      log(`Placed ${ipn}`);
      place.textContent = "Placed";
      setTimeout(() => { place.textContent = "Place"; }, 1500);
    } catch (error) {
      log(`Could not place ${ipn}: ${error.message}`);
      setNote(note, "error", error.message);
      place.textContent = "Place";
    } finally {
      place.disabled = false;
    }
  }

  // ------------------------------------------------------------- keyboard

  /* The panel lives inside an editor, where reaching for the mouse to place a
     resistor is the slow way round. */
  function select(index) {
    if (!rows.length) return;
    if (rows[selected]) rows[selected].element.classList.remove("part--selected");
    selected = Math.max(0, Math.min(rows.length - 1, index));
    const row = rows[selected].element;
    row.classList.add("part--selected");
    row.scrollIntoView({ block: "nearest" });
  }

  function onKeyDown(event) {
    if (event.ctrlKey || event.metaKey || event.altKey) return;

    const focused = document.activeElement;
    /* An open dropdown owns the arrows, Enter and typing. The list's
       shortcuts would fight it for all three. */
    if (focused && focused.tagName === "SELECT") return;
    const typing = focused === dom.search;

    switch (event.key) {
      case "/":
        if (typing) return;
        dom.search.focus();
        break;
      case "Escape":
        if (typing && dom.search.value) {
          dom.search.value = "";
          load();
        } else {
          dom.search.blur();
        }
        return;
      case "ArrowDown":
        select(selected + 1);
        break;
      case "ArrowUp":
        select(selected - 1);
        break;
      case "Enter": {
        // A focused button or link keeps its own Enter.
        if (focused && (focused.tagName === "BUTTON" || focused.tagName === "A")) return;
        const row = rows[selected];
        if (!row || row.place.disabled) return;
        row.place.click();
        break;
      }
      default:
        return;
    }
    event.preventDefault();
  }

  // -------------------------------------------------------------- session

  async function refreshSession() {
    const me = await api("/api/v1/session/me");
    signedIn = Boolean(me.authenticated);
    dom.sessionName.textContent = signedIn ? me.display_name : "Not signed in";
    dom.sessionName.classList.toggle("badge--ok", signedIn);
    dom.sessionAction.textContent = signedIn ? "Sign out" : "Sign in";
  }

  /* Both directions change what may be seen: signing in opens a list that was
     refused, signing out closes the one on screen. Everything is fetched
     again in place -- the page is never reloaded, because KiCad would not
     open a new RPC session for it. */
  function reloadCatalog() {
    load();
    loadStatus();
  }

  async function signOut() {
    log("Signing out");
    await api("/api/v1/session/logout", { method: "POST" });
    await refreshSession();
    reloadCatalog();
    log("Signed out");
    // KiCad holds tokens of its own; without this it would sign straight
    // back in. Outside KiCad there is nobody to tell.
    if (bridge.isEmbedded()) await bridge.logout().catch(() => {});
  }

  async function signIn() {
    /* Outside KiCad there is no bridge to ask, so the browser signs in with
       the identity provider itself and comes back here. Leaving the page is
       fine in a browser: there is no RPC session to lose. */
    if (!bridge.isEmbedded()) {
      if (config.webLogin !== "true") {
        log("Signing in from a browser is disabled; sign in from KiCad instead.");
        return;
      }
      const here = window.location.pathname + window.location.search;
      window.location.assign(`${base}/auth/login?next=${encodeURIComponent(here)}`);
      return;
    }

    log("Asking KiCad to sign in");
    await bridge.login();
    await refreshSession();
    reloadCatalog();
    log(signedIn ? "Signed in" : "The sign-in did not create a session");
  }

  async function onSessionAction() {
    dom.sessionAction.disabled = true;
    try {
      await (signedIn ? signOut() : signIn());
    } catch (error) {
      log(`${signedIn ? "Sign-out" : "Sign-in"} failed: ${error.message}`);
    } finally {
      dom.sessionAction.disabled = false;
    }
  }

  // --------------------------------------------------------- index status

  function showIndexStatus(status) {
    const badge = dom.indexStatus;
    badge.hidden = false;
    badge.removeAttribute("href");
    badge.title = "";

    if (status.state !== "ready") {
      badge.className = `badge${status.state === "error" ? " badge--danger" : ""}`;
      badge.textContent = { syncing: "indexing", empty: "no library", error: "index failed" }[
        status.state
      ];
      badge.title = status.error || "";
    } else if (status.errors) {
      // A link, not a tooltip: what it counts can be read.
      badge.className = "badge badge--danger badge--link";
      badge.textContent = plural(status.errors, "error");
      badge.href = `${base}/issues?severity=error`;
    } else if (status.warnings) {
      badge.className = "badge badge--warn badge--link";
      badge.textContent = plural(status.warnings, "warning");
      badge.href = `${base}/issues?severity=warning`;
    } else {
      badge.className = "badge badge--ok";
      badge.textContent = plural(status.parts, "part");
    }
  }

  async function loadStatus() {
    try {
      const status = await api("/api/v1/status");
      facets = status.facets;
      showIndexStatus(status);
    } catch {
      // Signed out, or the list has already said what went wrong.
      facets = { statuses: [], categories: [] };
      dom.indexStatus.hidden = true;
    }
    renderFilters();
  }

  // ----------------------------------------------------------------- start

  let searchTimer = null;
  dom.search.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(load, SEARCH_DELAY_MS);
  });

  if (dom.sessionAction) dom.sessionAction.addEventListener("click", onSessionAction);
  document.addEventListener("keydown", onKeyDown);

  bridge.onMessage((envelope) => {
    if (envelope.command === "NEW_SESSION") log("KiCad opened a session");
  });
  if (!bridge.isEmbedded()) {
    log("Not running inside KiCad: parts can be browsed here, but not placed.");
  }

  load();
  loadStatus();
})();
