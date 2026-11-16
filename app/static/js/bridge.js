/* The KiCad bridge, for the modules that use it.
 *
 * The bridge itself is kicad-bridge.js, and is not a module: it is loaded in
 * the head, as an ordinary script, because it has to be listening before KiCad
 * speaks and a module is never run that early. It leaves what it offers on the
 * window. This is the one place that reaches for it there, so that everything
 * else imports the bridge like anything else it depends on.
 *
 * Only for pages that load kicad-bridge.js: the catalog and a part's page.
 */
export const bridge = window.KicadBridge;
