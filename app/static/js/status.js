/* The "Refresh now" button on the status page.
 *
 * It waits for the refresh to finish and says what came of it, because the
 * person pressing it has usually just pushed something and wants to know
 * whether it arrived.
 */
import { api, plural } from "./app.js";

function start() {
  const button = document.getElementById("refresh-now");
  const result = document.getElementById("refresh-result");
  if (!button) return;

  function describe(outcome) {
    if (outcome.error) return `The sources could not be reached: ${outcome.error}`;
    if (!outcome.changed) return "Nothing new: every source is at the commit already indexed.";
    return `Indexed ${plural(outcome.parts, "part")}.`;
  }

  button.addEventListener("click", async () => {
    button.disabled = true;
    result.textContent = "Fetching the sources";
    try {
      const outcome = await api("/api/v1/library/refresh", { method: "POST" });
      result.textContent = describe(outcome);
      // The figures on this page are from before; show the ones from after.
      if (outcome.changed) setTimeout(() => window.location.reload(), 900);
    } catch (error) {
      result.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  });
}

start();
