/* What suppliers say about a part's manufacturer parts: stock, status, price.
 *
 * Asked for once the page is on screen, and not while it is being made: a
 * supplier takes its time, and the page should not wait on somebody else's
 * server. One row for each manufacturer part and supplier. What a supplier
 * says is shown beside what the library says, never in place of it.
 */
import { api, el } from "./app.js";

// What one costs when buying this many: what someone building one board, a
// handful, or a small run would pay.
const QUANTITIES = [1, 10, 100, 1000];

function start() {
  const mount = document.getElementById("supply");
  if (!mount) return;

  /* The last price break reached at a quantity, or null below the first. */
  function priceAt(prices, quantity) {
    let found = null;
    for (const step of prices) {
      if (step.quantity <= quantity && (!found || step.quantity > found.quantity)) found = step;
    }
    return found ? found.unit_price : null;
  }

  function money(amount, currency) {
    if (amount === null) return "—";
    try {
      // Small parts cost fractions of a cent; two decimals would say 0.00.
      return new Intl.NumberFormat(undefined, {
        style: "currency",
        currency,
        minimumFractionDigits: 2,
        maximumFractionDigits: 4,
      }).format(amount);
    } catch {
      return `${amount} ${currency || ""}`.trim();
    }
  }

  const day = (seconds) => new Date(seconds * 1000).toLocaleDateString();

  function cell(className, ...children) {
    const node = el("td", className);
    node.append(...children);
    return node;
  }

  /* Why a supplier says nothing about a part, in a row of its own width. */
  const SILENT = {
    "not-found": "does not list it",
    "over-limit": "not asked: today's calls to it are used up",
    failed: "did not answer",
  };

  function supplierCell(answer) {
    if (!answer.url) return cell(null, answer.name);
    const link = el("a", null, answer.name);
    link.href = answer.url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    // Inside KiCad's panel this offers the address to copy instead of
    // navigating the panel away.
    link.dataset.external = `The ${answer.name} page`;
    const node = cell(null, link);
    if (answer.sku) node.append(el("span", "small muted block mono", answer.sku));
    return node;
  }

  function statusCell(answer) {
    const said = answer.status || answer.lifecycle || "—";
    // Marked only where the supplier says the part stands worse than the
    // library does: that is the reason to look.
    const node = cell(null, answer.warns ? el("span", "tag tag--warn", said) : said);
    if (answer.stale) node.append(el("span", "small muted block", `as of ${day(answer.asked_at)}`));
    return node;
  }

  function row(source, answer, first) {
    const line = el("tr");
    line.append(cell(null, first ? el("span", "mono", source.mpn) : ""));

    if (answer.state !== "found") {
      const why = cell("muted", `${answer.name} ${SILENT[answer.state] || "says nothing"}`);
      why.colSpan = QUANTITIES.length + 4;
      line.append(why);
      return line;
    }

    const stock = answer.stock === null ? "—" : answer.stock.toLocaleString();
    line.append(supplierCell(answer), cell("num", stock), statusCell(answer));
    for (const quantity of QUANTITIES) {
      line.append(cell("num", money(priceAt(answer.prices, quantity), answer.currency)));
    }
    line.append(cell("num", answer.lead_weeks === null ? "—" : `${answer.lead_weeks} wk`));
    return line;
  }

  function table(sources) {
    const head = el("tr");
    for (const [text, className] of [
      ["Manufacturer part", null],
      ["Supplier", null],
      ["In stock", "num"],
      ["Status", null],
      ...QUANTITIES.map((quantity) => [`at ${quantity.toLocaleString()}`, "num"]),
      ["Lead time", "num"],
    ]) {
      const heading = el("th", className, text);
      heading.scope = "col";
      head.append(heading);
    }
    const thead = el("thead");
    thead.append(head);

    const body = el("tbody");
    for (const source of sources) {
      source.answers.forEach((answer, index) => body.append(row(source, answer, index === 0)));
    }

    const grid = el("table", "table");
    grid.append(thead, body);
    const scroll = el("div", "table-scroll");
    scroll.append(grid);
    return scroll;
  }

  async function load() {
    try {
      const supply = await api(`/api/v1/parts/${encodeURIComponent(mount.dataset.ipn)}/supply`);
      const asked = supply.sources.filter((source) => source.answers.length);
      if (!asked.length) {
        mount.replaceChildren(el("p", "muted", "No supplier was asked."));
        return;
      }
      mount.replaceChildren(
        table(asked),
        el("p", "hint", "Each price is for one piece when buying that many. Asked once a day.")
      );
    } catch (error) {
      mount.replaceChildren(el("p", "muted", `The suppliers could not be asked: ${error.message}`));
    }
  }

  load();
}

start();
