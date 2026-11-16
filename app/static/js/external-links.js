/* Links that leave this app: a datasheet, a commit on the git host.
 *
 * In a browser they open in a new tab, which is what a link marked
 * target="_blank" does and needs no help. Inside KiCad's Remote Symbols panel
 * that is exactly the wrong thing. KiCad keeps web links in the panel -- it
 * loads them in the same view, and turns a request for a new window into one
 * -- and the panel has no back button, so a click leaves the part page for a
 * manufacturer's PDF with no way to return. A page cannot ask KiCad to use
 * the system browser instead: the commands it accepts are for signing in and
 * for downloading and placing parts, and there is none for this.
 *
 * So there, the link does not navigate. It offers the address, ready to paste
 * into a browser, and the panel stays where it is.
 */
import { el, button, inKicad } from "./app.js";

const NOTE_ID = "external-note";

function close() {
  const note = document.getElementById(NOTE_ID);
  if (note) note.remove();
}

/* Copy by the clipboard API where there is one, and by selecting the text
   as a person would where there is not. Reports whether it worked. */
async function copy(input) {
  input.focus();
  input.select();
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(input.value);
      return true;
    }
  } catch {
    // fall through to the older way
  }
  try {
    return document.execCommand("copy");
  } catch {
    return false;
  }
}

async function offer(address, what) {
  close();

  const note = el("div", "external-note");
  note.id = NOTE_ID;
  note.setAttribute("role", "status");

  const message = el("p", "external-note__text");
  const input = el("input", "external-note__address");
  input.type = "text";
  input.readOnly = true;
  input.value = address;
  input.setAttribute("aria-label", `${what} address`);
  // Selected on a click, so that it can be copied by hand if copying for
  // the person was not possible.
  input.addEventListener("focus", () => input.select());

  const again = button("button", "Copy", async () => {
    message.textContent = (await copy(input))
      ? `${what} address copied. Paste it into your browser.`
      : "Select the address and copy it, then paste it into your browser.";
  });

  const row = el("div", "external-note__row");
  row.append(input, again, button("button", "Close", close));
  note.append(message, row);
  document.body.append(note);

  message.textContent = (await copy(input))
    ? `${what} address copied. KiCad's panel cannot open a browser: paste it into one.`
    : "KiCad's panel cannot open a browser. Select the address, copy it, and paste it into one.";
}

document.addEventListener("click", (event) => {
  const link = event.target instanceof Element ? event.target.closest("a[data-external]") : null;
  if (!link || !inKicad()) return;

  // Not a click that navigates: the panel would take the address for a page
  // of its own and show it, with no way back.
  event.preventDefault();
  offer(link.href, link.dataset.external || "The");
});

// A key that closes it, like any other small popup.
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") close();
});
