/* FaceSort (1.56): the parts of Chrome's extension API that Max Image Saver uses, answered by
 * FaceSort through its web channel. Runs in its own script world, before content.js. */
(() => {
  'use strict';
  if (window.__fsShim) return;
  const pending = new Map();
  const ports = new Map();
  let nextId = 1;
  let bridge = null;
  const queue = [];
  const hasTransport = () => !!(window.qt && window.qt.webChannelTransport);
  function send(o) {
    const s = JSON.stringify(o);
    if (bridge) bridge.send(s); else queue.push(s);
  }
  if (hasTransport() && typeof QWebChannel === 'function') {
    new QWebChannel(window.qt.webChannelTransport, (ch) => {
      bridge = ch.objects.fs;
      queue.splice(0).forEach((s) => bridge.send(s));
    });
  }
  window.__fsShim = {
    reply(id, r) {
      const cb = pending.get(id);
      pending.delete(id);
      if (cb) try { cb(r); } catch (_) { /* the page's own error */ }
    },
    portMsg(pid, m) {
      const p = ports.get(pid);
      if (p) p._l.slice().forEach((f) => { try { f(m); } catch (_) { /* ignore */ } });
    },
    portGone(pid) {
      const p = ports.get(pid);
      if (!p) return;
      ports.delete(pid);
      p._d.slice().forEach((f) => { try { f(); } catch (_) { /* ignore */ } });
    },
  };
  const runtime = {
    lastError: null,
    sendMessage(msg, cb) {
      if (!hasTransport()) {               // (a frame without FaceSort's channel)
        runtime.lastError = { message: 'FaceSort is not connected here' };
        setTimeout(() => { try { cb && cb(undefined); } finally { runtime.lastError = null; } }, 0);
        return;
      }
      const id = nextId++;
      pending.set(id, (r) => { runtime.lastError = null; if (cb) cb(r); });
      send({ kind: 'msg', id, msg, page: location.href });
    },
    connect(info) {
      const pid = nextId++;
      const name = (info && info.name) || '';
      const p = {
        name, _l: [], _d: [],
        onMessage: { addListener: (f) => p._l.push(f) },
        onDisconnect: { addListener: (f) => p._d.push(f) },
        postMessage: (m) => send({ kind: 'port', port: pid, name, msg: m, page: location.href }),
        disconnect: () => { ports.delete(pid); send({ kind: 'disconnect', port: pid }); },
      };
      ports.set(pid, p);
      send({ kind: 'connect', port: pid, name, page: location.href });
      return p;
    },
    getPlatformInfo(cb) { if (cb) cb({}); },
    onMessage: { addListener(f) { listeners.push(f); } },     // (FaceSort -> page messages)
  };
  const listeners = [];
  window.__fsShim.message = (m) => listeners.forEach((f) => { try { f(m, {}, () => {}); } catch (_) { /* ignore */ } });
  const storage = {
    sync: {
      get(defaults, cb) {
        const r = Object.assign({}, defaults || {});
        if (cb) setTimeout(() => cb(r), 0);
        return Promise.resolve(r);
      },
      set() { return Promise.resolve(); },
    },
    onChanged: { addListener() {} },
  };
  window.chrome = Object.assign(window.chrome || {}, { runtime, storage });

  // A sign-in form sent: FaceSort offers to keep the login (encrypted) for this site.
  document.addEventListener('submit', (e) => {
    try {
      const f = e.target;
      const pw = f && f.querySelector && f.querySelector('input[type=password]');
      if (!pw || !pw.value) return;
      const ins = [...f.querySelectorAll('input')]
        .filter((i) => ['text', 'email', 'tel', ''].includes((i.getAttribute('type') || '').toLowerCase()) && i.value);
      send({ kind: 'login', host: location.hostname, user: ins.length ? ins[ins.length - 1].value : '', pass: pw.value });
    } catch (_) { /* ignore */ }
  }, true);
})();
