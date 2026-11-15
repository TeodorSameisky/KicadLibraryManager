/* Narrowing the product tree by what is typed and by a tag.
 *
 * The whole tree is on the page, so this only hides what does not match. A
 * family is open while a filter is on, so that what matched can be seen, and
 * goes back to how the reader left it when the filter is cleared.
 */
(() => {
  "use strict";

  const search = document.getElementById("product-search");
  const tag = document.getElementById("product-tag");
  const count = document.getElementById("product-count");
  const none = document.getElementById("product-none");
  const root = document.getElementById("products");
  if (!search || !root) return;

  const families = [...root.querySelectorAll("[data-family]")];
  const total = Number(root.dataset.total);
  const opened = new Map(); // how the reader left each family, while a filter overrides it
  const everything = count.textContent.trim().replace(/\s+/g, " ");

  function apply() {
    const words = search.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
    const wanted = tag ? tag.value : "";
    const filtering = words.length > 0 || wanted !== "";
    let shown = 0;

    for (const family of families) {
      let any = false;
      for (const row of family.querySelectorAll("tr.device")) {
        const tags = row.dataset.tags ? row.dataset.tags.split("|") : [];
        const match =
          (!wanted || tags.includes(wanted)) &&
          words.every((word) => row.dataset.search.includes(word));
        row.hidden = !match;
        any = any || match;
        if (match) shown += 1;
      }
      family.hidden = !any;

      if (filtering) {
        if (!opened.has(family)) opened.set(family, family.open);
        family.open = true;
      } else if (opened.has(family)) {
        family.open = opened.get(family);
      }
    }
    if (!filtering) opened.clear();

    if (tag) tag.parentElement.classList.toggle("select--on", wanted !== "");
    none.hidden = shown > 0;
    count.textContent = filtering
      ? `${shown} of ${total} device${total === 1 ? "" : "s"}`
      : everything;
  }

  search.addEventListener("input", apply);
  if (tag) tag.addEventListener("change", apply);
  // Enter in the search box is not a request to leave the page.
  document.getElementById("product-filter").addEventListener("submit", (e) => e.preventDefault());
})();
