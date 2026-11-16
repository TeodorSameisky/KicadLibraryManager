/* The catalog panel KiCad embeds.
 *
 * Three things shape it. It is drawn in a WebView that is often narrow, so the
 * layout folds rather than scrolling sideways. It is used from inside an
 * editor, so it can be driven from the keyboard. And placing a part can fail
 * for reasons the user can act on -- a missing footprint, a broken symbol --
 * so a failure is reported on the row that caused it, not in a log somebody
 * would have to think to open.
 */
import { api, el, link, button, stateBox, plural, base } from "./app.js";
import { bridge } from "./bridge.js";
import { place } from "./place.js";

const config = document.body.dataset;

const SEARCH_DELAY_MS = 150;
const INDEXING_POLL_MS = 2000;

const dom = {
  search: document.getElementById("search"),
  count: document.getElementById("count"),
  filters: document.getElementById("filters"),
  parameters: document.getElementById("parameters"),
  parametersSummary: document.getElementById("parameters-summary"),
  parametersList: document.getElementById("parameters-list"),
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
const filters = {
  status: params.get("status"),
  category: params.get("category"),
  // What is asked of each parameter of the category, by its name:
  // {op, value}. One condition each is all the panel asks; the API takes more.
  where: new Map(),
};
for (const condition of params.getAll("where")) {
  const found = /^(.*?)(>=|<=|=)(.*)$/.exec(condition);
  if (found && found[1].trim() && found[3].trim()) {
    filters.where.set(found[1].trim(), { op: found[2], value: found[3].trim() });
  }
}
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
   row of tabs: one click, and what is on can be read without opening
   anything. Categories are declared by the library and keep being added to,
   so they fold into a dropdown -- thirty of them in a row would push the
   parts they filter off the bottom of a panel that already shares its
   height with an editor. */
function renderFilters() {
  const controls = [];
  // With fewer than two choices there is nothing to narrow.
  if (facets.statuses.length > 1) controls.push(statusTabs());
  if (facets.categories.length > 1 || filters.category) controls.push(categorySelect());

  dom.filters.replaceChildren(...controls);
  dom.filters.hidden = controls.length === 0;
}

function statusTabs() {
  const tabs = el("div", "tabs");
  tabs.setAttribute("role", "group");
  tabs.setAttribute("aria-label", "Status");

  const options = [{ value: null, label: "All" }, ...facets.statuses];
  for (const option of options) {
    const on = filters.status === option.value;
    const tab = button(`tab${on ? " tab--on" : ""}`, option.label, () => {
      filters.status = option.value;
      renderFilters();
      load();
    });
    tab.setAttribute("aria-pressed", String(on));
    if (option.count !== undefined) tab.append(el("span", "tab__count", option.count));
    tabs.append(tab);
  }
  return tabs;
}

/* A native <select>, not a menu built here. The platform draws its popup,
   so it escapes the panel instead of being clipped by it, and it already
   scrolls, takes the arrow keys and jumps as you type. The counts ride in
   the option text because options cannot be styled; that is the price. */
function categorySelect() {
  const select = el("select");
  select.setAttribute("aria-label", "Category");
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
    // A resistance asked of capacitors would only empty the list.
    filters.where.clear();
    wrap.classList.toggle("select--on", Boolean(filters.category));
    load();
  });

  return wrap;
}

const isNarrowed = () =>
  Boolean(dom.search.value.trim() || filters.status || filters.category || filters.where.size);

function clearFilters() {
  filters.status = null;
  filters.category = null;
  filters.where.clear();
  dom.search.value = "";
  renderFilters();
  load();
}

// ----------------------------------------------------------- parameters

/* What the parts of the chosen category can be told apart by, each with a
   dropdown of the values it takes among the parts the other conditions
   leave. They come with every list, because what is left to choose depends
   on what has been chosen.

   A parameter whose values are quantities can also be asked to be at least
   or at most one of them: the sign before the dropdown, changed by pressing
   it. A voltage rating is wanted to be enough, not to be equal. */
const OPS = [
  { op: "=", sign: "=", says: "exactly" },
  { op: ">=", sign: "\u2265", says: "at least" },
  { op: "<=", sign: "\u2264", says: "at most" },
];
const opOf = (name) => OPS.find((o) => o.op === (filters.where.get(name) || {}).op) || OPS[0];

let controls = new Map(); // name -> {element, update}, for what is on screen

function parameterControl(name) {
  const wrap = el("span", "param");
  const select = el("select");
  select.setAttribute("aria-label", name);
  const box = el("span", "select");
  box.append(select);

  // Kept apart from the condition: the sign can be set before a value is.
  let chosen = opOf(name);
  const sign = button("param__op", chosen.sign, () => {
    chosen = OPS[(OPS.indexOf(chosen) + 1) % OPS.length];
    showSign();
    if (!select.value) return;
    filters.where.set(name, { op: chosen.op, value: select.value });
    describeParameters();
    load();
  });
  function showSign() {
    sign.textContent = chosen.sign;
    sign.title = `${name}: ${chosen.says} the value chosen. Press to change.`;
    sign.setAttribute("aria-label", `${name}: ${chosen.says}`);
  }
  showSign();

  select.addEventListener("change", () => {
    if (select.value) filters.where.set(name, { op: chosen.op, value: select.value });
    else filters.where.delete(name);
    wrap.classList.toggle("param--on", Boolean(select.value));
    box.classList.toggle("select--on", Boolean(select.value));
    describeParameters();
    load();
  });

  wrap.append(el("span", "param__name", name), sign, box);

  /* The values are replaced and the element is kept: rebuilding it would
     drop the focus of whoever just chose from it. */
  function update(parameter) {
    const asked = filters.where.get(name);
    if (!parameter.numeric) chosen = OPS[0];
    else if (asked) chosen = opOf(name);
    sign.hidden = !parameter.numeric;
    showSign();

    const options = [new Option("any", "")];
    for (const choice of parameter.choices) {
      options.push(new Option(`${choice.value}  (${choice.count})`, choice.value));
    }
    // A value asked for that the list does not offer -- a bound typed into
    // the address -- still has to be what the dropdown shows.
    if (asked && !parameter.choices.some((choice) => choice.value === asked.value)) {
      options.push(new Option(asked.value, asked.value));
    }
    select.replaceChildren(...options);
    select.value = asked ? asked.value : "";
    wrap.classList.toggle("param--on", Boolean(asked));
    box.classList.toggle("select--on", Boolean(asked));
  }

  return { element: wrap, update };
}

/* Said on the fold itself, so that a list narrowed by something folded away
   can still be explained. */
function describeParameters() {
  const asked = [...filters.where].map(
    ([name, { op, value }]) => `${name} ${OPS.find((o) => o.op === op).sign} ${value}`
  );
  dom.parametersSummary.textContent = asked.length
    ? `Parameters: ${asked.join(", ")}`
    : "Parameters";
  dom.parameters.classList.toggle("params--on", asked.length > 0);
}

function renderParameters(parameters) {
  const names = parameters.map((parameter) => parameter.name);
  // Built again only when the parameters themselves change: with another
  // category, not with every value chosen.
  if (names.join("\n") !== [...controls.keys()].join("\n")) {
    controls = new Map(names.map((name) => [name, parameterControl(name)]));
    dom.parametersList.replaceChildren(...[...controls.values()].map((c) => c.element));
  }
  for (const parameter of parameters) controls.get(parameter.name).update(parameter);
  describeParameters();
  dom.parameters.hidden = parameters.length === 0;
}

// ----------------------------------------------------------------- list

function show(...nodes) {
  rows = [];
  selected = -1;
  dom.results.replaceChildren(...nodes);
}

/* The words above the columns. Kept from assistive technology: what each
   row holds already says what it is. */
function listHead() {
  const head = el("div", "parts__head");
  head.setAttribute("aria-hidden", "true");
  head.append(
    el("span"),
    el("span", null, "Part"),
    el("span", null, "Description"),
    el("span", null, "Preferred source"),
    el("span")
  );
  return head;
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
  // Approved is the ordinary case and goes unmarked.
  if (part.status !== "approved") {
    const colour = part.status === "draft" ? "tag--warn" : "tag--bad";
    name.append(el("span", `tag ${colour}`, part.status));
  }

  const description = el("span", "part__desc", part.description);
  description.title = part.description;

  const others = part.mpn_count > 1 ? ` +${part.mpn_count - 1}` : "";
  const source = el(
    "span",
    "part__source",
    part.preferred_mpn ? part.preferred_mpn + others : "no source"
  );
  source.title = source.textContent;

  // Spans the row, so that a message has the width to say something useful.
  const note = el("div", "part__note");
  note.hidden = true;

  // Not called `place`: that is the function that does the placing.
  const placer = button("button", "Place", () => placePart(part.ipn, placer, note));
  placer.setAttribute("aria-label", `Place ${part.ipn} into KiCad`);
  const action = el("span", "part__action");
  action.append(placer);

  row.append(thumb, name, description, source, action, note);
  return { element: row, place: placer };
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
  renderParameters(data.parameters || []);
  if (indexing) {
    show(
      stateBox(
        "Indexing the library",
        "The repository is being cloned and read. The parts appear here when it is done."
      )
    );
    dom.count.textContent = "";
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
    show(listHead(), list);
    rows = built;
    dom.count.textContent = describe(data);
  }

  // The filters and the status line were drawn from an index that did not
  // exist yet; now that it does, they are fetched again.
  if (wasIndexing && !indexing) loadStatus();
  wasIndexing = indexing;
}

async function load() {
  const request = ++latestRequest;
  const query = new URLSearchParams({ q: dom.search.value });
  if (filters.status) query.set("status", filters.status);
  if (filters.category) query.set("category", filters.category);
  for (const [name, { op, value }] of filters.where) {
    query.append("where", `${name}${op}${value}`);
  }

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
            // A condition from the address that cannot be read is refused,
            // and the dropdowns that would take it back are not on screen.
            ...(filters.where.size ? [button("button", "Clear filters", clearFilters)] : []),
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

async function placePart(ipn, placer, note) {
  placer.disabled = true;
  placer.textContent = "Placing";
  setNote(note, null, "");

  try {
    await place(ipn, {
      log,
      onWarnings: (found) => setNote(note, "warn", found.join(" ")),
    });

    placer.textContent = "Placed";
    setTimeout(() => { placer.textContent = "Place"; }, 1500);
  } catch (error) {
    log(`Could not place ${ipn}: ${error.message}`);
    setNote(note, "error", error.message);
    placer.textContent = "Place";
  } finally {
    placer.disabled = false;
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
  const line = dom.indexStatus;
  line.hidden = false;
  line.className = "masthead__status";
  line.removeAttribute("href");
  line.title = "";

  if (status.state !== "ready") {
    line.textContent = { syncing: "indexing", empty: "no library", error: "index failed" }[
      status.state
    ];
    line.classList.toggle("is-bad", status.state === "error");
    line.title = status.error || "";
  } else if (status.errors) {
    // A link, not a tooltip: what it counts can be read.
    line.textContent = plural(status.errors, "error");
    line.classList.add("is-bad");
    line.href = `${base}/issues?severity=error`;
  } else if (status.warnings) {
    line.textContent = plural(status.warnings, "warning");
    line.classList.add("is-warn");
    line.href = `${base}/issues?severity=warning`;
  } else {
    line.textContent = plural(status.parts, "part");
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
