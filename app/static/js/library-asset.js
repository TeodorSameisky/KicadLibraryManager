/* Removing a symbol, footprint or 3D model that nothing uses.
 *
 * Asks first, with room to say why: the reason goes into the library's
 * history with the change.
 */
import { api, el, button, base } from "./app.js";

const control = document.getElementById("asset-remove");
const result = document.getElementById("asset-result");

function notice(kind, lines) {
  const box = el("div", `inline-note${kind ? ` inline-note--${kind}` : ""}`);
  for (const line of [].concat(lines)) box.append(el("div", null, line));
  result.replaceChildren(box);
}

async function remove(note, confirm) {
  confirm.disabled = true;
  notice(null, "Saving");
  const { kind, ref } = control.dataset;
  try {
    await api(`/api/v1/assets/${encodeURIComponent(kind)}/${encodeURIComponent(ref)}`, {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ note: note.value }),
    });
    window.location.assign(`${base}/library?kind=${encodeURIComponent(kind)}`);
  } catch (error) {
    notice("bad", error.problems || error.message);
  }
}

control.addEventListener("click", () => {
  const note = el("textarea");
  note.id = "remove-note";
  note.rows = 3;
  note.maxLength = 1000;
  note.placeholder = "Optional. Kept with the change in the library's history.";
  const label = el("label", null, `Remove ${control.dataset.ref}? Say why, if you like.`);
  label.htmlFor = note.id;

  const confirm = button("button button--danger", "Remove", () => remove(note, confirm));
  const actions = el("div", "actions");
  actions.append(confirm, button("button", "Cancel", () => result.replaceChildren()));

  const box = el("div", "note-box");
  box.append(label, note, actions);
  result.replaceChildren(box);
  note.focus();
});
