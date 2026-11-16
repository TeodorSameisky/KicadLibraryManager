/* Everything in the library that does not line up.
 *
 * A real library always has some, so this is a working list and not an error
 * screen. It is grouped by the kind of problem: the same mistake made in two
 * hundred files is one thing to fix, not two hundred.
 */
import { api, el, link, button, stateBox, plural } from "./app.js";

const LIMIT = 1000;

const dom = {
  list: document.getElementById("issues"),
  count: document.getElementById("issue-count"),
  severity: document.getElementById("severity"),
};

// Linked to from the panel's status line and the status page, so that a reader
// arrives at the severity that sent them here.
let severity = new URLSearchParams(window.location.search).get("severity");
if (severity !== "error" && severity !== "warning") severity = null;

function renderFilter(totals) {
  const options = [
    { value: null, label: "All", count: totals.errors + totals.warnings },
    { value: "error", label: "Errors", count: totals.errors },
    { value: "warning", label: "Warnings", count: totals.warnings },
  ];

  dom.severity.replaceChildren(
    ...options.map((option) => {
      const on = severity === option.value;
      const tab = button(`tab${on ? " tab--on" : ""}`, option.label, () => {
        severity = option.value;
        load();
      });
      tab.setAttribute("aria-pressed", String(on));
      tab.append(el("span", "tab__count", option.count));
      return tab;
    })
  );
}

/* The kinds of problem the app itself can put right, and where. Offered to
   whoever may do it; everybody else reads what is wrong, as with the rest. */
const FIXES = {
  "manufacturer-alias": {
    path: "/manufacturers#written-otherwise",
    says: "Rewrite them on the manufacturers page",
  },
};
const canFix = dom.list.dataset.canFix === "true";

/* One group per kind of problem, errors first, then the most common. */
function grouped(issues) {
  const groups = new Map();
  for (const issue of issues) {
    const key = `${issue.severity}/${issue.kind}`;
    if (!groups.has(key)) {
      groups.set(key, { kind: issue.kind, severity: issue.severity, items: [] });
    }
    groups.get(key).items.push(issue);
  }
  return [...groups.values()].sort((a, b) =>
    a.severity === b.severity
      ? b.items.length - a.items.length
      : a.severity === "error" ? -1 : 1
  );
}

function renderGroup(group) {
  const isError = group.severity === "error";
  const box = el("details", "issue-group");
  // Errors open, warnings folded: one is a repair list, the other advice.
  box.open = isError;

  const head = el("summary", "issue-group__head");
  head.append(
    el("span", `tag ${isError ? "tag--bad" : "tag--warn"}`, group.severity),
    el("span", "issue-group__kind", group.kind),
    el("span", "issue-group__count", group.items.length)
  );

  const body = el("tbody");
  for (const issue of group.items) {
    const row = el("tr");
    // Which repository, then where in it: what a maintainer opens.
    const where = [issue.source, issue.path].filter(Boolean).join(": ");
    row.append(el("td", "issue__message", issue.message), el("td", "issue__path", where));
    body.append(row);
  }

  const table = el("table", "table");
  table.append(body);
  const scroll = el("div", "table-scroll");
  scroll.append(table);

  box.append(head, scroll);
  const fix = FIXES[group.kind];
  if (fix && canFix) {
    const action = el("p", "issue-group__fix");
    action.append(link(fix.path, "button", fix.says));
    box.append(action);
  }
  return box;
}

function render(data) {
  renderFilter(data);

  if (!data.issues.length) {
    dom.count.hidden = true;
    dom.list.replaceChildren(
      severity
        ? stateBox(`No ${severity}s`)
        : stateBox(
            "Everything lines up",
            "Every symbol, footprint and 3D model the library refers to was found."
          )
    );
    return;
  }

  dom.list.replaceChildren(...grouped(data.issues).map(renderGroup));

  dom.count.hidden = false;
  dom.count.textContent =
    data.returned < data.total
      ? `${data.returned} of ${data.total}`
      : plural(data.total, "issue");
}

async function load() {
  const query = new URLSearchParams({ limit: LIMIT });
  if (severity) query.set("severity", severity);

  try {
    render(await api(`/api/v1/issues?${query}`));
  } catch (error) {
    dom.count.hidden = true;
    dom.list.replaceChildren(
      error.unauthorized
        ? stateBox("Sign in to read this library", "Reload this page to sign in.")
        : stateBox("The issues could not be loaded", error.message)
    );
  }
}

load();
