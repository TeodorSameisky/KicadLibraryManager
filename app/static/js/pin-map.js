/* Links a symbol's pins to the footprint pads they map to.
 *
 * Both drawings are inlined into the page, so a pin and a pad with the same
 * number can be lit together. Loaded through <img>, each would be a document
 * of its own and out of reach from here.
 */
(() => {
  "use strict";

  const previews = document.getElementById("previews");
  if (!previews) return;

  // By number: every pin and pad that carries it.
  const byNumber = new Map();
  for (const node of previews.querySelectorAll("[data-pin], [data-pad]")) {
    const number = node.dataset.pin || node.dataset.pad;
    if (!byNumber.has(number)) byNumber.set(number, []);
    byNumber.get(number).push(node);
  }

  for (const [number, nodes] of byNumber) {
    const highlight = (on) => nodes.forEach((node) => node.classList.toggle("hl", on));

    for (const node of nodes) {
      const kind = node.dataset.pin !== undefined ? "Pin" : "Pad";
      // Reachable from the keyboard too, not only by hovering.
      node.setAttribute("tabindex", "0");
      node.setAttribute("role", "img");
      node.setAttribute("aria-label", `${kind} ${number}`);

      node.addEventListener("mouseenter", () => highlight(true));
      node.addEventListener("mouseleave", () => highlight(false));
      node.addEventListener("focus", () => highlight(true));
      node.addEventListener("blur", () => highlight(false));
    }
  }
})();
