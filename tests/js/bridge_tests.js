/* Runs the page scripts that talk to KiCad against a pretend KiCad.
 *
 * They are plain browser scripts with no module system, so each is run in a
 * fresh context holding only what a page would give it. Prints a JSON object:
 * the name of each test, and whether it passed.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const SCRIPTS = path.join(__dirname, "..", "..", "app", "static", "js");
const read = (name) => fs.readFileSync(path.join(SCRIPTS, name), "utf8");
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

class Storage {
  constructor() { this.data = new Map(); }
  getItem(key) { return this.data.has(key) ? this.data.get(key) : null; }
  setItem(key, value) { this.data.set(key, String(value)); }
}

class RefusingStorage {
  getItem() { throw new Error("storage is blocked"); }
  setItem() { throw new Error("storage is blocked"); }
}

/* One page: a window with a host to send to, and the bridge loaded into it. */
function page({ storage = new Storage(), host = true, scripts = ["kicad-bridge.js"] } = {}) {
  const sent = [];
  const win = { sessionStorage: storage };
  win.window = win;
  if (host) win.chrome = { webview: { postMessage: (text) => sent.push(JSON.parse(text)) } };

  const context = {
    window: win,
    document: { currentScript: null, body: { dataset: {} } },
    setTimeout, clearTimeout, console, JSON, Promise, Map, Set, Number, String, Array, Object, Error,
  };
  for (const script of scripts) vm.runInNewContext(read(script), context);

  // A command nobody answers times out later, as it would in a page. Tests
  // that don't wait for an answer must not die of it.
  const realSend = win.KicadBridge.send;
  win.KicadBridge.send = (...args) => {
    const answer = realSend(...args);
    answer.catch(() => {});
    return answer;
  };
  return { win, sent, bridge: win.KicadBridge, deliver: (envelope) => win.kiclient.postMessage(JSON.stringify(envelope)) };
}

const newSession = (id, messageId = 7) => ({
  version: 1, session_id: id, message_id: messageId, command: "NEW_SESSION", parameters: {},
});

const tests = {};
const test = (name, body) => { tests[name] = body; };

function assert(condition, message) {
  if (!condition) throw new Error(message);
}
function equal(actual, expected, what) {
  assert(JSON.stringify(actual) === JSON.stringify(expected),
    `${what}: expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
}

// -- the session --------------------------------------------------------

test("answers-new-session", async () => {
  const { sent, deliver } = page();
  deliver(newSession("S1"));

  equal(sent.length, 1, "messages sent");
  equal([sent[0].command, sent[0].status, sent[0].response_to, sent[0].session_id, sent[0].message_id],
    ["NEW_SESSION", "OK", 7, "S1", 8], "the reply");
  equal(sent[0].version, 1, "protocol version");
});

test("sends-with-the-session", async () => {
  const { sent, bridge, deliver } = page();
  deliver(newSession("S1"));

  const answer = bridge.send("DL_SYMBOL", { mode: "PLACE" }, "payload");
  equal([sent[1].session_id, sent[1].command, sent[1].parameters, sent[1].data, sent[1].message_id],
    ["S1", "DL_SYMBOL", { mode: "PLACE" }, "payload", 9], "the command");

  deliver({ version: 1, session_id: "S1", message_id: 100, response_to: 9, status: "OK" });
  equal((await answer).status, "OK", "the answer");
});

test("error-reply-rejects", async () => {
  const { bridge, deliver } = page();
  deliver(newSession("S1"));

  const answer = bridge.send("DL_SYMBOL");
  deliver({ version: 1, session_id: "S1", message_id: 100, response_to: 9, status: "ERROR", error_message: "Unable to save" });

  let message = null;
  await answer.catch((error) => { message = error.message; });
  equal(message, "Unable to save", "KiCad's own words");
});

test("no-session-refuses", async () => {
  const { sent, bridge } = page();

  let message = null;
  await bridge.send("DL_SYMBOL").catch((error) => { message = error.message; });

  equal(message, "There is no KiCad session", "the refusal");
  equal(sent.length, 0, "nothing was sent");
});

test("waits-then-gives-up", async () => {
  const { bridge } = page();

  let message = null;
  await bridge.whenReady(20).catch((error) => { message = error.message; });

  equal(message, "KiCad did not open a session", "the timeout");
});

// -- a session carried from one page to the next ------------------------------

test("later-page-reuses-session", async () => {
  const storage = new Storage();
  const first = page({ storage });
  first.deliver(newSession("S1"));
  first.bridge.send("DL_SYMBOL"); // message 9, so the next page continues after it

  // The panel followed a link: a new page, and KiCad opens no new session.
  const second = page({ storage });
  await second.bridge.whenReady(50);
  second.bridge.send("DL_FOOTPRINT", { a: 1 }, "x");

  equal([second.sent[0].session_id, second.sent[0].command], ["S1", "DL_FOOTPRINT"], "the later page's command");
  assert(second.sent[0].message_id > 9, `ids carry on: ${second.sent[0].message_id}`);
});

test("new-session-replaces", async () => {
  const storage = new Storage();
  page({ storage }).deliver(newSession("S1"));

  const second = page({ storage });
  second.deliver(newSession("S2", 1));
  second.bridge.send("DL_SYMBOL");

  equal(second.sent[1].session_id, "S2", "the new session is used");
  equal(JSON.parse(storage.getItem("klm.kicad-session")).id, "S2", "and is the one kept");
});

test("storage-refused", async () => {
  const { sent, bridge, deliver } = page({ storage: new RefusingStorage() });

  deliver(newSession("S1"));
  bridge.send("DL_SYMBOL");

  equal(sent[0].command, "NEW_SESSION", "still answered");
  equal(sent[1].session_id, "S1", "and still sends");
});

test("junk-in-storage-is-ignored", async () => {
  const storage = new Storage();
  storage.setItem("klm.kicad-session", "not json {");
  const { bridge } = page({ storage });

  let message = null;
  await bridge.send("DL_SYMBOL").catch((error) => { message = error.message; });

  equal(message, "There is no KiCad session", "treated as no session");
});

// -- placing ------------------------------------------------------------

function placing({ embedded = true, failAt = null } = {}) {
  const calls = [];
  const bundle = {
    warnings: ["footprint is large"],
    assets: [
      { command: "DL_FOOTPRINT", filename: "fp.kicad_mod", parameters: { mode: "SAVE" }, data: "a" },
      { command: "DL_3DMODEL", filename: "m.step", parameters: { mode: "SAVE" }, data: "b" },
      { command: "DL_SYMBOL", filename: "s.kicad_sym", parameters: { mode: "PLACE" }, data: "c" },
    ],
  };
  const win = {
    App: { api: async (url) => { calls.push(["api", url]); return bundle; } },
    KicadBridge: {
      isEmbedded: () => embedded,
      whenReady: async () => { calls.push(["ready"]); },
      send: async (command) => {
        calls.push(["send", command]);
        if (command === failAt) throw new Error("Unable to save the downloaded symbol.");
      },
    },
  };
  win.window = win;
  vm.runInNewContext(read("place.js"), { window: win, encodeURIComponent, Promise });
  return { place: win.Place.place, calls };
}

test("place-sends-in-order", async () => {
  const { place, calls } = placing();
  const warnings = await place("1102-0001");

  equal(calls.map((c) => c.join(":")), [
    "api:/api/v1/parts/1102-0001/assets", "ready",
    "send:DL_FOOTPRINT", "send:DL_3DMODEL", "send:DL_SYMBOL",
  ], "what happened, in order: saved first, the symbol last");
  equal(warnings, ["footprint is large"], "the warnings come back");
});

test("place-warns-before-sending", async () => {
  const { place, calls } = placing();
  let when = null;
  await place("1102-0001", { onWarnings: () => { when = calls.length; } });

  equal(when, 1, "after fetching, before anything was sent");
});

test("place-stops-on-error", async () => {
  const { place, calls } = placing({ failAt: "DL_3DMODEL" });

  let message = null;
  await place("1102-0001").catch((error) => { message = error.message; });

  equal(message, "Unable to save the downloaded symbol.", "KiCad's reason");
  assert(!calls.some((c) => c[1] === "DL_SYMBOL"), "the symbol was not placed after a failure");
});

test("place-needs-kicad", async () => {
  const { place, calls } = placing({ embedded: false });

  let message = null;
  await place("1102-0001").catch((error) => { message = error.message; });

  equal(message, "Parts can only be placed from inside KiCad.", "the reason");
  equal(calls.length, 0, "nothing was even fetched");
});

// -- telling whether this is KiCad ----------------------------------------

test("in-kicad-detection", async () => {
  /* The head script decides, and says so on the page; the other scripts ask
     the page. */
  const inside = (host) => {
    const classes = new Set();
    const document = {
      body: { dataset: {} },
      documentElement: { classList: { add: (name) => classes.add(name), contains: (name) => classes.has(name) } },
    };
    const win = { ...host };
    win.window = win;
    vm.runInNewContext(read("in-kicad.js"), { window: win, document, Boolean });
    vm.runInNewContext(read("app.js"), {
      window: win, document, fetch: () => {}, Promise, Object, Array, Error, String, Boolean,
    });
    const marked = classes.has("in-kicad");
    assert(win.App.inKicad() === marked, "the page and the scripts agree");
    return marked;
  };

  equal(inside({}), false, "an ordinary browser");
  equal(inside({ chrome: {} }), false, "Chrome has window.chrome and no webview");
  equal(inside({ chrome: { webview: {} } }), true, "KiCad on Windows");
  equal(inside({ webkit: { messageHandlers: { kicad: {} } } }), true, "KiCad on macOS and Linux");
  equal(inside({ webkit: { messageHandlers: {} } }), false, "some other WebKit host");
  equal(inside({ external: { invoke() {} } }), true, "the older host bridge");
});

(async () => {
  const results = {};
  for (const [name, body] of Object.entries(tests)) {
    try {
      await body();
      results[name] = { ok: true };
    } catch (error) {
      results[name] = { ok: false, error: String(error && error.message ? error.message : error) };
    }
    await tick();
  }
  // Pending timeouts of unanswered commands need not be waited for.
  process.stdout.write(JSON.stringify(results), () => process.exit(0));
})();
