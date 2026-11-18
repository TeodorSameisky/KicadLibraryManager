/* Making and revoking the tokens KiCad's HTTP library reads with.
 *
 * A new token is shown once, with the .kicad_httplib file that holds it,
 * saved from here: the server keeps only a hash, so it cannot be shown again.
 */
import { api, el, button } from "./app.js";

function save(file, name) {
  const blob = new Blob([JSON.stringify(file, null, 4) + "\n"], { type: "application/json" });
  const link = el("a");
  link.href = URL.createObjectURL(blob);
  link.download = name;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
}

function fileName(file) {
  const name = String(file.name || "library").replace(/[^A-Za-z0-9._-]+/g, "-");
  return `${name}.kicad_httplib`;
}

// Times as the reader's clock has them.
for (const cell of document.querySelectorAll("[data-time]")) {
  const seconds = Number(cell.dataset.time);
  if (cell.dataset.time && seconds) cell.textContent = new Date(seconds * 1000).toLocaleString();
}

const open = document.getElementById("download-open");
if (open) {
  const file = JSON.parse(document.getElementById("open-file").textContent);
  open.addEventListener("click", () => save(file, fileName(file)));
}

const form = document.getElementById("token-form");
if (form) {
  const label = document.getElementById("token-label");
  const submit = document.getElementById("token-submit");
  const result = document.getElementById("token-result");

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    submit.disabled = true;
    try {
      const made = await api("/api/v1/tokens", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ label: label.value }),
      });
      const secret = el("input", "mono");
      secret.type = "text";
      secret.readOnly = true;
      secret.value = made.secret;
      secret.setAttribute("aria-label", "The token");
      secret.addEventListener("focus", () => secret.select());

      const box = el("div", "inline-note");
      const actions = el("div", "actions");
      actions.append(
        button("button", "Download the .kicad_httplib file", () =>
          save(made.library_file, fileName(made.library_file))
        ),
        button("button", "Done", () => window.location.reload())
      );
      box.append(
        el("div", null, "This is the only time the token is shown. The file holds it."),
        secret,
        actions
      );
      result.replaceChildren(box);
      form.hidden = true;
    } catch (error) {
      result.replaceChildren(el("div", "inline-note inline-note--bad", error.message));
      submit.disabled = false;
    }
  });
}

for (const control of document.querySelectorAll("[data-revoke]")) {
  control.addEventListener("click", async () => {
    control.disabled = true;
    try {
      await api(`/api/v1/tokens/${encodeURIComponent(control.dataset.revoke)}`, {
        method: "DELETE",
      });
      window.location.reload();
    } catch (error) {
      control.disabled = false;
      control.after(el("div", "inline-note inline-note--bad", error.message));
    }
  });
}
