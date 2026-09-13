/* Links a symbol's pins to the matching footprint pads.
 *
 * Both drawings are inlined into the page, so a pin and a pad sharing a number
 * can be highlighted together. Loaded through <img> they would each be a
 * separate document and unreachable from here.
 */
(function () {
  "use strict";

  var root = document.getElementById("previews");
  if (!root) return;

  function matching(number) {
    var escaped = window.CSS && CSS.escape ? CSS.escape(number) : number;
    return root.querySelectorAll('[data-pin="' + escaped + '"], [data-pad="' + escaped + '"]');
  }

  function highlight(number, on) {
    Array.prototype.forEach.call(matching(number), function (node) {
      node.classList.toggle("hl", on);
    });
  }

  Array.prototype.forEach.call(root.querySelectorAll("[data-pin], [data-pad]"), function (node) {
    var number = node.getAttribute("data-pin") || node.getAttribute("data-pad");
    if (!number) return;

    node.setAttribute("tabindex", "0");
    node.setAttribute("role", "img");
    node.setAttribute("aria-label", "Pin " + number);

    ["mouseenter", "focus"].forEach(function (event) {
      node.addEventListener(event, function () { highlight(number, true); });
    });
    ["mouseleave", "blur"].forEach(function (event) {
      node.addEventListener(event, function () { highlight(number, false); });
    });
  });
})();
