/* The part page's Place button.
 *
 * Shown only inside KiCad's panel: in an ordinary browser there is nothing to
 * place into, and the page is the same page a placed symbol's Datasheet field
 * opens, which is read in a browser.
 */
import { el, inKicad } from "./app.js";
import { place } from "./place.js";

function start() {
  const button = document.getElementById("place-button");
  const actions = document.getElementById("part-actions");
  const result = document.getElementById("place-result");
  if (!button || !inKicad()) return;

  actions.hidden = false;
  button.hidden = false;

  function say(kind, text) {
    if (!text) {
      result.replaceChildren();
      return;
    }
    const note = el("div", `inline-note inline-note--${kind}`, text);
    result.replaceChildren(note);
  }

  button.addEventListener("click", async () => {
    button.disabled = true;
    button.textContent = "Placing";
    say(null, "");

    try {
      const warnings = await place(button.dataset.ipn, {
        // Shown while it is on its way, not after: a missing footprint is
        // worth knowing before the part lands without a land pattern.
        onWarnings: (found) => say("warn", found.join(" ")),
      });
      button.textContent = "Placed";
      // A warning stays on screen; only the button goes back.
      if (!warnings.length) say(null, "");
      setTimeout(() => { button.textContent = "Place in KiCad"; }, 1500);
    } catch (error) {
      say("bad", error.message);
      button.textContent = "Place in KiCad";
    } finally {
      button.disabled = false;
    }
  });
}

start();
