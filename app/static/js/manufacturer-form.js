/* The form a manufacturer is added to the list with, or changed.
 *
 * A name, and a row for each other way it gets written. The rows are built
 * here from the JSON the page carries -- which may already hold a spelling the
 * worklist sent along, not yet saved. Saving sends JSON to the endpoint a
 * script would use, and whatever the server finds wrong is shown as it says
 * it.
 */
import { api, el, link, button, plural, base } from "./app.js";

function start() {
  const form = document.getElementById("manufacturer-form");
  if (!form) return;

  const initial = JSON.parse(document.getElementById("manufacturer-data").textContent);
  const dom = {
    name: document.getElementById("manufacturer-name"),
    aliases: document.getElementById("manufacturer-aliases"),
    note: document.getElementById("manufacturer-note"),
    submit: document.getElementById("manufacturer-submit"),
    remove: document.getElementById("manufacturer-remove"),
    result: document.getElementById("manufacturer-result"),
  };
  const endpoint = "/api/v1/manufacturers";

  function notice(kind, lines, actions = []) {
    const box = el("div", `inline-note${kind ? ` inline-note--${kind}` : ""}`);
    const text = el("div");
    for (const line of [].concat(lines)) text.append(el("div", null, line));
    box.append(text, ...actions);
    dom.result.replaceChildren(box);
  }

  function addAlias(value = "") {
    const row = el("div", "row");
    const input = el("input");
    input.type = "text";
    input.placeholder = "Another way it is written";
    input.value = value;
    input.maxLength = 120;
    input.autocomplete = "off";
    input.setAttribute("aria-label", "Another way it is written");

    const remove = button("button", "Remove", () => row.remove());
    remove.setAttribute("aria-label", "Remove this spelling");

    row.append(input, remove);
    row.read = () => input.value.trim();
    row.input = input;
    dom.aliases.append(row);
    return row;
  }

  const read = () => ({
    was: initial.was,
    name: dom.name.value.trim(),
    aliases: [...dom.aliases.children].map((row) => row.read()).filter(Boolean),
    note: dom.note.value,
  });

  /* What is left to do after this change: the names still not on the list are
     the reason the list is kept. */
  function tell(after, saved) {
    const waiting = after.unlisted.reduce((sum, found) => sum + found.parts, 0);
    const back = link("/manufacturers", "button", "Back to the manufacturers");
    if (waiting) {
      notice(
        "warn",
        `${saved} ${plural(waiting, "manufacturer part")} still ${waiting === 1 ? "names" : "name"} somebody who is not in the list.`,
        [back]
      );
    } else {
      notice(null, `${saved} Every manufacturer part now names somebody in the list.`, [back]);
    }
  }

  async function save() {
    dom.submit.disabled = true;
    notice(null, "Saving to the library");
    try {
      const sent = read();
      const after = await api(endpoint, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(sent),
      });
      // It is listed under this name from now on: a second save changes it,
      // and does not add it again.
      initial.was = sent.name;
      tell(after, "Saved.");
    } catch (error) {
      // One line per thing to fix, as the server listed them.
      notice("bad", error.problems || error.message);
    } finally {
      dom.submit.disabled = false;
    }
  }

  async function destroy() {
    try {
      // The note above the buttons goes with the removal, as with a save.
      await api(endpoint, {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: initial.was, note: dom.note.value }),
      });
      window.location.assign(`${base}/manufacturers`);
    } catch (error) {
      notice("bad", error.problems || error.message);
    }
  }

  dom.name.value = initial.name || "";
  for (const alias of initial.aliases) addAlias(alias);

  document.getElementById("add-manufacturer-alias").addEventListener("click", () => {
    addAlias().input.focus();
  });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    save();
  });
  if (dom.remove) {
    dom.remove.addEventListener("click", () => {
      notice(
        "warn",
        `Take ${initial.was} off the list? The manufacturer parts that name it are not changed: they then name somebody the list does not know. What is written in the note above is kept with the removal.`,
        [
          button("button button--danger", "Remove it", destroy),
          button("button", "Keep it", () => dom.result.replaceChildren()),
        ]
      );
    });
  }
}

start();
