/* The KiCad Remote Symbols RPC, from the page's side.
 *
 * KiCad drives. It injects window.kiclient.postMessage(json) and opens with
 * NEW_SESSION, which has to be answered before the page may send anything of
 * its own. Messages back to KiCad go over whichever host bridge the platform's
 * WebView provides.
 *
 * Two things follow from how KiCad behaves:
 *
 *  - This file is loaded in <head>, not deferred. NEW_SESSION arrives as the
 *    page loads, and a bridge installed later never sees it.
 *  - The page must never reload itself. KiCad sends NEW_SESSION once per load
 *    it started, so after a script-initiated reload there is no session and
 *    nothing can be placed. State changes are applied in place instead.
 */
(() => {
  "use strict";

  const RPC_VERSION = 1;
  const RESPONSE_TIMEOUT_MS = 8000;
  const SESSION_TIMEOUT_MS = 8000;

  const script = document.currentScript;
  const server = {
    server_name: (script && script.dataset.serverName) || "KiCad Library Manager",
    server_version: (script && script.dataset.serverVersion) || "0",
  };

  let sessionId = null;
  let lastMessageId = 0;
  const pending = new Map(); // message id, as a string -> {resolve, reject, timer}
  const sessionWaiters = new Set(); // {resolve, timer}
  const listeners = new Set();

  /* The host's way of receiving a message, or null in an ordinary browser. */
  function hostChannel() {
    const { webkit, chrome, external } = window;
    if (webkit && webkit.messageHandlers && webkit.messageHandlers.kicad) {
      return (text) => webkit.messageHandlers.kicad.postMessage(text); // macOS, Linux
    }
    if (chrome && chrome.webview && chrome.webview.postMessage) {
      return (text) => chrome.webview.postMessage(text); // Windows
    }
    if (external && typeof external.invoke === "function") {
      return (text) => external.invoke(text);
    }
    return null;
  }

  function post(envelope) {
    const channel = hostChannel();
    if (!channel) return false;
    channel(JSON.stringify({ version: RPC_VERSION, session_id: sessionId, ...envelope }));
    return true;
  }

  // ------------------------------------------------------------- incoming

  function openSession(envelope) {
    sessionId = envelope.session_id;
    lastMessageId = envelope.message_id || 0;

    // Whatever was in flight belonged to the previous session.
    for (const waiter of pending.values()) {
      clearTimeout(waiter.timer);
      waiter.reject(new Error("KiCad started a new session"));
    }
    pending.clear();

    post({
      message_id: ++lastMessageId,
      response_to: envelope.message_id,
      command: "NEW_SESSION",
      status: "OK",
      parameters: server,
    });

    for (const waiter of sessionWaiters) {
      clearTimeout(waiter.timer);
      waiter.resolve(sessionId);
    }
    sessionWaiters.clear();
  }

  function receive(incoming) {
    let envelope = incoming;
    if (typeof envelope === "string") {
      try {
        envelope = JSON.parse(envelope);
      } catch {
        return;
      }
    }
    if (!envelope || typeof envelope !== "object") return;

    if (envelope.response_to !== undefined) {
      const key = String(envelope.response_to);
      const waiter = pending.get(key);
      if (!waiter) return;
      pending.delete(key);
      clearTimeout(waiter.timer);
      if (envelope.status === "ERROR") {
        waiter.reject(new Error(envelope.error_message || "KiCad reported an error"));
      } else {
        waiter.resolve(envelope);
      }
      return;
    }

    if (envelope.command === "NEW_SESSION") openSession(envelope);
    for (const listener of listeners) listener(envelope);
  }

  // Wrap rather than replace: if KiCad has already put a kiclient in place,
  // whatever it does with a message still happens.
  const kiclient = window.kiclient || {};
  const previous =
    typeof kiclient.postMessage === "function" ? kiclient.postMessage.bind(kiclient) : null;
  kiclient.postMessage = (incoming) => {
    receive(incoming);
    if (previous) previous(incoming);
  };
  window.kiclient = kiclient;

  // ------------------------------------------------------------- outgoing

  /* Resolves once KiCad has opened a session. A caller that runs before
     NEW_SESSION has arrived would otherwise fail for no reason of its own. */
  function whenReady(timeoutMs = SESSION_TIMEOUT_MS) {
    if (sessionId) return Promise.resolve(sessionId);
    return new Promise((resolve, reject) => {
      const waiter = { resolve };
      waiter.timer = setTimeout(() => {
        sessionWaiters.delete(waiter);
        reject(new Error("KiCad did not open a session"));
      }, timeoutMs);
      sessionWaiters.add(waiter);
    });
  }

  /* Send a command and wait for KiCad's answer to it. */
  function send(command, parameters = {}, data = "") {
    return new Promise((resolve, reject) => {
      if (!sessionId) {
        reject(new Error("There is no KiCad session"));
        return;
      }
      const id = ++lastMessageId;
      if (!post({ message_id: id, command, parameters, data })) {
        reject(new Error("This page is not running inside KiCad"));
        return;
      }
      const key = String(id);
      const timer = setTimeout(() => {
        pending.delete(key);
        reject(new Error("KiCad did not answer in time"));
      }, RESPONSE_TIMEOUT_MS);
      pending.set(key, { resolve, reject, timer });
    });
  }

  window.KicadBridge = {
    /* Whether a host bridge exists at all: false in an ordinary browser. */
    isEmbedded: () => hostChannel() !== null,
    whenReady,
    send,
    /* Ask KiCad to run its OAuth2 sign-in. It answers once the session
       bootstrap has completed and the WebView holds a session cookie. */
    login: () => whenReady().then(() => send("REMOTE_LOGIN", { interactive: true })),
    logout: () => whenReady().then(() => send("LOGOUT")),
    onMessage(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
})();
