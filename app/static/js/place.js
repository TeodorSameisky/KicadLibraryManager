/* Placing a part into KiCad: the catalog's Place button and the part page's
 * are the same thing, so it is done here once.
 *
 * The server says what to send and in what order -- the footprint and 3D model
 * saved first, the symbol placed last, so that what it refers to is already
 * there when it lands -- and this sends it, one command at a time, waiting for
 * KiCad's answer to each.
 */
import { api } from "./app.js";
import { bridge } from "./bridge.js";

/* Place `ipn`. Resolves with the warnings the server had about it (a
   footprint it could not find, a model too large to send safely), and
   rejects with the reason if it could not be placed.

   `onWarnings` is called as soon as they are known, before anything is
   sent, so that they can be shown while the part is on its way.
   `log` is told each step. */
export async function place(ipn, { log = () => {}, onWarnings = () => {} } = {}) {
  if (!bridge.isEmbedded()) throw new Error("Parts can only be placed from inside KiCad.");

  log(`Fetching ${ipn}`);
  const bundle = await api(`/api/v1/parts/${encodeURIComponent(ipn)}/assets`);

  for (const warning of bundle.warnings) log(`Warning: ${warning}`);
  if (bundle.warnings.length) onWarnings(bundle.warnings);

  await bridge.whenReady();
  for (const asset of bundle.assets) {
    log(`Sending ${asset.filename}`);
    await bridge.send(asset.command, asset.parameters, asset.data);
  }

  log(`Placed ${ipn}`);
  return bundle.warnings;
}
