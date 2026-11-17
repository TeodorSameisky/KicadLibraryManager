/* Uploading symbols, footprints and 3D models.
 *
 * As soon as files are chosen they are sent to be checked, and what they
 * would add is shown -- drawn, with where each goes, what it replaces and what
 * is wrong -- before anything is changed. Adding them sends the same files
 * again, to the endpoint a script would use.
 */
import { api, el, link } from "./app.js";

const form = document.getElementById("upload-form");
const dom = {
  library: document.getElementById("upload-library"),
  files: document.getElementById("upload-files"),
  plan: document.getElementById("upload-plan"),
  confirm: document.getElementById("upload-confirm"),
  note: document.getElementById("upload-note"),
  submit: document.getElementById("upload-submit"),
  result: document.getElementById("upload-result"),
};
const LABELS = { symbol: "Symbol", footprint: "Footprint", model: "3D model" };

let checked = null; // the last answer, for the files and library as they are now
let asked = 0;
let timer = null;

function notice(target, kind, lines) {
  const box = el("div", `inline-note${kind ? ` inline-note--${kind}` : ""}`);
  const text = el("div");
  for (const line of [].concat(lines)) text.append(el("div", null, line));
  box.append(text);
  target.replaceChildren(box);
}

/* A file's bytes as base64: a STEP model is not always text. */
function encoded(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).replace(/^data:[^,]*,/, ""));
    reader.onerror = () => reject(new Error(`${file.name} could not be read.`));
    reader.readAsDataURL(file);
  });
}

async function body() {
  const files = [];
  for (const file of dom.files.files) files.push({ name: file.name, content: await encoded(file) });
  return { library: dom.library.value.trim(), files };
}

/* A drawing the server made. Parsed as SVG, not put in as markup. */
function drawing(svg) {
  const parsed = new DOMParser().parseFromString(svg, "image/svg+xml");
  const root = parsed.documentElement;
  if (root.nodeName !== "svg") return null;
  for (const script of root.querySelectorAll("script, foreignObject")) script.remove();
  return document.importNode(root, true);
}

function showPlan(plan) {
  const parts = [];

  if (plan.problems.length) {
    const box = el("div");
    notice(box, "bad", plan.problems);
    parts.push(box);
  }
  if (plan.notes.length) {
    const box = el("div");
    notice(box, "warn", plan.notes);
    parts.push(box);
  }

  if (plan.items.length) {
    const table = el("table", "table");
    const head = el("tr");
    for (const name of ["", "Name", "From", "Goes to"]) {
      const th = el("th", null, name);
      th.scope = "col";
      head.append(th);
    }
    table.append(el("thead"), el("tbody"));
    table.tHead.append(head);
    for (const item of plan.items) {
      const row = el("tr");
      const what = el("td");
      what.append(LABELS[item.kind]);
      if (item.replaces) {
        what.append(" ", el("span", "tag tag--warn", "replaces"));
      }
      const name = el("td");
      name.append(el("span", "mono", item.ref));
      if (item.replaces && item.used_by.length) {
        name.append(el("div", "muted small", `used by ${item.used_by.join(", ")}`));
      }
      row.append(what, name, el("td", "muted", item.source_file), el("td", "mono small", item.path));
      table.tBodies[0].append(row);
    }
    const scroll = el("div", "table-scroll");
    scroll.append(table);
    parts.push(scroll);

    const drawn = plan.items.filter((item) => item.drawing);
    if (drawn.length) {
      const sheet = el("div", "sheet");
      for (const item of drawn) {
        const figure = el("figure", "sheet__cell");
        const picture = el("div", "sheet__drawing");
        const svg = drawing(item.drawing);
        if (svg) picture.append(svg);
        figure.append(el("figcaption", null, `${LABELS[item.kind]} ${item.ref}`), picture);
        sheet.append(figure);
      }
      parts.push(sheet);
    }
  }

  dom.plan.replaceChildren(...parts);
  const ready = !plan.problems.length && plan.items.length > 0;
  dom.confirm.hidden = !ready;
  dom.submit.disabled = !ready;
  dom.submit.textContent = plan.replaces ? "Replace and add to the library" : "Add to the library";
}

async function check() {
  const mine = ++asked;
  checked = null;
  dom.submit.disabled = true;
  dom.result.replaceChildren();
  if (!dom.files.files.length) {
    dom.plan.replaceChildren();
    dom.confirm.hidden = true;
    return;
  }
  notice(dom.plan, null, "Reading the files");
  try {
    const plan = await api("/api/v1/assets/check", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(await body()),
    });
    if (mine !== asked) return;
    checked = plan;
    showPlan(plan);
  } catch (error) {
    if (mine === asked) notice(dom.plan, "bad", error.problems || error.message);
  }
}

function checkSoon() {
  clearTimeout(timer);
  timer = setTimeout(check, 400);
}

function done(plan) {
  const lines = el("div");
  lines.append(el("div", null, `Added to ${plan.library}.`));
  const links = el("div", "actions");
  for (const item of plan.items) {
    links.append(link(`/library/${item.kind}/${encodeURIComponent(item.ref)}`, "mono", item.ref));
  }
  const symbol = plan.items.find((item) => item.kind === "symbol");
  const footprint = plan.items.find((item) => item.kind === "footprint");
  if (symbol || footprint) {
    const query = new URLSearchParams();
    if (symbol) query.set("symbol", symbol.ref);
    if (footprint) query.set("footprint", footprint.ref);
    links.append(link(`/parts/new?${query}`, "button", "Request a part with these"));
  }
  const box = el("div", "inline-note");
  box.append(lines, links);
  dom.result.replaceChildren(box);
}

async function save() {
  if (!checked) return;
  dom.submit.disabled = true;
  notice(dom.result, null, "Saving to the library");
  try {
    const plan = await api("/api/v1/assets", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...(await body()), replace: checked.replaces, note: dom.note.value }),
    });
    dom.files.value = "";
    dom.plan.replaceChildren();
    dom.confirm.hidden = true;
    dom.note.value = "";
    checked = null;
    done(plan);
  } catch (error) {
    notice(dom.result, "bad", error.problems || error.message);
    dom.submit.disabled = false;
  }
}

dom.files.addEventListener("change", check);
dom.library.addEventListener("input", checkSoon);
form.addEventListener("submit", (event) => {
  event.preventDefault();
  save();
});
