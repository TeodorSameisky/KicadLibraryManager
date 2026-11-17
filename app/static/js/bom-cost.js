/* Asking the suppliers about a BOM's parts, a piece at a time.
 *
 * A supplier allows only so many calls a minute, and a BOM can have hundreds
 * of manufacturer parts. So the server asks for a few seconds at a time and
 * says what is still to be asked; this asks it again while that goes down,
 * and puts the costing worked out from the answers so far on the page each
 * time. It stops when everything is asked, when someone says to, or when a
 * round gets no further -- a supplier's calls are used up for now, or it is
 * not answering -- and says so.
 */
import { api, plural } from "./app.js";

function start() {
  const box = document.getElementById("cost-ask");
  if (!box) return;

  const begin = document.getElementById("cost-ask-start");
  const stop = document.getElementById("cost-ask-stop");
  const progress = document.getElementById("cost-ask-progress");
  const { bomId, boards } = box.dataset;
  let stopping = false;

  /* The costing, as the page shows it now: fetched and put in place of the
     one on screen, so that the totals and the table move as answers come. */
  async function refresh() {
    const response = await fetch(window.location.href, { credentials: "same-origin" });
    if (!response.ok) return;
    const page = new DOMParser().parseFromString(await response.text(), "text/html");
    const fresh = page.getElementById("costing");
    if (fresh) document.getElementById("costing").replaceWith(fresh);
  }

  async function ask() {
    begin.hidden = true;
    stop.hidden = false;
    stopping = false;
    let left = Number(box.dataset.unasked);
    progress.textContent = "Asking the suppliers";

    try {
      while (!stopping) {
        const costing = await api(`/api/v1/boms/${bomId}/cost/ask?boards=${boards}`, {
          method: "POST",
        });
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

start();
