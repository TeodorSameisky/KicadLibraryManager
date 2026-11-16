/* Uploading a BOM, and deleting one.
 *
 * The file is read here and posted as JSON, so the page uses the same
 * endpoint a script would. Both of the things this page can do to a stored
 * record -- replace it, delete it -- ask first, in the page, with the name of
 * what would be lost.
 */
import { api, el, button, base } from "./app.js";

/* A sentence about what just happened, with any actions that follow from it. */
function notice(target, kind, text, actions = []) {
  const box = el("div", `inline-note${kind ? ` inline-note--${kind}` : ""}`);
  box.append(el("span", null, text), ...actions);
  target.replaceChildren(box);
}

// --------------------------------------------------------------- upload

const form = document.getElementById("upload");
if (form) {
  const file = document.getElementById("bom-file");
  const device = document.getElementById("bom-device");
  const project = document.getElementById("bom-project");
  const revision = document.getElementById("bom-revision");
  const note = document.getElementById("bom-note");
  const submit = document.getElementById("bom-submit");
  const result = document.getElementById("upload-result");

  /* KiCad names the export after the project, so the file's name is the
     best first guess at what to call it. Only a guess: it is not put in
     over something already typed. */
  file.addEventListener("change", () => {
    const chosen = file.files[0];
    if (!chosen || project.value.trim()) return;
    project.value = chosen.name
      .replace(/\.[^.]+$/, "")
      .replace(/[-_ ]?bom$/i, "");
  });

  /* A BOM for a device is, in the way people name them, a project called
     after it. So choosing one fills the project in -- unless something else
     was typed there, which stays. */
  let suggested = "";
  if (device) {
    device.addEventListener("change", () => {
      if (project.value.trim() && project.value !== suggested) return;
      const chosen = device.selectedOptions[0];
      suggested = device.value ? chosen.textContent.trim() : "";
      project.value = suggested;
    });
  }

  async function upload(replace) {
    const chosen = file.files[0];
    if (!chosen) return notice(result, "bad", "Choose the BOM file to upload.");
    if (!project.value.trim()) return notice(result, "bad", "Say which project this BOM is for.");

    submit.disabled = true;
    notice(result, null, "Uploading");
    try {
      const stored = await api("/api/v1/boms", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          project: project.value,
          device: device ? device.value : "",
          revision: revision.value,
          note: note.value,
          filename: chosen.name,
          content: await chosen.text(),
          replace,
        }),
      });
      // Straight to what was just uploaded: that is what the upload was for.
      window.location.assign(`${base}/boms/${stored.id}`);
    } catch (error) {
      if (error.status === 409) {
        notice(result, "warn", `${error.message} Uploading again replaces it.`, [
          button("button", "Replace it", () => upload(true)),
          button("button", "Cancel", () => result.replaceChildren()),
        ]);
      } else {
        notice(
          result,
          "bad",
          error.unauthorized ? "Your session has ended. Reload the page to sign in." : error.message
        );
      }
    } finally {
      submit.disabled = false;
    }
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    upload(false);
  });
}

// --------------------------------------------------------------- device

const attach = document.getElementById("device-form");
if (attach) {
  const choice = document.getElementById("bom-device");
  const save = document.getElementById("device-save");
  const result = document.getElementById("device-result");

  attach.addEventListener("submit", async (event) => {
    event.preventDefault();
    save.disabled = true;
    try {
      await api(`/api/v1/boms/${attach.dataset.bomId}/device`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ device: choice.value }),
      });
      // Reloaded rather than patched, so the page says what is now stored.
      window.location.reload();
    } catch (error) {
      notice(result, "bad", error.message);
      save.disabled = false;
    }
  });
}

// --------------------------------------------------------------- delete

const actions = document.getElementById("bom-actions");
const remove = document.getElementById("bom-delete");
if (actions && remove) {
  const result = document.getElementById("delete-result");

  async function destroy() {
    try {
      await api(`/api/v1/boms/${actions.dataset.bomId}`, { method: "DELETE" });
      window.location.assign(`${base}/boms`);
    } catch (error) {
      notice(result, "bad", error.message);
    }
  }

  remove.addEventListener("click", () => {
    notice(
      result,
      "warn",
      "Delete this BOM? Its parts will no longer list this project under where they are used.",
      [
        button("button button--danger", "Delete it", destroy),
        button("button", "Keep it", () => result.replaceChildren()),
      ]
    );
  });
}
