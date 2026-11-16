/* The manufacturers page: rewriting the manufacturer parts that name a listed
 * manufacturer by another spelling.
 *
 * Each such manufacturer has a button, and there is one for all of them. A
 * press is one commit to the library, in the name of whoever pressed it; what
 * was rewritten is said under the table, and the row is taken away, because
 * there is nothing left in it to do. The list itself is not changed: a
 * spelling it has stays in it, and is recognised the next time it is used.
 */
import { api, el, plural } from "./app.js";

function start() {
  const section = document.getElementById("written-otherwise");
  if (!section) return;

  const result = document.getElementById("rewrite-result");
  const everything = section.querySelector("[data-rewrite-all]");
  const buttons = () => [...section.querySelectorAll("[data-rewrite], [data-rewrite-all]")];

  function notice(kind, lines) {
    const box = el("div", `inline-note${kind ? ` inline-note--${kind}` : ""}`);
    const text = el("div");
    for (const line of [].concat(lines)) text.append(el("div", null, line));
    box.append(text);
    result.replaceChildren(box);
  }

  async function rewrite(name, pressed) {
    for (const button of buttons()) button.disabled = true;
    notice(null, "Rewriting in the library");
    try {
      const after = await api("/api/v1/manufacturers/rewrite", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(name === null ? {} : { name }),
      });

      // What is left to do is what the server says is left, not what this
      // page remembers.
      const left = new Set(after.written_otherwise.map((found) => found.name));
      for (const row of section.querySelectorAll("tr[data-manufacturer]")) {
        if (!left.has(row.dataset.manufacturer)) row.remove();
      }
      const waiting = after.written_otherwise.reduce((sum, found) => sum + found.parts, 0);
      section.querySelector(".table-scroll").hidden = left.size === 0;
      if (everything) {
        everything.hidden = left.size < 2;
        everything.textContent = `Rewrite all ${plural(waiting, "manufacturer part")}`;
      }

      notice(null, [
        `Rewrote ${plural(after.rewritten.length, "manufacturer part")}: ${after.rewritten.join(", ")}.`,
        waiting
          ? `${plural(waiting, "manufacturer part")} still ${waiting === 1 ? "names" : "name"} a listed manufacturer another way.`
          : "Every manufacturer part now names its maker as the list does.",
      ]);
    } catch (error) {
      notice("bad", error.problems || error.message);
      pressed.focus();
    } finally {
      for (const button of buttons()) button.disabled = false;
    }
  }

  for (const button of section.querySelectorAll("[data-rewrite]")) {
    button.addEventListener("click", () => rewrite(button.dataset.rewrite, button));
  }
  if (everything) everything.addEventListener("click", () => rewrite(null, everything));
}

start();
