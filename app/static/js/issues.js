/* Everything in the library that does not resolve.
 *
 * A real library always has some, so this is a working list rather than an
 * error screen: grouped by the kind of problem, because the same mistake
 * repeated across two hundred files is one thing to fix, not two hundred.
 */
(function () {
  "use strict";

  var App = window.App;
  var LIMIT = 1000;

  var els = {
    results: document.getElementById("issues"),
    count: document.getElementById("issue-count"),
    filters: document.getElementById("severity"),
  };

  // Deep-linked from the panel's status badge, so a reader lands on the
  // severity that sent them here.
  var severity = new URLSearchParams(window.location.search).get("severity");
  if (severity !== "error" && severity !== "warning") severity = null;

  function renderFilters(summary) {
    App.clear(els.filters);

    [
      { value: null, label: "All", count: summary.errors + summary.warnings },
      { value: "error", label: "Errors", count: summary.errors },
      { value: "warning", label: "Warnings", count: summary.warnings },
    ].forEach(function (option) {
      var active = severity === option.value;
      var chip = App.button("chip" + (active ? " chip--on" : ""), option.label, function () {
        severity = option.value;
        load();
      });
      chip.setAttribute("aria-pressed", active ? "true" : "false");
      chip.appendChild(App.el("span", "chip__count", option.count));
      els.filters.appendChild(chip);
    });
  }

  /* Group by kind. "12 symbols reference a footprint no source provides" is
     one decision; the same line printed twelve times is twelve readings of
     the same decision. */
  function group(issues) {
    var order = [];
    var byKind = {};

    issues.forEach(function (issue) {
      var key = issue.severity + "/" + issue.kind;
      if (!byKind[key]) {
        byKind[key] = { kind: issue.kind, severity: issue.severity, items: [] };
        order.push(key);
      }
      byKind[key].items.push(issue);
    });

    return order
      .map(function (key) { return byKind[key]; })
      .sort(function (a, b) {
        if (a.severity !== b.severity) return a.severity === "error" ? -1 : 1;
        return b.items.length - a.items.length;
      });
  }

  function renderGroup(entry) {
    var box = App.el("details", "issue-group");
    // Errors open, warnings folded: one is a repair list, the other is advice.
    box.open = entry.severity === "error";

    var head = App.el("summary", "issue-group__head");
    head.appendChild(
      App.el("span", "badge " + (entry.severity === "error" ? "badge--danger" : "badge--warn"),
        entry.severity)
    );
    head.appendChild(App.el("span", "issue-group__kind", entry.kind));
    head.appendChild(App.el("span", "issue-group__count", entry.items.length));
    box.appendChild(head);

    var table = App.el("table", "table table--issues");
    var body = App.el("tbody");

    entry.items.forEach(function (issue) {
      var row = App.el("tr");
      row.appendChild(App.el("td", "issue__message", issue.message));
      // Repository-relative: what a maintainer opens, and it says nothing
      // about where the container keeps its clones.
      row.appendChild(App.el("td", "issue__path mono", issue.path || ""));
      body.appendChild(row);
    });

    table.appendChild(body);

    var scroll = App.el("div", "table-scroll");
    scroll.appendChild(table);
    box.appendChild(scroll);
    return box;
  }

  function render(data) {
    renderFilters(data);

    if (!data.issues.length) {
      els.count.textContent = "";
      els.results.replaceChildren(
        App.stateBox(
          severity ? "No " + severity + "s" : "Everything resolves",
          "Every symbol, footprint and 3D model the library references was found."
        )
      );
      return;
    }

    var groups = group(data.issues);
    var list = App.el("div", "issue-groups");
    groups.forEach(function (entry) { list.appendChild(renderGroup(entry)); });
    els.results.replaceChildren(list);

    els.count.textContent =
      data.returned < data.total
        ? data.returned + " of " + data.total
        : App.plural(data.total, "issue");
  }

  function load() {
    var path = "/api/v1/issues?limit=" + LIMIT + (severity ? "&severity=" + severity : "");
    App.api(path)
      .then(render)
      .catch(function (err) {
        els.results.replaceChildren(
          App.stateBox(
            err.unauthorized ? "Sign in to read this library" : "Could not load issues",
            err.unauthorized ? "Open the catalog in KiCad and sign in there." : err.message
          )
        );
      });
  }

  load();
})();
