/* The form a category's template is edited with.
 *
 * Each field is a row: its name, whether it is required, the values it is
 * limited to, an example and a default. The rows are built here from the JSON
 * the page carries, and can be reordered, because the order is the order the
 * request form shows them in. Saving posts JSON to the endpoint a script
 * would use, and whatever the server finds wrong is shown as it says it.
 */
(() => {
  "use strict";

  const { api, el, link, button, base } = window.App;

  const form = document.getElementById("template-form");
  if (!form) return;

  const category = form.dataset.category;
  const initial = JSON.parse(document.getElementById("template-data").textContent);
  const dom = {
    fields: document.getElementById("template-fields"),
    value: document.getElementById("template-value"),
    submit: document.getElementById("template-submit"),
    remove: document.getElementById("template-remove"),
    result: document.getElementById("template-result"),
  };
  const endpoint = `/api/v1/templates/${encodeURIComponent(category)}`;

  function notice(kind, lines, actions = []) {
    const box = el("div", `inline-note${kind ? ` inline-note--${kind}` : ""}`);
    const text = el("div");
    for (const line of [].concat(lines)) text.append(el("div", null, line));
    box.append(text, ...actions);
    dom.result.replaceChildren(box);
  }

  function input(label, value, maxLength, className) {
    const node = el("input", className);
    node.type = "text";
    node.placeholder = label;
    node.value = value || "";
    node.maxLength = maxLength;
    node.autocomplete = "off";
    node.setAttribute("aria-label", label);
    return node;
  }

  function move(row, by) {
    const sibling = by < 0 ? row.previousElementSibling : row.nextElementSibling;
    if (!sibling) return;
    if (by < 0) dom.fields.insertBefore(row, sibling);
    else dom.fields.insertBefore(sibling, row);
  }

  function addField(spec = {}) {
    const row = el("div", "row row--spec");

    const name = input("Field name", spec.name, 60, "row__name");
    const required = el("label", "check");
    const box = el("input");
    box.type = "checkbox";
    box.checked = Boolean(spec.required);
    required.append(box, " required");

    const controls = el("span", "row__controls");
    const up = button("button", "Up", () => move(row, -1));
    const down = button("button", "Down", () => move(row, 1));
    const remove = button("button", "Remove", () => row.remove());
    up.setAttribute("aria-label", "Move this field up");
    down.setAttribute("aria-label", "Move this field down");
    remove.setAttribute("aria-label", "Remove this field");
    controls.append(up, down, remove);

    // Comma-separated, as a person would list them. A value that has to
    // contain a comma cannot be an option here; none of the usual ones do.
    const options = input("Allowed values (a, b, c)", (spec.options || []).join(", "), 600);
    const example = input("Example", spec.example, 100);
    const initialDefault = input("Starts as", spec.default, 200);
    const details = el("div", "row__details");
    details.append(options, example, initialDefault);

    row.append(name, required, controls, details);
    row.read = () => ({
      name: name.value.trim(),
      required: box.checked,
      options: options.value.split(",").map((option) => option.trim()).filter(Boolean),
      example: example.value.trim(),
      default: initialDefault.value.trim(),
    });
    row.nameInput = name;
    dom.fields.append(row);
    return row;
  }

  const read = () => ({
    value: dom.value.value.trim(),
    fields: [...dom.fields.children].map((row) => row.read()).filter((spec) => spec.name),
  });

  async function save() {
    dom.submit.disabled = true;
    notice(null, "Saving to the library");
    try {
      const saved = await api(endpoint, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(read()),
      });
      tell(saved, "Saved.");
    } catch (error) {
      // One line per thing to fix, as the server listed them.
      notice("bad", error.problems || error.message);
    } finally {
      dom.submit.disabled = false;
    }
  }

  /* What the template now means for the parts already there: the number that
     fall short is the work this change has made. */
  function tell(outcome, saved) {
    const actions = [link("/templates", "button", "Back to the templates")];
    if (outcome.falling_short) {
      notice(
        "warn",
        `${saved} ${outcome.falling_short} of the ${outcome.parts} existing parts in this category now fall short of it.`,
        [link("/issues?severity=warning", "button", "See which"), ...actions]
      );
    } else {
      notice(null, `${saved} Every existing part in this category meets it.`, actions);
    }
  }

  async function destroy() {
    try {
      await api(endpoint, { method: "DELETE" });
      window.location.assign(`${base}/templates`);
    } catch (error) {
      notice("bad", error.problems || error.message);
    }
  }

  for (const spec of initial.fields) addField(spec);
  dom.value.value = initial.value || "";

  document.getElementById("add-template-field").addEventListener("click", () => {
    addField().nameInput.focus();
  });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    save();
  });
  if (dom.remove) {
    dom.remove.addEventListener("click", () => {
      notice(
        "warn",
        `Remove the template for ${category}? Its parts are then asked for nothing.`,
        [
          button("button button--danger", "Remove it", destroy),
          button("button", "Keep it", () => dom.result.replaceChildren()),
        ]
      );
    });
  }
})();
