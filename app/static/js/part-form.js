/* The form a part is requested and edited with, and the buttons that approve
 * or retire one.
 *
 * The form's rows -- fields, manufacturer parts -- are built here, from the
 * JSON the page carries and the lists the library offers. Saving posts JSON
 * to the same endpoint a script would use, and whatever the server finds
 * wrong is shown as it says it, next to the button.
 */
(() => {
  "use strict";

  const { api, el, button, base } = window.App;

  function notice(target, kind, lines, actions = []) {
    const box = el("div", `inline-note${kind ? ` inline-note--${kind}` : ""}`);
    const text = el("div");
    for (const line of [].concat(lines)) text.append(el("div", null, line));
    box.append(text, ...actions);
    target.replaceChildren(box);
  }

  // ----------------------------------------------- approving and retiring

  /* Approving or retiring a part is a decision someone will want the reason
     for later, so the button asks first, and the reason -- if there is one --
     goes into the library's history with the change. */
  for (const control of document.querySelectorAll("[data-set-status]")) {
    const result = document.getElementById("status-result");

    async function submit(note, confirm) {
      confirm.disabled = true;
      notice(result, null, "Saving");
      try {
        await api(`/api/v1/parts/${encodeURIComponent(control.dataset.ipn)}/status`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ status: control.dataset.setStatus, note: note.value }),
        });
        window.location.reload();
      } catch (error) {
        notice(result, "bad", error.message);
      }
    }

    control.addEventListener("click", () => {
      const verb = control.textContent.trim();
      const note = el("textarea");
      note.id = "status-note";
      note.rows = 3;
      note.maxLength = 1000;
      note.placeholder = "Optional. Kept with the change in the library's history.";
      const label = el("label", null, `${verb} ${control.dataset.ipn}? Say why, if you like.`);
      label.htmlFor = note.id;

      const danger = control.classList.contains("button--danger");
      const confirm = button(`button${danger ? " button--danger" : ""}`, verb, () =>
        submit(note, confirm)
      );
      const actions = el("div", "actions");
      actions.append(confirm, button("button", "Cancel", () => result.replaceChildren()));

      const box = el("div", "note-box");
      box.append(label, note, actions);
      result.replaceChildren(box);
      note.focus();
    });
  }

  // -------------------------------------------------------------- the form

  const form = document.getElementById("part-form");
  if (!form) return;

  const initial = JSON.parse(document.getElementById("part-data").textContent);
  const dom = {
    category: document.getElementById("part-category"),
    description: document.getElementById("part-description"),
    symbol: document.getElementById("part-symbol"),
    footprint: document.getElementById("part-footprint"),
    template: document.getElementById("part-template"),
    fields: document.getElementById("part-fields"),
    sources: document.getElementById("part-sources"),
    note: document.getElementById("part-note"),
    submit: document.getElementById("part-submit"),
    result: document.getElementById("part-result"),
  };
  let known = new Map(); // manufacturer parts the library already has, by number
  let templates = {}; // by category code: the fields a part there carries

  function input(placeholder, value, maxLength) {
    const node = el("input");
    node.type = "text";
    node.placeholder = placeholder;
    node.value = value || "";
    node.maxLength = maxLength;
    node.autocomplete = "off";
    node.setAttribute("aria-label", placeholder);
    return node;
  }

  function removeButton(row) {
    const remove = button("button", "Remove", () => row.remove());
    remove.setAttribute("aria-label", "Remove this row");
    return remove;
  }

  function addField(name = "", value = "") {
    const row = el("div", "row");
    const nameInput = input("Name", name, 60);
    const valueInput = input("Value", value, 200);
    row.append(nameInput, valueInput, removeButton(row));
    row.read = () => [nameInput.value.trim(), valueInput.value.trim()];
    dom.fields.append(row);
    return row;
  }

  /* A field the category's template names. Its name is given, not typed, and
     where the template lists the values it may take, those are the choices. */
  function addTemplateField(expected, value) {
    const row = el("div", "row row--template");
    const label = el("label", `row__label${expected.required ? " is-required" : ""}`, expected.name);

    let control;
    if (expected.options.length) {
      control = el("select");
      control.append(new Option("", ""));
      for (const option of expected.options) control.append(new Option(option, option));
      /* A value the template no longer allows is shown rather than silently
         dropped; the server will say that it has to change. */
      if (value && !expected.options.includes(value)) control.append(new Option(value, value));
    } else {
      control = input(expected.example || expected.name, "", 200);
      control.removeAttribute("aria-label"); // it has a real label
    }
    control.value = value || "";
    control.id = `template-field-${dom.template.children.length}`;
    control.required = expected.required;
    label.htmlFor = control.id;

    row.append(label, control);
    row.read = () => [expected.name, control.value.trim()];
    dom.template.append(row);
  }

  const currentCategory = () => (dom.category ? dom.category.value : initial.category) || "";

  /* Every field with a name and something in it. One left empty is not a
     field the part carries. */
  function readFields() {
    const fields = {};
    for (const row of [...dom.template.children, ...dom.fields.children]) {
      const [name, value] = row.read();
      if (name && value) fields[name] = value;
    }
    return fields;
  }

  /* Lay the fields out for the chosen category: what its template names
     first, then whatever else was entered. Nothing typed is lost when the
     category changes -- a value keeps its name, wherever that now belongs. */
  function layOutFields(values) {
    const template = templates[currentCategory()];
    const rest = { ...values };
    dom.template.replaceChildren();
    dom.fields.replaceChildren();

    for (const expected of template ? template.fields : []) {
      addTemplateField(expected, expected.name in rest ? rest[expected.name] : expected.default);
      delete rest[expected.name];
    }
    for (const [name, value] of Object.entries(rest)) addField(name, value);

    // With no template to say what a part carries, Value is the usual start.
    if (!template && !dom.fields.children.length) addField("Value", "");
  }

  /* A manufacturer part. If the library already has it, that is all there is
     to say; if not, the row opens up to ask who makes it. */
  function addSource(source = {}) {
    const row = el("div", "row row--source");

    const mpn = input("Manufacturer part number", source.mpn, 120);
    mpn.setAttribute("list", "mpns");

    const preferred = el("label", "check");
    const radio = el("input");
    radio.type = "radio";
    radio.name = "preferred";
    radio.checked = Boolean(source.preferred);
    preferred.append(radio, " preferred");

    const about = el("span", "row__about");
    const manufacturer = input("Manufacturer", source.manufacturer, 120);
    const datasheet = input("Datasheet address", source.datasheet, 500);
    const lifecycle = el("select");
    lifecycle.setAttribute("aria-label", "Lifecycle");
    for (const state of ["active", "nrnd", "obsolete", "unknown"]) {
      lifecycle.append(new Option(state, state));
    }
    const details = el("div", "row__details");
    details.append(manufacturer, datasheet, lifecycle);

    function describe() {
      const existing = known.get(mpn.value.trim());
      details.hidden = Boolean(existing) || !mpn.value.trim();
      about.textContent = existing
        ? `${existing.manufacturer || "in the library"} · ${existing.lifecycle}`
        : mpn.value.trim() ? "new to the library" : "";
    }
    mpn.addEventListener("input", describe);

    row.append(mpn, preferred, about, removeButton(row), details);
    row.read = () => ({
      mpn: mpn.value.trim(),
      preferred: radio.checked,
      manufacturer: manufacturer.value.trim(),
      datasheet: datasheet.value.trim(),
      lifecycle: lifecycle.value,
    });
    row.describe = describe;
    dom.sources.append(row);
    describe();
    return row;
  }

  function fill(list, values) {
    document.getElementById(list).replaceChildren(...values.map((value) => new Option(value)));
  }

  async function loadChoices() {
    const offered = await api("/api/v1/library/choices");
    fill("symbols", offered.symbols);
    fill("footprints", offered.footprints);
    fill("mpns", offered.mpns.map((mpn) => mpn.mpn));
    known = new Map(offered.mpns.map((mpn) => [mpn.mpn, mpn]));
    templates = offered.templates || {};

    if (dom.category) {
      dom.category.append(new Option("Choose a category", ""));
      for (const category of offered.categories) {
        dom.category.append(new Option(`${category.code}  ${category.name}`, category.code));
      }
      dom.category.value = new URLSearchParams(window.location.search).get("category") || "";
      dom.category.addEventListener("change", () => layOutFields(readFields()));
    }
    layOutFields(readFields());
    for (const row of dom.sources.children) row.describe();
  }

  function read() {
    return {
      category: dom.category ? dom.category.value : "",
      description: dom.description.value.trim(),
      symbol: dom.symbol.value.trim(),
      footprint: dom.footprint.value.trim(),
      fields: readFields(),
      sources: [...dom.sources.children].map((row) => row.read()).filter((s) => s.mpn),
      note: dom.note.value,
    };
  }

  async function save() {
    dom.submit.disabled = true;
    notice(dom.result, null, "Saving to the library");
    try {
      const editing = Boolean(initial.ipn);
      const saved = await api(
        editing ? `/api/v1/parts/${encodeURIComponent(initial.ipn)}` : "/api/v1/parts",
        {
          method: editing ? "PUT" : "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(read()),
        }
      );
      window.location.assign(`${base}/ipn/${encodeURIComponent(saved.ipn)}`);
    } catch (error) {
      // One line per thing to fix, as the server listed them.
      notice(dom.result, "bad", error.problems || error.message);
      dom.submit.disabled = false;
    }
  }

  dom.description.value = initial.description || "";
  dom.symbol.value = initial.symbol || "";
  dom.footprint.value = initial.footprint || "";
  layOutFields(initial.fields || {});
  for (const source of initial.sources || []) addSource(source);
  if (!dom.sources.children.length) addSource();

  document.getElementById("add-field").addEventListener("click", () => {
    addField().querySelector("input").focus();
  });
  document.getElementById("add-source").addEventListener("click", () => {
    addSource().querySelector("input").focus();
  });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    save();
  });

  loadChoices().catch((error) => notice(dom.result, "bad", error.message));
})();
