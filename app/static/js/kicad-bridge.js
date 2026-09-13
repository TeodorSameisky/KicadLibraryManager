/*
 * Minimal KiCad Remote Symbols RPC bridge.
 *
 * KiCad drives the session: it injects window.kiclient.postMessage(json) and
 * opens with NEW_SESSION, which we must answer before sending anything of our
 * own. Replies travel back over whichever host bridge the platform exposes.
 */
(function () {
  "use strict";

  const RPC_VERSION = 1;
  const RESPONSE_TIMEOUT_MS = 8000;

  let sessionId = null;
  let messageCounter = 0;
  const waiters = new Map();
  const listeners = new Set();

  function postToKiCad(payload) {
    if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.kicad) {
      window.webkit.messageHandlers.kicad.postMessage(payload);
      return true;
    }
    if (window.chrome && window.chrome.webview && window.chrome.webview.postMessage) {
      window.chrome.webview.postMessage(payload);
      return true;
    }
    if (window.external && typeof window.external.invoke === "function") {
      window.external.invoke(payload);
      return true;
    }
    return false; // running in a plain browser, not inside KiCad
  }

  function handleIncoming(incoming) {
    let env = incoming;
    if (typeof env === "string") {
      try {
        env = JSON.parse(env);
      } catch (err) {
        return;
      }
    }
    if (!env || typeof env !== "object") return;

    if (env.command === "NEW_SESSION" && env.response_to === undefined) {
      sessionId = env.session_id;
      messageCounter = env.message_id || 0;
      waiters.clear();
      postToKiCad(
        JSON.stringify({
          version: RPC_VERSION,
          session_id: sessionId,
          message_id: ++messageCounter,
          response_to: env.message_id,
          command: "NEW_SESSION",
          status: "OK",
          parameters: { server_name: "KiCad Library Manager", server_version: "0.1.0" },
        })
      );
      resolveSessionWaiters();
      listeners.forEach((fn) => fn(env));
      return;
    }

    if (env.response_to !== undefined) {
      const waiter = waiters.get(String(env.response_to));
      if (waiter) {
        waiters.delete(String(env.response_to));
        if (env.status === "ERROR") {
          waiter.reject(new Error(env.error_message || "RPC error"));
        } else {
          waiter.resolve(env);
        }
      }
      return;
    }

    listeners.forEach((fn) => fn(env));
  }

  function send(command, parameters, data) {
    return new Promise(function (resolve, reject) {
      if (!sessionId) {
        reject(new Error("No KiCad session yet"));
        return;
      }
      const id = ++messageCounter;
      const key = String(id);
      const delivered = postToKiCad(
        JSON.stringify({
          version: RPC_VERSION,
          session_id: sessionId,
          message_id: id,
          command: command,
          parameters: parameters || {},
          data: data || "",
        })
      );
      if (!delivered) {
        reject(new Error("No KiCad bridge available"));
        return;
      }
      const timer = setTimeout(function () {
        waiters.delete(key);
        reject(new Error("Timed out waiting for KiCad"));
      }, RESPONSE_TIMEOUT_MS);
      waiters.set(key, {
        resolve: function (v) { clearTimeout(timer); resolve(v); },
        reject: function (e) { clearTimeout(timer); reject(e); },
      });
    });
  }

  const sessionWaiters = [];

  function resolveSessionWaiters() {
    while (sessionWaiters.length) {
      const w = sessionWaiters.shift();
      clearTimeout(w.timer);
      w.resolve(sessionId);
    }
  }

  /* Resolves once KiCad has opened a session, or rejects after timeoutMs.
     KiCad sends NEW_SESSION when it loads the page, so a caller that runs
     first would otherwise fail for no reason. */
  function whenReady(timeoutMs) {
    if (sessionId) return Promise.resolve(sessionId);
    return new Promise(function (resolve, reject) {
      const w = { resolve: resolve };
      w.timer = setTimeout(function () {
        const i = sessionWaiters.indexOf(w);
        if (i >= 0) sessionWaiters.splice(i, 1);
        reject(new Error("KiCad did not open a session"));
      }, timeoutMs || 5000);
      sessionWaiters.push(w);
    });
  }

  const existing = window.kiclient || {};
  const previous = typeof existing.postMessage === "function" ? existing.postMessage.bind(existing) : null;
  existing.postMessage = function (incoming) {
    handleIncoming(incoming);
    if (previous) previous(incoming);
  };
  window.kiclient = existing;

  window.KicadBridge = {
    send: send,
    whenReady: whenReady,
    login: function () {
      return whenReady(8000).then(function () {
        return send("REMOTE_LOGIN", { interactive: true });
      });
    },
    logout: function () {
      return whenReady(8000).then(function () { return send("LOGOUT", {}); });
    },
    onMessage: function (fn) { listeners.add(fn); return function () { listeners.delete(fn); }; },
    sessionId: function () { return sessionId; },
    isEmbedded: function () {
      return !!(
        (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.kicad) ||
        (window.chrome && window.chrome.webview) ||
        (window.external && typeof window.external.invoke === "function")
      );
    },
  };
})();
