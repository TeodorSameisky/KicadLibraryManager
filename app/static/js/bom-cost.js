/* The cost page: working the costing out again as the number of boards
 * changes, and asking the suppliers about a BOM's parts, a piece at a time.
 *
 * Both put a fresh costing on the page the same way: the page is fetched for
 * the number on screen and its costing put in place of the one shown. The
 * server works it out; nothing about prices is worked out here.
 *
 * The number of boards asks nobody, so it is acted on as it is typed.
 *
 * Asking does cost calls. A supplier allows only so many a minute, and a BOM
 * can have hundreds of manufacturer parts. So the server asks for a few
 * seconds at a time and says what is still to be asked; this asks it again
 * while that goes down. It stops when everything is asked, when someone says
 * to, or when a round gets no further -- a supplier's calls are used up for
 * now, or it is not answering -- and says so.
 */
import { api, plural } from "./app.js";

// Long enough not to work it out for each digit of "1000".
const TYPING_DELAY_MS = 300;

const boardsInput = document.getElementById("boards");

/* The number of boards on screen, as the server will take it: whole, at
   least one. Null while what is typed is not a number yet. */
function boards() {
  const typed = Number.parseInt(boardsInput.value, 10);
  if (!Number.isFinite(typed)) return null;
  return Math.max(1, Math.min(typed, Number(boardsInput.max) || typed));
}

/* The page's address for a number of boards: what a link to it, a reload,
   and the fetch below all go by. */
function addressFor(count) {
  const url = new URL(window.location.href);
  url.searchParams.set("boards", String(count));
  return url;
}

let latest = 0;

/* The costing for the number on screen, put in place of the one shown.
   An answer to a number that has since been replaced is not shown. */
async function refresh() {
  const count = boards();
  if (count === null) return;
  const request = ++latest;
  const url = addressFor(count);
  const response = await fetch(url, { credentials: "same-origin" });
  if (!response.ok || request !== latest) return;
  const page = new DOMParser().parseFromString(await response.text(), "text/html");
  const fresh = page.getElementById("costing");
  if (!fresh || request !== latest) return;
  document.getElementById("costing").replaceWith(fresh);
  window.history.replaceState(null, "", url);
}

// ---------------------------------------------------------------- boards

const form = document.getElementById("cost-boards");
let typing = null;
boardsInput.addEventListener("input", () => {
  clearTimeout(typing);
  typing = setTimeout(refresh, TYPING_DELAY_MS);
});
// Enter works it out at once, and does not reload the page.
form.addEventListener("submit", (event) => {
  event.preventDefault();
  clearTimeout(typing);
  refresh();
});

// ---------------------------------------------------------------- asking

const box = document.getElementById("cost-ask");
if (box) {
  const begin = document.getElementById("cost-ask-start");
  const stop = document.getElementById("cost-ask-stop");
  const progress = document.getElementById("cost-ask-progress");
  let stopping = false;

  async function ask() {
    begin.hidden = true;
    stop.hidden = false;
    stopping = false;
    let left = Number(box.dataset.unasked);
    progress.textContent = "Asking the suppliers";

    try {
      while (!stopping) {
        const costing = await api(
          `/api/v1/boms/${box.dataset.bomId}/cost/ask?boards=${boards() ?? 1}`,
          { method: "POST" }
        );
        await refresh();
        const asked = costing.questions - costing.unasked;
        progress.textContent = `${asked} of ${costing.questions} answers here`;

        if (costing.unasked === 0) {
          progress.textContent = `All ${plural(costing.questions, "answer")} are here.`;
          box.dataset.unasked = "0";
          return;
        }
        if (costing.unasked >= left) {
          progress.textContent =
            `${plural(costing.unasked, "answer")} could not be had now: a supplier's calls ` +
            "are used up for the moment, or it is not answering. The status page says which. " +
            "Asking again later costs nothing that has not been asked.";
          box.dataset.unasked = String(costing.unasked);
          begin.textContent = "Ask again";
          begin.hidden = false;
          return;
        }
        left = costing.unasked;
        box.dataset.unasked = String(left);
      }
      progress.textContent = "Stopped. What was asked is kept.";
      begin.textContent = "Ask the rest";
      begin.hidden = false;
    } catch (error) {
      progress.textContent = error.unauthorized
        ? "Your session has ended. Reload the page to sign in."
        : `The suppliers could not be asked: ${error.message}`;
      begin.hidden = false;
    } finally {
      stop.hidden = true;
    }
  }

  begin.addEventListener("click", ask);
  stop.addEventListener("click", () => {
    stopping = true;
    stop.hidden = true;
    progress.textContent = "Stopping after this round";
  });
}
