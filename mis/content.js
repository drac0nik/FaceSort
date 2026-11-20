/* Max Image Saver - content script, as used inside FaceSort's browser (1.56).
 * The only change from the Chrome extension: the hook at the end, so FaceSort's gallery watcher
 * can start the same ALL / GALLERY listing it would start with a click, and see when it's done.
 * chrome.* comes from FaceSort (mis/shim.js), and the downloads are made by FaceSort. */
/* Max Image Saver — content script
 * Shows a small download button over hovered images and saves the largest
 * version the site offers (srcset, lazy-load attributes, linked originals,
 * known CDN URL patterns, and site lookups for Flickr / FetLife).
 */
(() => {
  'use strict';
  if (window.__maxImageSaverLoaded) return;
  window.__maxImageSaverLoaded = true;

  const DEFAULTS = { enabled: true, minSize: 120, saveAs: false, disabledSites: [], captureList: true };
  let settings = { ...DEFAULTS };
  const isActive = () =>
    settings.enabled && !(settings.disabledSites || []).includes(location.hostname);

  try {
    chrome.storage.sync.get(DEFAULTS, (s) => {
      settings = { ...DEFAULTS, ...s };
      if (!isActive()) hide(true);
    });
    chrome.storage.onChanged.addListener((changes, area) => {
      if (area !== 'sync') return;
      for (const k in changes) settings[k] = changes[k].newValue ?? DEFAULTS[k];
      if (!isActive()) hide(true);
    });
  } catch (_) { /* extension context gone */ }

  // ───────────────────────── UI (isolated in a closed shadow root) ─────────────────────────
  const ICONS = {
    idle: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4.5v10m0 0-4.2-4.2M12 14.5l4.2-4.2M5.5 19h13" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    busy: '<svg viewBox="0 0 24 24" class="spin" aria-hidden="true"><circle cx="12" cy="12" r="7.5" fill="none" stroke="currentColor" stroke-opacity=".25" stroke-width="2.4"/><path d="M12 4.5a7.5 7.5 0 0 1 7.5 7.5" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/></svg>',
    done: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5.5 12.5 4.2 4.2 8.8-9.2" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    error: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7.5 7.5l9 9m0-9-9 9" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/></svg>',
  };

  const host = document.createElement('max-image-saver');
  host.setAttribute('style', 'all:initial!important;position:fixed!important;top:0!important;left:0!important;width:0!important;height:0!important;z-index:2147483647!important;pointer-events:none!important;');
  const root = host.attachShadow({ mode: 'closed' });
  root.innerHTML = `
    <style>
      .wrap{position:fixed;top:0;right:0;display:flex;align-items:center;gap:6px;
        opacity:0;transform:scale(.88);transform-origin:top right;pointer-events:none;
        transition:opacity .12s ease,transform .12s ease;font:600 11px/1 system-ui,-apple-system,"Segoe UI",sans-serif}
      .wrap.show{opacity:1;transform:none}
      button{all:unset;box-sizing:border-box;width:30px;height:30px;display:grid;place-items:center;
        border-radius:7px;color:#fff;background:rgba(20,20,24,.72);cursor:pointer;
        border:1px solid rgba(255,255,255,.22);box-shadow:0 2px 8px rgba(0,0,0,.35);
        -webkit-backdrop-filter:blur(6px);backdrop-filter:blur(6px);transition:background .12s,transform .08s}
      .wrap.show button{pointer-events:auto}
      button:hover{background:rgba(20,20,24,.92)}
      button:active{transform:scale(.92)}
      button:focus-visible{outline:2px solid #6aa8ff;outline-offset:2px}
      button svg{width:18px;height:18px}
      .wrap[data-state=done] button{background:rgba(22,128,72,.9)}
      .wrap[data-state=error] button{background:rgba(180,40,40,.9)}
      .chip{color:#fff;background:rgba(20,20,24,.82);padding:6px 8px;border-radius:6px;white-space:nowrap;
        box-shadow:0 2px 8px rgba(0,0,0,.3);opacity:0;transition:opacity .15s;max-width:260px;overflow:hidden;text-overflow:ellipsis}
      .chip.show{opacity:1}
      .spin{animation:r .8s linear infinite}
      @keyframes r{to{transform:rotate(360deg)}}
      /* Flickr "download whole gallery" button: bold, pink, labelled */
      button.all{width:auto;height:30px;padding:0 10px 0 8px;display:flex;align-items:center;gap:5px;
        background:#ff0084;border:2px solid #fff;color:#fff;font:800 12px/1 system-ui,-apple-system,"Segoe UI",sans-serif;
        letter-spacing:.06em;box-shadow:0 2px 10px rgba(255,0,132,.55),0 1px 3px rgba(0,0,0,.4)}
      button.all:hover{background:#e00074}
      button.all[data-phase=confirm]{background:#fff;color:#ff0084;border-color:#ff0084}
      button.all[data-phase=running]{background:#b8005f}
      button.all svg{width:16px;height:16px}
      button.all[hidden],button.gal[hidden]{display:none}
      button.all:disabled,button.gal:disabled{opacity:.45;cursor:default}
      /* GALLERY: opens each thumbnail's linked page — distinct blue */
      button.gal{width:auto;height:30px;padding:0 10px 0 8px;display:flex;align-items:center;gap:5px;
        background:#2563eb;border:2px solid #fff;color:#fff;font:800 12px/1 system-ui,-apple-system,"Segoe UI",sans-serif;
        letter-spacing:.06em;box-shadow:0 2px 10px rgba(37,99,235,.55),0 1px 3px rgba(0,0,0,.4)}
      button.gal:hover{background:#1d4ed8}
      button.gal[data-phase=confirm]{background:#fff;color:#2563eb;border-color:#2563eb}
      button.gal[data-phase=running]{background:#1e3a8a}
      button.gal svg{width:16px;height:16px}
      /* Patreon "P+": whole post gallery */
      button.pplus{width:auto;height:30px;padding:0 9px;background:#ff424d;border:2px solid #fff;color:#fff;
        font:900 13px/1 system-ui,-apple-system,"Segoe UI",sans-serif;letter-spacing:.02em;
        box-shadow:0 2px 10px rgba(255,66,77,.55),0 1px 3px rgba(0,0,0,.4)}
      button.pplus:hover{background:#e3343f}
      button.pplus[hidden]{display:none}
      /* ModelMayhem "MM+": portfolio + credited photos from the profile image */
      button.mmplus{width:auto;height:30px;padding:0 9px;background:#f59e0b;border:2px solid #fff;color:#111;
        font:900 13px/1 system-ui,-apple-system,"Segoe UI",sans-serif;letter-spacing:.02em;
        box-shadow:0 2px 10px rgba(245,158,11,.55),0 1px 3px rgba(0,0,0,.4)}
      button.mmplus:hover{background:#d97706}
      button.mmplus[hidden]{display:none}
      /* LIST: images the page has loaded */
      button.cap{width:auto;height:30px;padding:0 10px 0 8px;display:flex;align-items:center;gap:5px;
        background:#059669;border:2px solid #fff;color:#fff;font:800 12px/1 system-ui,-apple-system,"Segoe UI",sans-serif;
        letter-spacing:.06em;box-shadow:0 2px 10px rgba(5,150,105,.5),0 1px 3px rgba(0,0,0,.4)}
      button.cap:hover{background:#047857}
      button.cap[hidden]{display:none}
      button.cap svg{width:16px;height:16px}
      .cpanel{position:fixed;right:16px;bottom:60px;width:min(560px,calc(100vw - 32px));max-height:min(72vh,720px);
        display:flex;flex-direction:column;background:#17171b;color:#eee;border:1px solid rgba(255,255,255,.14);border-radius:12px;
        box-shadow:0 12px 40px rgba(0,0,0,.5);pointer-events:auto;font:500 12.5px/1.35 system-ui,-apple-system,"Segoe UI",sans-serif}
      .cpanel[hidden]{display:none}
      .cpanel header{display:flex;align-items:center;gap:8px;padding:10px 12px;border-bottom:1px solid rgba(255,255,255,.1)}
      .cpanel header b{flex:1;font-size:13px}
      .cpanel .bar{display:flex;flex-wrap:wrap;gap:6px;padding:8px 12px;border-bottom:1px solid rgba(255,255,255,.08)}
      .cpanel button.t{all:unset;cursor:pointer;padding:6px 10px;border-radius:6px;background:rgba(255,255,255,.1);font-weight:700;font-size:12px}
      .cpanel button.t:hover{background:rgba(255,255,255,.18)}
      .cpanel button.t.go{background:#059669}
      .cpanel button.t.go:hover{background:#047857}
      .cpanel button.t:disabled{opacity:.4;cursor:default}
      .cpanel button.t:focus-visible,.cpanel .card:focus-visible{outline:2px solid #6aa8ff;outline-offset:2px}
      .cpanel .grid{overflow:auto;padding:8px 10px;display:flex;flex-direction:column;gap:6px}
      .cpanel .card{display:grid;grid-template-columns:22px 26px 132px minmax(0,1fr) auto;align-items:center;gap:10px;
        padding:6px 8px;border-radius:8px;background:rgba(255,255,255,.04);cursor:pointer;border:2px solid transparent;flex:none}
      .cpanel .card:hover{background:rgba(255,255,255,.08)}
      .cpanel .card .num{color:#888;font-size:11px;font-weight:700;text-align:right}
      .cpanel .card .pv{width:132px;height:96px;border-radius:6px;background:#000;display:block;overflow:hidden}
      .cpanel .card .pv img{width:100%;height:100%;object-fit:contain;display:block;pointer-events:none}
      .cpanel .card .info{min-width:0;display:flex;flex-direction:column;gap:3px}
      .cpanel .card .nm{font-weight:700;font-size:12.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
      .cpanel .card .sz{color:#bbb;font-size:11.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
      .cpanel .card .open{all:unset;cursor:pointer;padding:5px 9px;border-radius:6px;background:rgba(255,255,255,.1);font-size:11.5px;font-weight:700}
      .cpanel .card .open:hover{background:rgba(255,255,255,.2)}
      .cpanel .card .open:focus-visible{outline:2px solid #6aa8ff;outline-offset:2px}
      .cpanel .card .ck{width:18px;height:18px;border-radius:5px;border:2px solid #fff;background:rgba(0,0,0,.35);display:grid;place-items:center}
      .cpanel .card.sel{border-color:#10b981;background:rgba(16,185,129,.12)}
      .cpanel .card.sel .ck{background:#10b981}
      .cpanel .card.sel .ck::after{content:"";width:5px;height:9px;border:solid #fff;border-width:0 2px 2px 0;transform:rotate(45deg) translate(-1px,-1px)}
      .cpanel label.t{display:inline-flex;align-items:center;gap:6px}
      .cpanel label.t input{accent-color:#10b981;margin:0}
      .cpanel label.t.on{background:#b91c1c}
      button.cap[data-auto="1"]{background:#b91c1c;box-shadow:0 2px 10px rgba(185,28,28,.55),0 1px 3px rgba(0,0,0,.4)}
      .cpanel .empty{padding:24px 12px;color:#aaa;text-align:center}
      .dock{position:fixed;right:16px;bottom:16px;display:flex;align-items:center;gap:8px;pointer-events:auto;
        font:600 11px/1 system-ui,-apple-system,"Segoe UI",sans-serif}
      .dock[hidden]{display:none}
      .dock button{pointer-events:auto}
      .toast{position:fixed;right:16px;bottom:60px;display:flex;align-items:center;gap:10px;padding:10px 10px 10px 14px;
        background:rgba(20,20,24,.92);color:#fff;border-left:4px solid #ff0084;border-radius:8px;pointer-events:auto;
        box-shadow:0 6px 24px rgba(0,0,0,.35);font:600 12.5px/1.3 system-ui,-apple-system,"Segoe UI",sans-serif;max-width:360px}
      .toast[hidden]{display:none}
      .toast .bar{height:3px;background:rgba(255,255,255,.18);border-radius:2px;margin-top:6px;overflow:hidden}
      .toast .bar i{display:block;height:100%;width:0;background:#ff0084;transition:width .2s}
      .toast button{all:unset;cursor:pointer;padding:6px 10px;border-radius:6px;background:rgba(255,255,255,.12);font-weight:700;font-size:12px;white-space:nowrap}
      .toast button:hover{background:rgba(255,255,255,.22)}
    </style>
    <div class="wrap" data-state="idle">
      <span class="chip"></span>
      <button type="button" class="mmplus" hidden title="Download this member's whole portfolio plus their credited photos" aria-label="Download portfolio and credited photos">MM+</button>
      <button type="button" class="pplus" hidden title="Download every image in this post's gallery (full size)" aria-label="Download this Patreon post's gallery">P+</button>
      <button type="button" class="one" title="Download largest version" aria-label="Download largest version of this image">${ICONS.idle}</button>
    </div>
    <div class="dock" hidden>
      <span class="chip dchip"></span>
      <button type="button" class="cap" hidden title="Images this page has loaded (largest version of each)" aria-label="Show captured images">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 6h16M4 12h16M4 18h10" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/></svg>
        <span class="lbl">LIST</span>
      </button>
      <button type="button" class="gal" hidden title="Open each thumbnail's linked page and download the largest image on it" aria-label="Download gallery from linked pages">
        <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="3" width="7.5" height="7.5" rx="1.6" fill="none" stroke="currentColor" stroke-width="2"/><rect x="13.5" y="3" width="7.5" height="7.5" rx="1.6" fill="none" stroke="currentColor" stroke-width="2"/><rect x="3" y="13.5" width="7.5" height="7.5" rx="1.6" fill="none" stroke="currentColor" stroke-width="2"/><path d="M17.25 13.5v7m0 0-2.6-2.6m2.6 2.6 2.6-2.6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
        <span class="lbl">GALLERY</span>
      </button>
      <button type="button" class="all" hidden title="Download every image on this page" aria-label="Download all images">
        <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="7" y="3" width="14" height="14" rx="2.5" fill="none" stroke="currentColor" stroke-width="2.2"/><path d="M3.5 7.5v10A3 3 0 0 0 6.5 20.5h10" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/><path d="M14 6.5v6m0 0-2.5-2.5M14 12.5l2.5-2.5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
        <span class="lbl">ALL</span>
      </button>
    </div>
    <div class="cpanel" hidden role="dialog" aria-label="Captured images">
      <header><b class="c-title">Captured images</b><button type="button" class="t c-close" aria-label="Close">✕</button></header>
      <div class="bar">
        <button type="button" class="t c-all">Select all</button>
        <button type="button" class="t c-none">Select none</button>
        <button type="button" class="t c-clear">Clear list</button>
        <label class="t c-autolbl" title="Save each image the moment it loads (largest version)"><input type="checkbox" class="c-auto"> Auto-save as they load</label>
        <span style="flex:1"></span>
        <button type="button" class="t go c-save">Save selected</button>
      </div>
      <div class="grid"></div>
    </div>
    <div class="toast" hidden role="status">
      <div style="flex:1;min-width:0"><div class="t-text"></div><div class="bar"><i></i></div></div>
      <button type="button" class="t-btn">Cancel</button>
    </div>`;
  const wrap = root.querySelector('.wrap');
  const btn = root.querySelector('button.one');
  const btnAll = root.querySelector('button.all');
  const btnAllLabel = btnAll.querySelector('.lbl');
  const btnGal = root.querySelector('button.gal');
  const btnP = root.querySelector('button.pplus');
  const btnMM = root.querySelector('button.mmplus');
  const btnGalLabel = btnGal.querySelector('.lbl');
  const dock = root.querySelector('.dock');
  const dockChip = root.querySelector('.dchip');
  const btnCap = root.querySelector('button.cap');
  const btnCapLabel = btnCap.querySelector('.lbl');
  const cpanel = root.querySelector('.cpanel');
  const chip = root.querySelector('.wrap .chip');
  const toast = root.querySelector('.toast');
  const toastText = toast.querySelector('.t-text');
  const toastBar = toast.querySelector('.bar i');
  const toastBtn = toast.querySelector('.t-btn');

  function mount() {
    if (!host.isConnected) (document.documentElement || document.body)?.appendChild(host);
  }

  let target = null;
  let visible = false;
  let overButton = false;
  let busy = false;
  let hideTimer = 0;
  let resetTimer = 0;

  function setState(state, text) {
    wrap.dataset.state = state;
    btn.innerHTML = ICONS[state];
    if (text) { chip.textContent = text; chip.classList.add('show'); }
    else chip.classList.remove('show');
  }

  function position() {
    if (!target || !target.isConnected) return hide(true);
    const r = target.getBoundingClientRect();
    const vw = document.documentElement.clientWidth || innerWidth;
    const vh = document.documentElement.clientHeight || innerHeight;
    const visTop = Math.max(r.top, 0), visRight = Math.min(r.right, vw);
    const visBottom = Math.min(r.bottom, vh), visLeft = Math.max(r.left, 0);
    if (visBottom - visTop < 40 || visRight - visLeft < 40) return hide(true);
    wrap.style.top = `${Math.round(visTop + 8)}px`;
    wrap.style.right = `${Math.round(vw - visRight + 8)}px`;
  }

  function show() {
    mount();
    position();
    if (!target) return;
    btnP.hidden = !(IS_PATREON && patreonPostId(target));
    btnMM.hidden = !(IS_MODELMAYHEM && mmIsProfileImage(target));
    visible = true;
    wrap.classList.add('show');
  }

  function hide(force) {
    clearTimeout(hideTimer); hideTimer = 0;
    if (busy && !force) return;
    visible = false;
    wrap.classList.remove('show');
    if (!busy) { target = null; setState('idle'); }
  }

  // ───────────────────────── hover detection ─────────────────────────
  let lastX = -1, lastY = -1, rafPending = false;

  function scheduleCheck() {
    if (rafPending) return;
    rafPending = true;
    requestAnimationFrame(check);
  }

  function check() {
    rafPending = false;
    if (!isActive() || lastX < 0) return;
    if (overButton || busy) { if (visible) position(); return; }
    const el = findImageAt(lastX, lastY);
    if (el) {
      clearTimeout(hideTimer); hideTimer = 0;
      if (el !== target) { target = el; clearTimeout(resetTimer); setState('idle'); }
      show();
    } else if (visible && !hideTimer) {
      hideTimer = setTimeout(() => hide(), 250);
    }
  }

  function bgUrlOf(el) {
    const bg = getComputedStyle(el).backgroundImage;
    if (!bg || bg === 'none') return null;
    const m = bg.match(/url\(\s*(['"]?)(.*?)\1\s*\)/);
    return m ? m[2] : null;
  }

  function bigEnough(el) {
    const r = el.getBoundingClientRect();
    const min = Number(settings.minSize) || DEFAULTS.minSize;
    if (r.width >= min && r.height >= min) return true;
    // Click-to-enlarge thumbnails are often small: allow them down to 48px.
    return r.width >= ENLARGE_MIN && r.height >= ENLARGE_MIN && looksEnlargeable(el);
  }

  // ── "Click to enlarge" detection ──
  const ENLARGE_MIN = 48;
  const LIGHTBOX_MARK = /lightbox|fancybox|magnific|mfp-|pswp|photoswipe|lightgallery|lg-item|glightbox|featherlight|venobox|baguettebox|viewer|zoom|enlarge|expand|fullsize|full-size|popup-image|image-popup/i;
  const LIGHTBOX_ATTRS = ['data-fancybox', 'data-lightbox', 'data-lightbox-gallery', 'data-pswp-src', 'data-pswp-width', 'data-mfp-src',
    'data-lg-size', 'data-lg-src', 'data-glightbox', 'data-featherlight', 'data-gallery', 'data-zoomable', 'data-action', 'data-toggle'];

  // useCursor: also accept a hand / magnifier cursor (hover button only — too broad for ALL).
  function looksEnlargeable(el, useCursor = true) {
    if (!el || !el.getAttribute) return false;
    const a = el.closest('a[href]');
    if (a && IMG_EXT.test(a.href)) return true;
    let n = el;
    for (let d = 0; n && d < 4 && n !== document.body; d++, n = n.parentElement) {
      for (const attr of LIGHTBOX_ATTRS) {
        if (!n.hasAttribute(attr)) continue;
        // data-action / data-toggle are generic; only count them when they name a viewer.
        if (attr === 'data-action' || attr === 'data-toggle') {
          if (/zoom|lightbox|modal|gallery|viewer/i.test(n.getAttribute(attr))) return true;
        } else {
          return true;
        }
      }
      if (/lightbox/i.test(n.getAttribute('rel') || '')) return true;
      const cls = typeof n.className === 'string' ? n.className : '';
      if (LIGHTBOX_MARK.test(cls) || LIGHTBOX_MARK.test(n.id || '')) return true;
    }
    if (!useCursor) return false;
    try {
      for (const x of [el, el.parentElement, a].filter(Boolean)) {
        const c = getComputedStyle(x).cursor;
        if (c === 'zoom-in' || c === 'pointer') return true;
      }
    } catch { /* detached */ }
    return false;
  }

  const usableImg = (el) => el && el.tagName === 'IMG' && (el.currentSrc || el.src) && bigEnough(el);

  // Elements under the point, including inside open web components.
  function stackAt(x, y) {
    let stack;
    try { stack = document.elementsFromPoint(x, y); } catch { return []; }
    const out = [];
    for (const el of stack) {
      if (el === host) continue;
      if (el.shadowRoot && out.length < 12) {
        try { out.push(...el.shadowRoot.elementsFromPoint(x, y).filter((e) => e !== el)); } catch { /* closed */ }
      }
      out.push(el);
      if (out.length >= 14) break;
    }
    return out;
  }

  // An image under the point that the page made ignore the mouse (pointer-events: none),
  // so the browser reports the box around it instead.
  function hiddenImgAt(container, x, y) {
    if (!container || container === document.documentElement || container === document.body) return null;
    let best = null, area = 0;
    const imgs = container.querySelectorAll('img');
    for (let i = 0; i < imgs.length && i < 60; i++) {
      const im = imgs[i];
      const r = im.getBoundingClientRect();
      if (x < r.left || x > r.right || y < r.top || y > r.bottom) continue;
      if (!usableImg(im)) continue;
      const cs = getComputedStyle(im);
      if (cs.visibility === 'hidden' || cs.display === 'none' || (cs.opacity !== '' && Number(cs.opacity) === 0)) continue;
      if (r.width * r.height > area) { area = r.width * r.height; best = im; }
    }
    return best;
  }

  function findImageAt(x, y) {
    const stack = stackAt(x, y);
    for (const el of stack) if (usableImg(el)) return el;
    for (const el of stack.slice(0, 6)) {
      const im = hiddenImgAt(el, x, y) || (el.shadowRoot && hiddenImgAt(el.shadowRoot, x, y));
      if (im) return im;
    }
    for (const el of stack.slice(0, 8)) {
      if (el === document.documentElement || el === document.body) continue;
      if (bigEnough(el) && bgUrlOf(el)) return el;
    }
    return null;
  }

  document.addEventListener('mousemove', (e) => {
    lastX = e.clientX; lastY = e.clientY;
    scheduleCheck();
  }, { capture: true, passive: true });

  document.addEventListener('mouseout', (e) => {
    if (!e.relatedTarget && visible && !overButton && !busy) {
      hideTimer = setTimeout(() => hide(), 250);
    }
  }, true);

  window.addEventListener('scroll', () => { if (visible) { position(); scheduleCheck(); } }, { capture: true, passive: true });
  window.addEventListener('resize', () => { if (visible) position(); }, { passive: true });

  for (const b of [btn, btnP, btnMM]) {
    b.addEventListener('mouseenter', () => { overButton = true; clearTimeout(hideTimer); hideTimer = 0; });
    b.addEventListener('mouseleave', () => { overButton = false; scheduleCheck(); });
  }

  // Swallow pointer events so the page (links, lightboxes, drag handlers) never sees them.
  for (const el of [btn, btnP, btnMM, dock, toast, cpanel]) {
    for (const type of ['pointerdown', 'pointerup', 'mousedown', 'mouseup', 'dblclick', 'auxclick', 'contextmenu', 'dragstart']) {
      el.addEventListener(type, (e) => { e.preventDefault(); e.stopPropagation(); }, true);
    }
  }

  btn.addEventListener('click', async (e) => {
    e.preventDefault(); e.stopPropagation();
    if (busy || !target) return;
    busy = true;
    clearTimeout(resetTimer);
    const el = target;
    setState('busy', 'Finding largest…');
    try {
      const best = await downloadFrom(el);
      const dims = best.w ? `Saved ${best.w} × ${best.h}` : 'Saved';
      const tagNote = best.tags && best.tags.length ? ` · tagged ${best.tags.join(', ')}` : '';
      setState('done', (best.datedSite ? `${dims} · ${best.dated ? 'date taken added' : 'no date taken'}` : dims) + tagNote);
    } catch (err) {
      setState('error', (err && err.message) || 'Download failed');
    } finally {
      busy = false;
      resetTimer = setTimeout(() => {
        if (target === el) setState('idle');
        if (!overButton) scheduleCheck();
      }, 1800);
    }
  });

  // ───────────────────────── Flickr: download entire gallery ─────────────────────────
  const IS_FLICKR = /(^|\.)flickr\.com$/.test(location.hostname);
  const FLICKR_SIZES = ['url_o', 'url_6k', 'url_5k', 'url_4k', 'url_3k', 'url_k', 'url_h', 'url_l', 'url_c', 'url_z', 'url_m'];
  const RESERVED_USERS = new Set(['tags', 'upload', 'organize', 'friends', 'search', 'groups']);
  const bulk = { phase: 'idle', mode: 'all', items: null, title: '', key: '', kind: 'flickr', port: null, timer: 0 };

  // Work out what "the whole gallery" means from the current Flickr URL.
  function flickrContext() {
    if (!IS_FLICKR) return null;
    const p = location.pathname;
    const fl = 'https://www.flickr.com';
    let m;
    if ((m = p.match(/^\/photos\/([^/]+)\/(?:albums|sets)\/(\d+)/))) return { kind: 'album', id: m[2], user: m[1] };
    if ((m = p.match(/^\/photos\/([^/]+)\/galleries\/(\d+)/))) return { kind: 'gallery', url: `${fl}/photos/${m[1]}/galleries/${m[2]}/` };
    if ((m = p.match(/^\/photos\/([^/]+)\/favorites/))) return { kind: 'faves', user: m[1] };
    if ((m = p.match(/^\/groups\/([^/]+)\/pool/))) return { kind: 'pool', url: `${fl}/groups/${m[1]}/pool/` };
    if ((m = p.match(/^\/photos\/([^/]+)\/\d+\/in\/(.+?)\/?$/))) {
      const user = m[1], ctx = m[2];
      let c;
      if ((c = ctx.match(/^(?:album|set)-(\d+)/))) return { kind: 'album', id: c[1], user };
      if ((c = ctx.match(/^gallery-(.+)-(\d+)/))) return { kind: 'gallery', url: `${fl}/photos/${c[1]}/galleries/${c[2]}/` };
      if ((c = ctx.match(/^pool-([^/]+)/))) return { kind: 'pool', url: `${fl}/groups/${c[1]}/pool/` };
      if ((c = ctx.match(/^faves-([^/]+)/))) return { kind: 'faves', user: c[1] };
      if (/^photostream/.test(ctx)) return { kind: 'stream', user };
    }
    if ((m = p.match(/^\/photos\/([^/]+)\/?(?:page\d+\/?|with\/\d+\/?)?$/)) && !RESERVED_USERS.has(m[1])) return { kind: 'stream', user: m[1] };
    // Anything else with several photos on screen (search, explore, tags…): download what's loaded.
    return domPhotoIds().length >= 2 ? { kind: 'page' } : null;
  }

  function domPhotoIds() {
    const ids = new Set();
    for (const a of document.querySelectorAll('a[href*="/photos/"]')) {
      const m = a.getAttribute('href').match(/\/photos\/[^/]+\/(\d{6,})(?:[/?#]|$)/);
      if (m) ids.add(m[1]);
    }
    return [...ids];
  }

  let flickrAuthCache = null;
  async function flickrAuth() {
    if (flickrAuthCache) return flickrAuthCache;
    // Flickr pages carry the session token as "csrf":"…" in their config JSON.
    // Sending cookies WITHOUT it makes every API call fail with "Login Failed".
    const scan = (html) => ({
      key: (html.match(/site_key["']?\s*[:=]\s*["']([0-9a-f]{32})["']/i) || [])[1],
      csrf: (html.match(/["']csrf["']\s*:\s*["']([^"']+)["']/i) ||
             html.match(/csrf\.token["']?\s*[:=]\s*["']([^"']+)["']/i) || [])[1] || '',
    });
    let auth = scan(document.documentElement.innerHTML);
    if (!auth.key) {
      const html = await (await fetch('/', { credentials: 'include' })).text();
      auth = scan(html);
    }
    if (!auth.key) throw new Error('Couldn’t read Flickr’s API key');
    return (flickrAuthCache = auth);
  }

  async function flickrApi(method, params) {
    const auth = await flickrAuth();
    const u = new URL('https://api.flickr.com/services/rest/');
    u.search = new URLSearchParams({
      method, api_key: auth.key, format: 'json', nojsoncallback: '1',
      ...(auth.csrf ? { csrf: auth.csrf } : {}), ...params,
    }).toString();
    const get = async (creds) => {
      try {
        return await (await fetch(u.href, { credentials: creds })).json();
      } catch {
        const r = await sendMessage({ type: 'fetch-json', url: u.href }); // worker isn't bound by CORS
        if (!r || !r.ok) throw new Error('Flickr API unreachable');
        return r.json;
      }
    };
    // Signed in: cookies + csrf (needed for private/friends-only photos).
    let j = await get('include');
    // If the session isn't accepted, public content still works without cookies.
    if (j && j.stat !== 'ok' && /login/i.test(j.message || '')) j = await get('omit');
    if (!j || j.stat !== 'ok') throw new Error((j && j.message) || 'Flickr API error');
    return j;
  }

  async function flickrAllPages(method, params, pick, onPage) {
    const photos = [];
    let page = 1, pages = 1, title = '', ownerName = '';
    do {
      const j = await flickrApi(method, {
        ...params, extras: FLICKR_SIZES.join(',') + ',original_format,date_taken,owner_name,path_alias', per_page: '500', page: String(page),
      });
      const box = pick(j);
      if (!title && box.title) title = typeof box.title === 'string' ? box.title : box.title._content || '';
      if (!ownerName && box.ownername) ownerName = box.ownername; // albums report their owner once
      photos.push(...(box.photo || []));
      pages = Number(box.pages) || 1;
      onPage && onPage(photos.length, Number(box.total) || photos.length);
      page++;
    } while (page <= pages && page <= 40);
    return { photos, title, ownerName };
  }

  // ── Per-photo details for the Flickr/<username>/<album>/<title> layout ──
  const flickrAlbumCache = new Map(); // photo id -> Promise<string[] | null>
  function flickrAlbumsOf(id) {
    if (!flickrAlbumCache.has(id)) {
      flickrAlbumCache.set(id, flickrApi('flickr.photos.getAllContexts', { photo_id: id })
        .then((j) => (j.set || []).map((s) => (typeof s.title === 'string' ? s.title : (s.title && s.title._content) || '')))
        .catch(() => null)); // unknown: treated as "no single album"
    }
    return flickrAlbumCache.get(id);
  }

  // { title, owner, dateTaken, albums } for one photo (used by the single-photo button).
  async function flickrPhotoInfo(id) {
    const out = { title: '', owner: '', dateTaken: '', albums: null };
    try {
      const j = await flickrApi('flickr.photos.getInfo', { photo_id: id });
      const p = j.photo || {};
      out.title = (p.title && p.title._content) || '';
      out.owner = (p.owner && (p.owner.username || p.owner.path_alias || p.owner.nsid)) || '';
      const d = p.dates;
      if (d && String(d.takenunknown) !== '1') out.dateTaken = d.taken || '';
    } catch {
      out.dateTaken = await flickrDateTaken(id); // page fallback for the date
      const m = location.pathname.match(/^\/photos\/([^/]+)\//);
      if (m) out.owner = decodeURIComponent(m[1]);
    }
    out.albums = await flickrAlbumsOf(id);
    return out;
  }

  // Downloads\Flickr\<username>\[<album>\]<title or Flickr file name>.<ext>
  // Viewing inside an album (…/albums/<id> or …/in/album-<id>): always that album.
  // Anywhere else (photostream etc.): album folder only when the photo is in exactly one album.
  function flickrPath(it) {
    const base = decodeURIComponent((String(it.url).split(/[?#]/)[0].split('/').pop() || ''));
    const m = base.match(/^(.+?)\.(jpe?g|png|gif)$/i);
    const ext = (m ? m[2] : 'jpg').toLowerCase().replace('jpeg', 'jpg');
    const name = safeName(it.title, 120) || safeName(m ? m[1] : base, 80) || it.id || 'photo';
    const user = safeName(it.owner, 80);
    const album = it.forcedAlbum
      ? safeName(it.forcedAlbum, 80)
      : Array.isArray(it.albums) && it.albums.length === 1 ? safeName(it.albums[0], 80) : '';
    return ['Flickr', user, album, `${name}.${ext}`].filter(Boolean).join('/');
  }

  // Title of the album the user is currently viewing, or '' when not in an album.
  const flickrAlbumTitleCache = new Map();
  function currentFlickrAlbumTitle() {
    const ctx = IS_FLICKR ? flickrContext() : null;
    if (!ctx || ctx.kind !== 'album') return Promise.resolve('');
    if (!flickrAlbumTitleCache.has(ctx.id)) {
      flickrAlbumTitleCache.set(ctx.id, flickrApi('flickr.photosets.getInfo', { photoset_id: ctx.id })
        .then((j) => (j.photoset && j.photoset.title && (j.photoset.title._content || j.photoset.title)) || '')
        .catch(() => {
          // Fallback: the album page's own heading / tab title.
          const h = document.querySelector('.album-title, h1');
          return ((h && h.textContent) || document.title.replace(/\s*\|\s*Flickr.*$/i, '')).trim();
        }));
    }
    return flickrAlbumTitleCache.get(ctx.id);
  }

  async function flickrUserId(user) {
    if (/@N\d+$/.test(user)) return user;
    const j = await flickrApi('flickr.urls.lookupUser', { url: `https://www.flickr.com/photos/${user}/` });
    return j.user.id;
  }

  // Single-photo fallback: read every size from the photo page.
  async function flickrBestForId(id) {
    const res = await fetch(`/photo.gne?id=${id}`, { credentials: 'include' });
    if (!res.ok) return null;
    const html = (await res.text()).replace(/\\\//g, '/');
    const re = new RegExp(`(?:https?:)?//(?:live|farm\\d+|c\\d+)\\.staticflickr\\.com/(?:\\d+/)+${id}_[0-9a-f]+(?:_([a-z0-9]{1,2}))?\\.(?:jpg|png|gif)`, 'gi');
    const order = ['o', '6k', '5k', '4k', '3k', 'k', 'h', 'l', 'b', 'c', 'z'];
    const rank = (u) => { const s = (u.match(/_([a-z0-9]{1,2})\.\w+$/) || [])[1]; const i = order.indexOf(s); return i < 0 ? 99 : i; };
    const found = [...new Set(html.match(re) || [])].map((u) => (u.startsWith('//') ? 'https:' + u : u)).sort((a, b) => rank(a) - rank(b));
    if (!found[0]) return null;
    const info = await flickrPhotoInfo(id);
    return {
      id, url: found[0], title: info.title, owner: info.owner, albums: info.albums,
      dateTaken: info.dateTaken || dateFromPhotoPage(html, id),
    };
  }

  // The photo page embeds its data as …"dateTaken":"2026-10-22 11:45:28","datePosted":"…","id":"<id>"…
  function dateFromPhotoPage(html, id) {
    const near = new RegExp(`"dateTaken":"([^"]+)"[^{}]{0,200}?"id":"${id}"`).exec(html);
    if (near) return near[1];
    const all = [...html.matchAll(/"dateTaken":"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})"/g)].map((m) => m[1]);
    return new Set(all).size === 1 ? all[0] : ''; // only trust an unambiguous single date
  }

  async function flickrDateTaken(id, tryPage = true) {
    try {
      const j = await flickrApi('flickr.photos.getInfo', { photo_id: id });
      const d = j.photo && j.photo.dates;
      if (d) return String(d.takenunknown) !== '1' ? d.taken || '' : '';
    } catch { /* fall through to the photo page */ }
    if (!tryPage) return '';
    try {
      const res = await fetch(`/photo.gne?id=${id}`, { credentials: 'include' });
      return res.ok ? dateFromPhotoPage((await res.text()).replace(/\\\//g, '/'), id) : '';
    } catch {
      return '';
    }
  }

  function flickrIdFromUrl(u) {
    const m = String(u).match(/staticflickr\.com\/(?:\d+\/)*(\d{5,})_[0-9a-f]+/);
    return m ? m[1] : null;
  }

  async function listFromDom(onProgress) {
    const ids = domPhotoIds();
    const out = new Array(ids.length);
    let i = 0, done = 0;
    await Promise.all(Array.from({ length: 4 }, async () => {
      while (i < ids.length) {
        const k = i++;
        try { out[k] = await flickrBestForId(ids[k]); } catch { out[k] = null; }
        onProgress(++done, ids.length);
      }
    }));
    return out.filter(Boolean);
  }

  async function listFlickrGallery(ctx, onProgress) {
    const photosToItems = (photos, ownerName) => photos
      .map((ph) => {
        const k = FLICKR_SIZES.find((s) => ph[s]);
        // datetakenunknown=1 means Flickr substituted the upload date — don't embed that.
        const dateTaken = String(ph.datetakenunknown) === '1' ? '' : (ph.datetaken || '');
        const owner = ph.ownername || ownerName || ph.pathalias || ph.owner || '';
        return k ? { id: ph.id, url: ph[k], title: ph.title || '', owner, dateTaken } : null;
      })
      .filter(Boolean);
    // Which albums is each photo in? (decides the album folder)
    const addAlbums = async (items) => {
      let i = 0, done = 0;
      await Promise.all(Array.from({ length: 6 }, async () => {
        while (i < items.length) {
          const it = items[i++];
          if (it.albums === undefined) it.albums = await flickrAlbumsOf(it.id);
          onProgress(++done, items.length, 'Checking albums…');
        }
      }));
      return items;
    };
    let res, title = '';
    const prog = (n, total) => onProgress(n, total);
    try {
      if (ctx.kind === 'album') {
        res = await flickrAllPages('flickr.photosets.getPhotos', { photoset_id: ctx.id }, (j) => j.photoset, prog);
        title = res.title;
      } else if (ctx.kind === 'gallery') {
        const g = await flickrApi('flickr.urls.lookupGallery', { url: ctx.url });
        title = (g.gallery.title && g.gallery.title._content) || 'Gallery';
        res = await flickrAllPages('flickr.galleries.getPhotos', { gallery_id: g.gallery.id }, (j) => j.photos, prog);
      } else if (ctx.kind === 'pool') {
        const g = await flickrApi('flickr.urls.lookupGroup', { url: ctx.url });
        title = ((g.group.groupname && g.group.groupname._content) || 'Group') + ' pool';
        res = await flickrAllPages('flickr.groups.pools.getPhotos', { group_id: g.group.id }, (j) => j.photos, prog);
      } else if (ctx.kind === 'stream') {
        const uid = await flickrUserId(ctx.user);
        title = `${decodeURIComponent(ctx.user)} photostream`;
        res = await flickrAllPages('flickr.people.getPhotos', { user_id: uid }, (j) => j.photos, prog);
      } else if (ctx.kind === 'faves') {
        const uid = await flickrUserId(ctx.user);
        title = `${decodeURIComponent(ctx.user)} favorites`;
        try { res = await flickrAllPages('flickr.favorites.getList', { user_id: uid }, (j) => j.photos, prog); }
        catch { res = await flickrAllPages('flickr.favorites.getPublicList', { user_id: uid }, (j) => j.photos, prog); }
      }
    } catch (e) {
      res = null; // fall back to the photos visible on this page
    }
    if (res && res.photos.length) {
      const items = photosToItems(res.photos, res.ownerName);
      if (ctx.kind === 'album') {
        // Viewing an album: every photo goes in this album's folder; no per-photo album lookups needed.
        for (const it of items) it.forcedAlbum = title;
        return { items, title, partial: false };
      }
      return { items: await addAlbums(items), title, partial: false };
    }
    const items = await listFromDom(prog);
    if (ctx.kind === 'album') {
      const albumTitle = await currentFlickrAlbumTitle();
      if (albumTitle) for (const it of items) it.forcedAlbum = albumTitle;
    }
    return { items, title: document.title.replace(/\s*\|\s*Flickr.*$/i, '') || 'Flickr', partial: true };
  }

  // Chrome rejects any path component it would have to alter (emoji, invisible
  // format/bidi characters, trailing dots, reserved names…). A rejected name used
  // to fall back to no name at all, which dropped the folder — so be strict here.
  const safeName = (s, max) => {
    let t = String(s || '').normalize('NFC')
      .replace(/[\p{Cc}\p{Cf}\p{Co}\p{Cs}\p{Zl}\p{Zp}︀-️\u{E0000}-\u{E007F}\u{1F3FB}-\u{1F3FF}]/gu, '')
      .replace(/\p{Extended_Pictographic}/gu, '')
      .replace(/[\\/:*?"<>|~%#{}^`$]/g, '_')
      .replace(/\s+/g, ' ')
      .trim()
      .slice(0, max)
      .replace(/^[.\s_-]+|[.\s]+$/g, '');
    if (/^(con|prn|aux|nul|com\d|lpt\d)(\..*)?$/i.test(t)) t = '_' + t;
    return t;
  };

  // Downloads\Flickr\<username>\<album>\<title>.jpg  (no album folder when the
  // photo is in no album or in more than one)
  function buildDownloadList(items) {
    return items.map((it) => ({
      url: it.url, dateTaken: it.dateTaken || '', filename: flickrPath(it), datePrefix: flickrDatePrefix(it),
    }));
  }

  // File names always get the YYYY-MM-DD_HHMM_ prefix when the date taken is known.
  function flickrDatePrefix() {
    return true;
  }

  // Both dock buttons share one job at a time; bulk.mode says which one owns it.
  const BULK_BTNS = {
    all: { el: btnAll, label: btnAllLabel, idle: 'ALL' },
    gallery: { el: btnGal, label: btnGalLabel, idle: 'GALLERY' },
  };

  function resetBulkButton() {
    if (IS_SG) setTimeout(() => { sgBarSync(); sgListBarSync(); }, 0);
    clearTimeout(bulk.timer);
    if (bulk.phase === 'confirm' || bulk.phase === 'idle') { bulk.phase = 'idle'; bulk.items = null; }
    for (const [mode, b] of Object.entries(BULK_BTNS)) {
      const mine = bulk.mode === mode;
      b.el.dataset.phase = mine ? bulk.phase : 'idle';
      b.label.textContent = mine && bulk.phase === 'running' ? 'STOP' : b.idle;
      b.el.disabled = !mine && bulk.phase !== 'idle';
    }
    dockChip.classList.remove('show');
    updateDock();
  }

  function flashChip(text, ms = 2500) {
    dockChip.textContent = text;
    dockChip.classList.add('show');
    clearTimeout(bulk.timer);
    bulk.timer = setTimeout(() => dockChip.classList.remove('show'), ms);
  }

  // ── Bottom-right dock: shown in the top window when the page has something to grab ──
  const IS_TOP = (() => { try { return window.top === window; } catch { return false; } })();
  function updateDock() {
    if (!IS_TOP) return;
    if (document.hidden) return;
    if (IS_SG) { ensureSgBar(); ensureSgListBar(); }
    const busyJob = bulk.phase !== 'idle';
    const sgList = IS_SG && !!sgListModel();
    const allOk = isActive() && (sgList || (IS_MS && !!msOwner()) || bulkAvailable());
    const galOk = isActive() && !IS_FLICKR && !IS_YOUPIC && !IS_MS && !(IS_SG && (sgAlbum() || sgList)) && galleryCount() >= 2; // hidden on Flickr, YouPic, Model Society, SG albums/set lists
    btnAll.hidden = !(allOk || (busyJob && bulk.mode === 'all'));
    btnGal.hidden = !(galOk || (busyJob && bulk.mode === 'gallery'));
    btnAll.title = IS_FLICKR ? 'Download every photo in this Flickr gallery'
      : sgList ? 'Download every set of this model'
      : IS_MS && msOwner() ? 'Download this whole gallery (every page)' : 'Download every image on this page (largest versions)';
    const capN = settings.captureList !== false && isActive() ? capturedList().length : 0;
    btnCap.hidden = !(capN > 0 || !cpanel.hidden);
    btnCapLabel.textContent = (capN ? `LIST ${capN}` : 'LIST') + (capAuto ? ' · AUTO' : '');
    btnCap.dataset.auto = capAuto ? '1' : '';
    if (capAuto) btnCap.hidden = false;
    const showDock = !btnAll.hidden || !btnGal.hidden || !btnCap.hidden;
    if (showDock) mount();
    dock.hidden = !showDock;
  }
  if (IS_TOP) {
    setInterval(updateDock, 2000);
    document.addEventListener('visibilitychange', updateDock);
    setTimeout(updateDock, 500);
  }

  function showToast(text, pct, buttonLabel) {
    mount();
    toast.hidden = false;
    toastText.textContent = text;
    toastBar.style.width = `${Math.max(0, Math.min(100, pct))}%`;
    toastBtn.textContent = buttonLabel;
  }

  // ── "Download all" on any other site: every large image on the page ──
  function pageImages() {
    const min = Number(settings.minSize) || DEFAULTS.minSize;
    const out = [];
    for (const img of document.images) {
      if (!(img.currentSrc || img.src || img.getAttribute('data-src'))) continue;
      const r = img.getBoundingClientRect();
      if (r.width <= 0 || r.height <= 0) continue; // hidden
      const shown = r.width >= min && r.height >= min;
      const big = img.naturalWidth >= min * 2 && img.naturalHeight >= min * 2;
      const enlargeable = !shown && !big && r.width >= ENLARGE_MIN && r.height >= ENLARGE_MIN && looksEnlargeable(img, false);
      if (shown || big || enlargeable) out.push(img);
    }
    return out;
  }

  let pageCountCache = { t: 0, n: 0 };
  function pageImageCount() {
    if (Date.now() - pageCountCache.t > 1500) pageCountCache = { t: Date.now(), n: pageImages().length };
    return pageCountCache.n;
  }

  function bulkAvailable() {
    if (IS_FLICKR) return !!flickrContext();
    return pageImageCount() >= 2;
  }

  async function listPageImages(onProgress) {
    const imgs = pageImages().slice(0, 1000);
    const found = new Array(imgs.length);
    let i = 0, done = 0;
    await Promise.all(Array.from({ length: 4 }, async () => {
      while (i < imgs.length && !bulk.abort) {
        const k = i++;
        try { found[k] = await pickLargest(imgs[k]); } catch { found[k] = null; }
        onProgress(++done, imgs.length);
      }
    }));
    const seen = new Set();
    const items = [];
    for (let k = 0; k < found.length; k++) {
      const f = found[k];
      if (!f || !f.url || seen.has(f.url)) continue;
      seen.add(f.url);
      let url = f.url;
      if (url.startsWith('blob:')) { try { url = await blobUrlToDataUrl(url); } catch { continue; } }
      items.push({ url, el: imgs[k] });
    }
    await enrichYoupic(items, onProgress);
    applyThumbFolders(items);
    applyMmPids(items);
    for (const it of items) delete it.el;
    const title = document.title.trim() || location.hostname;
    return { items, title };
  }

  // YouPic: read each photo's date taken, name tag, title and username (4 at a time).
  async function enrichYoupic(items, onProgress) {
    if (IS_YOUPIC) {
      let n = 0, j = 0;
      const yp = items.filter((it) => ypKeyFromUrl(it.url));
      await Promise.all(Array.from({ length: 4 }, async () => {
        while (j < yp.length) {
          const it = yp[j++];
          try {
            const info = await youpicInfo(it.url, it.el);
            it.dateTaken = info.dateTaken;
            it.path = youpicPath(info);
          } catch {
            it.dateTaken = '';
            it.path = youpicPath({ key: ypKeyFromUrl(it.url) || '' });
          }
          onProgress(++n, yp.length, 'Reading photo details…');
        }
      }));
    }
  }

  const IMG_EXTS = /^(jpe?g|jfif|png|gif|webp|avif|bmp|tiff?|svg|heic)$/i;
  function nameAndExt(url) {
    if (url.startsWith('data:')) {
      const m = url.match(/^data:image\/([\w+.-]+)/i);
      return { name: 'image', ext: (m && MIME_EXT[m[1].toLowerCase()]) || null };
    }
    let u;
    try { u = new URL(url); } catch { return { name: 'image', ext: null }; }
    let base = u.pathname.split('/').filter(Boolean).pop() || '';
    try { base = decodeURIComponent(base); } catch { /* keep raw */ }
    base = base.replace(/:(orig|large|medium|small|thumb)$/i, '').replace(/=s0$/, '');
    const m = base.match(/^(.*)\.([a-z0-9]{2,5})$/i);
    let name = m ? m[1] : base;
    let ext = m && IMG_EXTS.test(m[2]) ? m[2].toLowerCase() : null;
    if (m && !ext) name = base.replace(/\./g, '_'); // e.g. view.php → view_php; real type found later
    const fmt = u.searchParams.get('format') || u.searchParams.get('fm');
    if (!ext && fmt && IMG_EXTS.test(fmt)) ext = fmt.toLowerCase();
    if (ext === 'jpeg' || ext === 'jfif') ext = 'jpg';
    return { name: safeName(name, 80) || 'image', ext };
  }

  // ── Default layout for other sites: <Sitename>\<Username>\<Album>\<file> ──
  // Username and album are included only when the page clearly identifies them.
  const metaContent = (sel) => {
    const el = document.querySelector(sel);
    return el ? (el.getAttribute('content') || '').trim() : '';
  };

  function siteName() {
    const og = metaContent('meta[property="og:site_name"]') || metaContent('meta[name="application-name"]');
    if (og && og.length <= 40) return og;
    // e.g. www.artstation.com → Artstation, photos.example.co.uk → Example
    const parts = location.hostname.replace(/^www\./, '').split('.');
    const sld = /^(co|com|net|org|gov|ac|edu)$/.test(parts[parts.length - 2] || '') && parts.length > 2;
    const label = parts[parts.length - (sld ? 3 : 2)] || parts[0] || location.hostname;
    return label.charAt(0).toUpperCase() + label.slice(1);
  }

  const USER_PATH = /^\/(?:@([^/]+)|(?:u|user|users|profile|profiles|people|member|members|artist|artists|author|authors|photographer|photographers)\/([^/]+))/i;
  const ALBUM_PATH = /\/(?:albums?|galler(?:y|ies)|collections?|sets?|portfolios?|folders?)\/([^/?#]+)/i;

  function jsonLdAuthor() {
    for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
      try {
        const queue = [JSON.parse(s.textContent)];
        while (queue.length) {
          const o = queue.shift();
          if (!o || typeof o !== 'object') continue;
          if (Array.isArray(o)) { queue.push(...o); continue; }
          const a = o.author || o.creator;
          const name = Array.isArray(a) ? a[0] && (a[0].name || a[0]) : a && (a.name || a);
          if (typeof name === 'string' && name.trim() && name.length <= 60 && !/^https?:/.test(name)) return name.trim();
          if (o['@graph']) queue.push(o['@graph']);
        }
      } catch { /* bad JSON-LD */ }
    }
    return '';
  }

  function pageUsername() {
    const m = location.pathname.match(USER_PATH);
    if (m) { try { return decodeURIComponent(m[1] || m[2]); } catch { return m[1] || m[2]; } }
    const tw = metaContent('meta[name="twitter:creator"]').replace(/^@/, '');
    const author = metaContent('meta[name="author"]');
    const ld = jsonLdAuthor();
    const rel = (document.querySelector('a[rel~="author"], [itemprop="author"] [itemprop="name"], [itemprop="author"]') || {}).textContent || '';
    for (const v of [ld, author, rel.trim(), tw]) if (v && v.length <= 60 && !/^https?:/.test(v)) return v;
    return '';
  }

  function pageAlbum() {
    const m = location.pathname.match(ALBUM_PATH);
    if (!m) return '';
    const h1 = (document.querySelector('h1') || {}).textContent || '';
    if (h1.trim() && h1.trim().length <= 100) return h1.trim();
    const t = (metaContent('meta[property="og:title"]') || document.title || '').split(/\s[|\-–—]\s/)[0].trim();
    if (t) return t;
    try { return decodeURIComponent(m[1]).replace(/[-_+]+/g, ' '); } catch { return m[1]; }
  }

  function genericFolder() {
    return [siteName(), pageUsername(), pageAlbum()].map((s) => safeName(s, 80)).filter(Boolean).join('/') || 'Images';
  }

  // File names that tell you nothing (image.jpg, 12.jpg, long hashes) — GALLERY swaps in the linked page's title.
  function isOpaqueName(n) {
    return /^(image|img|photo|picture|pic|full|large|original|orig|download|file|view|index|default|main|\d{1,6}|[0-9a-f_-]{20,}|[A-Za-z0-9_-]{28,})$/i.test(n);
  }

  // <folder>/<original file name>.<ext> — the worker adds YYYY-MM-DD_HHMM_ when a date taken is known.
  function buildGenericList(items) {
    const folder = genericFolder();
    return items.map((it) => {
      const dateTaken = it.dateTaken || '';
      // YouPic has its own layout: Youpic/<Account Name>/<name tag or title>
      const tags = it.tags || [];
      if (!it.path) { const sp = smugmugPath(it.url); if (sp) it.path = sp; }
      const ref = it.referer ? { referer: it.referer } : {};
      if (it.datePrefix === false) ref.datePrefix = false; // e.g. numbered albums that must stay in grid order
      if (it.path) return { url: it.url, filename: it.path, autoExt: true, dateTaken, tags, ...ref };
      const { name, ext } = nameAndExt(it.url);
      const nm = it.nameHint && isOpaqueName(name) ? (safeName(it.nameHint, 100) || name) : name;
      const stem = `${folder}/${nm}`;
      // Unknown type: the worker reads the bytes and adds the right extension.
      return ext ? { url: it.url, filename: `${stem}.${ext}`, dateTaken, tags, ...ref } : { url: it.url, filename: stem, autoExt: true, dateTaken, tags, ...ref };
    });
  }

  // ───────────── GALLERY: thumbnails whose full image lives on a linked page ─────────────
  // For each thumbnail link, open the linked page and keep ONLY the largest image on it.
  const GALLERY_MIN_THUMB = 50;
  const IMG_FILE_RE = /\.(jpe?g|jfif|png|gif|webp|avif|bmp|tiff?|heic)(?:[?#]|$)/i;

  const IS_MODELMAYHEM = /(^|\.)modelmayhem\.com$/i.test(location.hostname);
  // ModelMayhem thumbnail → full image without opening the linked page.
  function modelMayhemFull(img) {
    const src = (img && (img.currentSrc || img.src)) || '';
    const m = src.match(/^(.*)_m(\.(?:jpe?g|png|gif|webp))(?=$|[?#])/i);
    return m ? m[1] + m[2] : null;
  }

  // ModelMayhem: drop "<page title>.html" (a shortcut to this page) into the images' folder.
  // Same name every time and saved with "overwrite", so repeat downloads don't pile up copies.
  function saveModelMayhemLink(folder) {
    if (!IS_MODELMAYHEM || !folder) return;
    const url = location.href.split('#')[0];
    const esc = (t) => String(t).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    const title = document.title.replace(/\s+/g, ' ').trim() || 'ModelMayhem page';
    const html = `<!doctype html>\n<html><head><meta charset="utf-8">\n<meta http-equiv="refresh" content="0; url=${esc(url)}">\n` +
      `<title>${esc(title)}</title></head>\n<body><p>Saved from: <a href="${esc(url)}">${esc(url)}</a></p></body></html>\n`;
    const name = safeName(title, 100) || 'ModelMayhem page';
    sendMessage({
      type: 'save-link',
      url: `data:text/html;charset=utf-8,${encodeURIComponent(html)}`,
      filename: `${folder}/${name}.html`,
    }).catch(() => {});
  }

  // Generic sites: thumbnail "thumb_123.jpg" → "123.jpg" (checked like any other candidate).
  function thumbPrefixFull(img) {
    if (IS_FLICKR || IS_YOUPIC || IS_PATREON || IS_MODELMAYHEM) return null;
    const src = (img && (img.currentSrc || img.src)) || '';
    const m = src.match(/^(https?:\/\/[^?#]*\/)thumb_([^/?#]+)((?:[?#].*)?)$/i);
    return m ? m[1] + m[2] + m[3] : null;
  }

  // Generic sites with "thumb_" thumbnails: <Site>/<URL segment before the file>, e.g.
  // site.com/test/thumb_123.jpg → "Site/test". Decided by the thumbnail shown on the page.
  function thumbFolder(img) {
    if (IS_FLICKR || IS_YOUPIC || IS_PATREON || IS_MODELMAYHEM) return null;
    let u;
    try { u = new URL((img && (img.currentSrc || img.src)) || ''); } catch { return null; }
    const segs = u.pathname.split('/').filter(Boolean);
    const file = segs.pop() || '';
    if (!/^thumb_/i.test(file)) return null;
    let parent = segs.pop() || '';
    try { parent = decodeURIComponent(parent); } catch { /* keep raw */ }
    return [siteName(), parent].map((x) => safeName(x, 80)).filter(Boolean).join('/');
  }

  function applyThumbFolders(items) {
    for (const it of items) {
      if (it.path || !it.el) continue;
      const tf = thumbFolder(it.el);
      if (tf) it.path = `${tf}/${nameAndExt(it.url).name}`; // extension found from the file itself
    }
  }

  function galleryLinks() {
    const here = location.href.split('#')[0];
    const seen = new Set();
    const out = [];
    for (const img of document.images) {
      const a = img.closest('a[href]');
      if (!a || !(img.currentSrc || img.src)) continue;
      let href;
      try { href = new URL(a.getAttribute('href'), document.baseURI); } catch { continue; }
      if (!/^https?:$/.test(href.protocol)) continue;
      href.hash = '';
      if (href.href === here || (href.origin === location.origin && href.pathname === '/' && !href.search)) continue;
      const r = img.getBoundingClientRect();
      if (r.width < GALLERY_MIN_THUMB || r.height < GALLERY_MIN_THUMB) continue;
      if (seen.has(href.href)) continue;
      seen.add(href.href);
      out.push({ img, href: href.href });
    }
    return out;
  }

  let galCountCache = { t: 0, n: 0 };
  function galleryCount() {
    if (Date.now() - galCountCache.t > 1500) galCountCache = { t: Date.now(), n: galleryLinks().length };
    return galCountCache.n;
  }

  // Linked page as text. Same-site pages are fetched here (with your session);
  // other sites go through the extension worker, which isn't limited by CORS.
  async function fetchLinkedPage(url) {
    if (new URL(url).origin === location.origin) {
      try {
        const res = await fetch(url, { credentials: 'include' });
        if (res.ok) {
          const type = res.headers.get('content-type') || '';
          return { type, url: res.url || url, text: type.startsWith('image/') ? '' : (await res.text()).slice(0, 4000000) };
        }
      } catch { /* try the worker */ }
    }
    const r = await sendMessage({ type: 'fetch-text', url }).catch(() => null);
    return r && r.ok ? r : null;
  }

  // Every image the linked page offers, at the largest variant each one declares.
  function linkedPageImages(html, baseUrl) {
    const doc = new DOMParser().parseFromString(html, 'text/html');
    const urls = new Set();
    const add = (u) => {
      if (!u || typeof u !== 'string') return;
      u = u.trim();
      if (!u || u.startsWith('data:') || /[\s{}]/.test(u)) return;
      try {
        const x = new URL(u, baseUrl);
        if (/^https?:$/.test(x.protocol) && !/\.svg(?:[?#]|$)/i.test(x.pathname)) urls.add(x.href);
      } catch { /* bad URL */ }
    };
    for (const sel of ['meta[property="og:image:secure_url"]', 'meta[property="og:image"]', 'meta[name="twitter:image"]',
      'meta[name="twitter:image:src"]', 'meta[itemprop="image"]', 'meta[itemprop="contentUrl"]']) {
      for (const m of doc.querySelectorAll(sel)) add(m.getAttribute('content'));
    }
    for (const l of doc.querySelectorAll('link[rel="image_src"], link[rel="preload"][as="image"]')) add(l.getAttribute('href'));
    for (const a of doc.querySelectorAll('a[href]')) { const h = a.getAttribute('href'); if (IMG_FILE_RE.test(h)) add(h); }
    for (const s of doc.querySelectorAll('script[type="application/ld+json"]')) {
      try {
        const q = [JSON.parse(s.textContent)];
        while (q.length) {
          const o = q.shift();
          if (!o || typeof o !== 'object') continue;
          if (Array.isArray(o)) { q.push(...o); continue; }
          if (typeof o.contentUrl === 'string') add(o.contentUrl);
          if (typeof o.image === 'string') add(o.image); else if (o.image) q.push(o.image);
          if (typeof o.url === 'string' && IMG_FILE_RE.test(o.url)) add(o.url);
          if (o['@graph']) q.push(o['@graph']);
        }
      } catch { /* bad JSON-LD */ }
    }
    for (const img of doc.querySelectorAll('img, source')) {
      const sets = [...parseSrcset(img.getAttribute('srcset'), 1000), ...parseSrcset(img.getAttribute('data-srcset'), 1000)]
        .sort((a, b) => b.score - a.score);
      if (sets[0]) add(sets[0].url);
      for (const at of DATA_ATTRS) add(img.getAttribute(at));
      add(img.getAttribute('src'));
    }
    const title = ((doc.querySelector('meta[property="og:title"]') || {}).content ||
      (doc.querySelector('h1') || {}).textContent || doc.title || '').replace(/\s+/g, ' ').trim()
      .split(/\s[|–—•]\s/)[0].trim(); // drop " | Site name"
    return { urls: [...urls], title };
  }

  async function probeMany(urls) {
    let results = await Promise.all(urls.map((u) => probeImage(u)));
    const failed = urls.filter((_, i) => !results[i]);
    if (failed.length) {
      try {
        const r = await sendMessage({ type: 'probe', urls: failed });
        const map = new Map(((r && r.results) || []).filter(Boolean).map((x) => [x.url, x]));
        results = results.map((v, i) => v || map.get(urls[i]) || null);
      } catch { /* ignore */ }
    }
    return results;
  }

  // The single largest image on one linked page (or the linked file itself).
  async function largestOnLinkedPage(href, extra = []) {
    let cands = [], title = '';
    if (IMG_FILE_RE.test(new URL(href).pathname)) {
      cands = [href];
    } else {
      const page = await fetchLinkedPage(href);
      if (page && (page.type || '').startsWith('image/')) cands = [page.url || href];
      else if (page && page.text) ({ urls: cands, title } = linkedPageImages(page.text, page.url || href));
    }
    cands = [...extra, ...cands];
    if (!cands.length) return null;
    // Check every image the page lists (plus known full-size URL patterns), keep the largest.
    const list = [...new Set([...cands.flatMap((u) => upgradeVariants(u)), ...cands])].slice(0, 40);
    const found = (await probeMany(list)).filter(Boolean);
    if (!found.length) return null;
    found.sort((a, b) => (b.w * b.h) - (a.w * a.h) || list.indexOf(a.url) - list.indexOf(b.url));
    return { ...found[0], title };
  }

  async function listGallery(onProgress) {
    const links = galleryLinks().slice(0, 500);
    const results = new Array(links.length);
    let i = 0, done = 0;
    await Promise.all(Array.from({ length: 3 }, async () => {
      while (i < links.length && !bulk.abort) {
        const k = i++;
        const direct = IS_MODELMAYHEM && modelMayhemFull(links[k].img);
        const thumbGuess = thumbPrefixFull(links[k].img);
        try { results[k] = direct ? { url: direct, title: '' } : await largestOnLinkedPage(links[k].href, thumbGuess ? [thumbGuess] : []); } catch { results[k] = null; }
        onProgress(++done, links.length, 'Opening linked pages…');
      }
    }));
    const seen = new Set();
    const items = [];
    results.forEach((r, k) => {
      if (!r || seen.has(r.url)) return;
      seen.add(r.url);
      items.push({ url: r.url, el: links[k].img, nameHint: r.title });
    });
    await enrichYoupic(items, onProgress);
    applyThumbFolders(items);
    applyMmPids(items);
    for (const it of items) delete it.el;
    return { items, title: document.title.trim() || location.hostname, missed: links.length - results.filter(Boolean).length };
  }

  // ── Shared click handling for ALL and GALLERY ──
  async function onBulkClick(mode) {
    // Busy: a click on the active button stops it (scanning or downloading).
    if (bulk.phase === 'running') return bulk.mode === mode ? cancelBulk() : undefined;
    if (bulk.phase === 'listing') {
      if (bulk.mode === mode) { bulk.abort = true; flashChip('Stopping…', 4000); }
      return;
    }
    if (mode === 'all' && IS_SG && sgListModel()) return sgRunAll(); // model's set list: every set
    const ctx = mode === 'all' && IS_FLICKR ? flickrContext() : null;
    if (mode === 'all' && IS_FLICKR && !ctx) return;

    const noun = mode === 'all' && IS_FLICKR ? 'photos' : 'images';
    bulk.phase = 'listing';
    bulk.mode = mode;
    bulk.abort = false;
    bulk.items = null;
    bulk.kind = mode === 'all' && IS_FLICKR ? 'flickr' : 'page';
    resetBulkButton();
    BULK_BTNS[mode].label.textContent = 'STOP';
    flashChip(mode === 'gallery' ? 'Opening linked pages…' : `Finding ${noun}…`, 600000);
    try {
      const progress = (n, total, label) => flashChip(
        bulk.kind === 'flickr' && !label ? `Finding photos… ${n}${total > n ? ' / ' + total : ''}` : `${label || 'Finding largest versions…'} ${n} / ${total}`, 600000);
      let res;
      const sg = IS_SG ? sgItems() : null;
      const ms = IS_MS && msOwner() ? await msItems(progress) : null;
      if (ms) res = ms; // Model Society: the owner's whole gallery, every page
      else if (sg) res = sg; // SuicideGirls album: only the album grid, full size
      else if (mode === 'gallery') res = await listGallery(progress);
      else res = IS_FLICKR ? await listFlickrGallery(ctx, progress) : await listPageImages(progress);
      if (bulk.abort) throw new Error('Stopped');
      if (IS_MODELMAYHEM && !bulk.abort) {
        const ext = await mmExtend(res.items, progress);
        res.items = ext.items;
      }
      if (bulk.abort) throw new Error('Stopped');
      if (!res.items.length) throw new Error(mode === 'gallery' ? 'No full-size images found on the linked pages' : `No downloadable ${noun} found`);
      bulk.items = res.items;
      bulk.title = res.title;
      bulk.missed = mode === 'gallery' || res.msFolder ? res.missed || 0 : 0;
      if (res.msFolder) saveMsLink(res.msFolder, res.msOwner);
      startBulk(); // one click: scan, then download straight away
    } catch (err) {
      bulk.phase = 'idle';
      bulk.items = null;
      resetBulkButton();
      flashChip((err && err.message) || `Couldn’t list the ${noun} here`, 4000);
    }
  }

  btnAll.addEventListener('click', (e) => { e.preventDefault(); e.stopPropagation(); onBulkClick('all'); });
  btnGal.addEventListener('click', (e) => { e.preventDefault(); e.stopPropagation(); onBulkClick('gallery'); });

  function startBulk() {
    const list = bulk.kind === 'flickr' ? buildDownloadList(bulk.items, bulk.title) : buildGenericList(bulk.items, bulk.title);
    const noun = bulk.kind === 'flickr' ? 'photo' : 'image';
    const missed = bulk.missed || 0;
    // Shared top folders, e.g. "Flickr/NASA HQ PHOTO" (just "Flickr" for mixed owners)
    const dirs = list.map((x) => x.filename.split('/').slice(0, -1));
    let depth = 0;
    while (depth < 2 && dirs.every((d) => d[depth] !== undefined && d[depth] === dirs[0][depth])) depth++;
    const folder = dirs[0].slice(0, Math.max(1, depth)).join('/');
    if (IS_MODELMAYHEM) saveModelMayhemLink(dirs[0].join('/'));
    bulk.phase = 'running';
    bulk.items = null;
    resetBulkButton();
    showToast(`Downloading 0 / ${list.length} to ${folder}`, 0, 'Cancel');

    const port = chrome.runtime.connect({ name: 'flickr-bulk' });
    bulk.port = port;
    port.onMessage.addListener((m) => {
      if (m.type === 'progress') {
        showToast(`Downloading ${m.done + m.failed} / ${m.total} to ${folder}${m.failed ? ` (${m.failed} failed)` : ''}`,
          ((m.done + m.failed) / m.total) * 100, 'Cancel');
      } else if (m.type === 'finished') {
        let msg = m.cancelled
          ? `Stopped — saved ${m.done} of ${m.total}`
          : `Saved ${m.done} ${noun}${m.done === 1 ? '' : 's'} to Downloads/${folder}${m.failed ? ` · ${m.failed} failed` : ''}` +
            (missed ? ` · ${missed} linked page${missed === 1 ? '' : 's'} had no image` : '');
        if (!m.cancelled && m.done && (noun === 'photo' || IS_YOUPIC)) {
          if (!m.withDates) msg += ` · ${IS_YOUPIC ? 'YouPic' : 'Flickr'} gave no “date taken” for these photos`;
          else msg += ` · date taken added to ${m.dated} of ${m.done}`;
        }
        if (m.misplaced) {
          msg = `Saved ${m.done}, but ${m.misplaced} landed outside Downloads/${folder}. ` +
            'Another download extension (or download-manager integration) is overriding the save location — disable it and try again.';
        }
        showToast(msg, 100, 'Close');
        finishBulk();
        if (!m.misplaced) bulk.timer = setTimeout(() => { if (bulk.phase === 'idle') toast.hidden = true; }, 8000);
      }
    });
    port.onDisconnect.addListener(() => { if (bulk.phase === 'running') { finishBulk(); toast.hidden = true; } });
    port.postMessage({ type: 'start', items: list, referer: location.href });
  }

  function finishBulk() {
    bulk.phase = 'idle';
    try { bulk.port && bulk.port.disconnect(); } catch { /* ignore */ }
    bulk.port = null;
    resetBulkButton();
  }

  function cancelBulk() {
    bulk.abort = true; // also ends a multi-set run after the current set
    if (bulk.port) try { bulk.port.postMessage({ type: 'cancel' }); } catch { /* ignore */ }
    toastText.textContent = 'Stopping after current downloads…';
  }

  toastBtn.addEventListener('click', (e) => {
    e.preventDefault(); e.stopPropagation();
    if (bulk.phase === 'running') cancelBulk();
    else toast.hidden = true;
  });

  // ───────────────────────── YouPic: "Taken" date ─────────────────────────
  // YouPic strips metadata from its files, but each photo page embeds its data
  // as a base64 binary block. Every photo record holds its 32-byte image key
  // (the first 64 hex chars of /i/<key><size>) followed by an EXIF struct:
  //   header varint (3 bits per field: 0 = absent, else wire type),
  //   camera str, lens str, copyright str, taken varint (Unix seconds),
  //   exposure, focal, aperture (each {num,denom}), iso varint.
  const IS_YOUPIC = /(^|\.)youpic\.com$/.test(location.hostname);
  const YP_EXIF_TYPES = [4, 4, 4, 1, 5, 5, 5, 1];
  const ypPageCache = new Map(); // page path -> Promise<Uint8Array[]>

  function ypKeyFromUrl(url) {
    const m = String(url).match(/youpic\.com\/i\/([0-9a-f]{16,})[0-9a-f]{6}(?:[?#]|$)/i);
    return m && m[1].length % 2 === 0 ? m[1].toLowerCase() : null;
  }

  function ypBlobs(path) {
    if (!ypPageCache.has(path)) {
      ypPageCache.set(path, (async () => {
        const res = await fetch(path, { credentials: 'include' });
        if (!res.ok) return [];
        const html = await res.text();
        const out = [];
        for (const b64 of html.match(/[A-Za-z0-9+/]{400,}={0,2}/g) || []) {
          try {
            const bin = atob(b64);
            const u = new Uint8Array(bin.length);
            for (let i = 0; i < bin.length; i++) u[i] = bin.charCodeAt(i);
            out.push(u);
          } catch { /* not base64 */ }
        }
        return out;
      })().catch(() => []));
      if (ypPageCache.size > 200) ypPageCache.delete(ypPageCache.keys().next().value);
    }
    return ypPageCache.get(path);
  }

  function ypVarint(u, o) {
    let v = 0, sh = 0, k = o;
    while (k < u.length && k - o < 10) {
      const by = u[k++];
      v += (by & 127) * 2 ** sh;
      sh += 7;
      if (!(by & 128)) return [v, k];
    }
    return [NaN, k];
  }

  // Try to parse an EXIF struct at offset o; returns { taken } or null.
  const ypUtf8 = new TextDecoder('utf-8', { fatal: true });
  function ypParseExif(u, o) {
    const [h, q] = ypVarint(u, o);
    if (!(h > 0) || h >= 2 ** 24) return null;
    for (let f = 0; f < 8; f++) {
      const t = (h >>> (3 * f)) & 7;
      if (t !== 0 && t !== YP_EXIF_TYPES[f]) return null;
    }
    let p = q, taken = 0;
    for (let f = 0; f < 8; f++) {
      const t = (h >>> (3 * f)) & 7;
      if (!t) continue;
      if (t === 4) {
        const [len, q2] = ypVarint(u, p);
        if (!(len <= 200) || q2 + len > u.length) return null;
        const bytes = u.subarray(q2, q2 + len);
        for (const x of bytes) if (x < 32) return null;
        try { ypUtf8.decode(bytes); } catch { return null; }
        p = q2 + len;
      } else if (t === 1) {
        const [v, q2] = ypVarint(u, p);
        if (Number.isNaN(v)) return null;
        if (f === 3) taken = v;
        p = q2;
      } else { // rational {num, denom}: header 9 (both), 1 or 8 (one of them)
        const [hh, q2] = ypVarint(u, p);
        if (hh !== 9 && hh !== 1 && hh !== 8) return null;
        p = q2;
        if (hh & 7) p = ypVarint(u, p)[1];
        if ((hh >> 3) & 7) p = ypVarint(u, p)[1];
      }
      if (p > u.length) return null;
    }
    return { taken };
  }

  function ypFindKey(u, keyBytes) {
    outer: for (let i = 0; i + keyBytes.length <= u.length; i++) {
      for (let j = 0; j < keyBytes.length; j++) if (u[i + j] !== keyBytes[j]) continue outer;
      return i;
    }
    return -1;
  }

  // Another photo's image key (32 bytes, length-prefixed, recognisable shape).
  function ypIsKeyAt(u, p) {
    return u[p - 1] === 32 && p + 32 <= u.length && u[p + 20] >= 0xf0 && u[p + 21] === 0xff &&
      u[p + 23] === 0 && u[p + 24] === 0 && u[p + 25] === 0 && u[p + 26] === 0 && u[p + 27] === 0;
  }

  function ypTakenFromBlob(u, key) {
    const keyBytes = key.match(/../g).map((x) => parseInt(x, 16));
    const at = ypFindKey(u, keyBytes);
    if (at < 0) return undefined; // this photo isn't in this block
    for (let o = at + keyBytes.length; o < Math.min(u.length, at + 3000); o++) {
      // Reached a different photo's record (its own file/thumbnail pair is ≤40 bytes apart).
      if (o - at > 40 && ypIsKeyAt(u, o)) return 0;
      const ex = ypParseExif(u, o);
      if (ex) return ex.taken;
    }
    return 0;
  }

  function ypVarintBytes(dec) {
    let v = BigInt(dec);
    const out = [];
    do { let by = Number(v & 127n); v >>= 7n; if (v) by |= 128; out.push(by); } while (v);
    return out;
  }

  // Title and the plain (non-#) entries of the tags line. They're stored as
  // rich-text pieces before the photo's image keys:
  //   text piece:    33, 0, len, utf8           (title has 15,13 right before it)
  //   hashtag piece: 161,128,80, 2, len, utf8   (skipped)
  function ypTextFromBlob(u, key, postId) {
    const keyBytes = key.match(/../g).map((x) => parseInt(x, 16));
    let at = ypFindKey(u, keyBytes);
    if (at < 0) return null;
    if (ypIsKeyAt(u, at - 33)) at -= 33; // first key of this photo's file/thumbnail pair
    // Start of this photo's record: its post ID if we know it, else after the previous photo's keys.
    let start = Math.max(0, at - 1500);
    let idAt = -1;
    if (postId) {
      const idBytes = ypVarintBytes(postId);
      for (let p = at - idBytes.length; p >= start; p--) {
        let ok = true;
        for (let j = 0; j < idBytes.length; j++) if (u[p + j] !== idBytes[j]) { ok = false; break; }
        if (ok) { idAt = p; break; }
      }
    }
    if (idAt >= 0) start = idAt;
    else for (let p = at - 41; p > start; p--) if (ypIsKeyAt(u, p)) { start = p + 32; break; }

    const pieces = [];
    for (let o = start; o < at - 3; o++) {
      if (u[o] === 33 && u[o + 1] === 0) {
        const len = u[o + 2];
        if (len > 0 && len < 128 && o + 3 + len <= at) {
          const bytes = u.subarray(o + 3, o + 3 + len);
          let text = null;
          try { text = ypUtf8.decode(bytes); } catch { /* not text */ }
          if (text !== null && !/[\x00-\x08\x0b-\x1f]/.test(text)) {
            pieces.push({ kind: 'text', text, title: u[o - 1] === 13 && u[o - 2] === 15 });
            o += 2 + len;
            continue;
          }
        }
      }
      if (u[o] === 161 && u[o + 1] === 128 && u[o + 2] === 80) { // hashtag/mention entity
        const len = u[o + 4];
        if (len > 0 && o + 5 + len <= at) { pieces.push({ kind: 'tag' }); o += 4 + len; continue; }
      }
    }
    let ti = pieces.findIndex((p) => p.title);
    if (ti < 0) ti = pieces.findIndex((p) => p.kind === 'text' && p.text.trim()); // no marker: first text is the title
    const title = ti >= 0 ? pieces[ti].text.replace(/\s+/g, ' ').trim() : '';
    const name = pieces.slice(ti + 1)
      .filter((p) => p.kind === 'text')
      .map((p) => p.text)
      .join(' ')
      .split(/\s+/)
      .filter((w) => w && !w.startsWith('#'))
      .join(' ')
      .trim();
    return { title, name };
  }

  // Display name stored right after the username: …len,"pdmedia",len,"PDMedia Photography"…
  function ypAccountFromBlob(u, username) {
    const ub = [...new TextEncoder().encode(username)];
    for (let i = 1; i + ub.length + 1 < u.length; i++) {
      if (u[i - 1] !== ub.length) continue;
      let ok = true;
      for (let j = 0; j < ub.length; j++) if (u[i + j] !== ub[j]) { ok = false; break; }
      if (!ok) continue;
      const len = u[i + ub.length];
      const s = i + ub.length + 1;
      if (len > 0 && len < 100 && s + len <= u.length) {
        try {
          const name = ypUtf8.decode(u.subarray(s, s + len)).trim();
          if (name && !/[\x00-\x1f]/.test(name)) return name;
        } catch { /* keep looking */ }
      }
    }
    return '';
  }

  // "YYYY-MM-DD HH:MM:SS" in this browser's time zone — the same date YouPic shows you.
  function localStamp(sec) {
    const d = new Date(sec * 1000);
    const z = (n) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${z(d.getMonth() + 1)}-${z(d.getDate())} ${z(d.getHours())}:${z(d.getMinutes())}:${z(d.getSeconds())}`;
  }

  const YP_POST_PATH = /^\/[^/]+\/\d{6,}\/\d{6,}\/?$/;
  function ypPostPath(el) {
    const a = el && el.closest && el.closest('a[href]');
    if (a && a.origin === location.origin && YP_POST_PATH.test(a.pathname)) return a.pathname;
    return null;
  }

  // Everything we need for one YouPic photo: date taken, name tag, title, account.
  async function youpicInfo(imgUrl, el) {
    const key = ypKeyFromUrl(imgUrl);
    const info = { dateTaken: '', name: '', title: '', account: '', username: '', postId: '', key: key || '' };
    if (!key) return info;
    const linked = ypPostPath(el);
    const here = YP_POST_PATH.test(location.pathname) ? location.pathname : null;
    const pages = [...new Set([linked, here].filter(Boolean))];
    const firstSeg = (p) => decodeURIComponent((p.match(/^\/([^/]+)/) || [])[1] || '');
    for (const path of pages) {
      const blobs = await ypBlobs(path);
      for (const u of blobs) {
        const taken = ypTakenFromBlob(u, key);
        if (taken === undefined) continue; // photo not in this block
        info.postId = (path.match(/(\d{6,})\/?$/) || [])[1] || '';
        info.username = firstSeg(path);
        if (taken > 631152000 && taken < Date.now() / 1000 + 86400) info.dateTaken = localStamp(taken);
        const text = ypTextFromBlob(u, key, info.postId);
        if (text) { info.title = text.title; info.name = text.name; }
        for (const b of blobs) { info.account = ypAccountFromBlob(b, info.username); if (info.account) break; }
        return info;
      }
    }
    // Not found in page data: still use what the URL tells us.
    const p = linked || here || '';
    info.username = p ? firstSeg(p) : '';
    info.postId = (p.match(/(\d{6,})\/?$/) || [])[1] || '';
    return info;
  }

  // Downloads\Youpic\<username>\<name tag | title | post id> (extension added later)
  function youpicPath(info) {
    const account = safeName(info.username, 80) || safeName(info.account, 80) || 'Unknown user';
    const base = safeName(info.name, 100) || safeName(info.title, 100) || info.postId || info.key.slice(0, 16) || 'image';
    return `Youpic/${account}/${base}`;
  }

  // ───────────────────────── Patreon: "P+" — whole post gallery ─────────────────────────
  // Uses your existing Patreon login in this browser: requests go from the patreon.com
  // page itself with its cookies. Nothing is read out, stored or sent anywhere else.
  const IS_PATREON = /(^|\.)patreon\.com$/.test(location.hostname);
  const postIdFrom = (h) => (String(h || '').match(/\/posts\/(?:[^/?#]*-)?(\d{4,})(?:[/?#]|$)/) || [])[1] || null;

  function patreonPostId(el) {
    if (!el || !IS_PATREON) return null;
    // 1) A post card in a feed: its own link to the post.
    const card = el.closest && el.closest('[data-tag="post-card"]');
    if (card) {
      const a = card.querySelector('a[href*="/posts/"]');
      const id = a && postIdFrom(a.getAttribute('href'));
      if (id) return id;
    }
    // 2) A single post page.
    const here = postIdFrom(location.pathname);
    if (here) return here;
    // 3) Nearest container that links to exactly one post.
    let n = el.parentElement;
    for (let d = 0; n && d < 14; d++, n = n.parentElement) {
      const ids = new Set([...n.querySelectorAll('a[href*="/posts/"]')].map((a) => postIdFrom(a.getAttribute('href'))).filter(Boolean));
      if (ids.size === 1) return [...ids][0];
      if (ids.size > 1) break;
    }
    return null;
  }

  async function patreonApiPost(id) {
    const base = { include: 'images,media,attachments_media,campaign,user', 'json-api-version': '1.0' };
    const fields = {
      'fields[post]': 'title,published_at,post_type,url,current_user_can_view,image,post_file',
      'fields[media]': 'id,image_urls,download_url,metadata,file_name',
      'fields[campaign]': 'name,url',
      'fields[user]': 'full_name,url',
      'json-api-use-default-includes': 'false',
    };
    const get = async (params) => {
      const res = await fetch(`/api/posts/${id}?${new URLSearchParams(params)}`, {
        credentials: 'include', headers: { Accept: 'application/vnd.api+json' },
      });
      return res.ok ? res.json() : Promise.reject(new Error(`Patreon returned ${res.status}`));
    };
    try { return await get({ ...base, ...fields }); } catch { return get(base); } // retry without field lists
  }

  const IMG_NAME = /\.(jpe?g|png|gif|webp|avif|bmp|tiff?|heic)$/i;
  const lastSeg = (u) => { try { return new URL(u, location.href).pathname.split('/').filter(Boolean).pop() || ''; } catch { return ''; } };

  // Gallery images (in post order) at their original size, from Patreon's post data.
  async function patreonGalleryFromApi(id) {
    const j = await patreonApiPost(id);
    const post = j.data || {};
    const attrs = post.attributes || {};
    const rel = post.relationships || {};
    const inc = new Map((j.included || []).map((x) => [`${x.type}:${x.id}`, x]));
    const refs = (r) => ((rel[r] && rel[r].data) || []);
    let media = refs('images');
    if (!media.length) media = refs('media'); // older posts keep their images here
    const urls = [];
    for (const ref of media) {
      const m = inc.get(`media:${ref.id}`);
      const a = (m && m.attributes) || {};
      const iu = a.image_urls || {};
      const name = a.file_name || '';
      if (name && !IMG_NAME.test(name) && !iu.original) continue; // not an image (video, zip…)
      const url = a.download_url || iu.original || iu.default || iu.url;
      if (url) urls.push({ url, name });
    }
    if (!urls.length) { // single-image post
      const pf = attrs.post_file || {};
      const big = (pf.url && (!pf.name || IMG_NAME.test(pf.name)) && pf.url) || (attrs.image && (attrs.image.large_url || attrs.image.url));
      if (big) urls.push({ url: big, name: pf.name || '' });
    }
    const camp = [...inc.values()].find((x) => x.type === 'campaign');
    const user = [...inc.values()].find((x) => x.type === 'user');
    const creator = (camp && (lastSeg(camp.attributes && camp.attributes.url) || (camp.attributes && camp.attributes.name))) ||
      (user && user.attributes && (lastSeg(user.attributes.url) || user.attributes.full_name)) || '';
    return { urls, title: attrs.title || '', creator, published: attrs.published_at || '', canView: attrs.current_user_can_view !== false };
  }

  // Backup: open the gallery image and step through with the right arrow, as you would by hand.
  async function patreonGalleryFromLightbox(img) {
    const before = new Set(document.images);
    const pick = () => {
      let best = null, area = 0;
      for (const im of document.images) {
        if (!(im.currentSrc || im.src)) continue;
        const r = im.getBoundingClientRect();
        if (r.width < 200 || r.height < 200) continue;
        if (!(im.closest('[role="dialog"], [aria-modal="true"]') || !before.has(im))) continue;
        if (r.width * r.height > area) { area = r.width * r.height; best = im; }
      }
      return best;
    };
    const waitFor = async (test, ms) => {
      for (let t = 0; t < ms; t += 150) { const v = test(); if (v) return v; await new Promise((r) => setTimeout(r, 150)); }
      return null;
    };
    const key = (k, code) => (document.activeElement || document.body).dispatchEvent(
      new KeyboardEvent('keydown', { key: k, code: k, keyCode: code, which: code, bubbles: true, cancelable: true }));
    img.click();
    let cur = await waitFor(pick, 4000);
    const urls = [];
    while (cur && urls.length < 300) {
      const u = cur.currentSrc || cur.src;
      if (urls.some((x) => x.url === u)) break; // wrapped around to the first image
      urls.push({ url: u, name: '' });
      key('ArrowRight', 39);
      cur = await waitFor(() => { const n = pick(); return n && (n.currentSrc || n.src) !== u ? n : null; }, 3000);
    }
    key('Escape', 27);
    return urls;
  }

  function patreonCreatorFromPage() {
    const seg = location.pathname.split('/').filter(Boolean);
    if (seg[0] === 'c' && seg[1]) return seg[1];
    if (seg[0] && !/^(posts|home|messages|settings|notifications|explore|search|library)$/.test(seg[0])) return seg[0];
    return '';
  }

  async function patreonGalleryItems(el) {
    const id = patreonPostId(el);
    if (!id) throw new Error('Couldn’t tell which post this is');
    let res = { urls: [], title: '', creator: '', canView: true };
    try { res = await patreonGalleryFromApi(id); } catch { /* use the lightbox below */ }
    if (!res.urls.length && el && el.tagName === 'IMG') {
      res.urls = await patreonGalleryFromLightbox(el);
    }
    if (!res.urls.length) throw new Error(res.canView ? 'No gallery images found in this post' : 'This post’s images aren’t available to your account');
    const creator = safeName(res.creator || patreonCreatorFromPage(), 80) || 'Unknown creator';
    if (!res.title) { // lightbox route: read the title from the post on the page
      const card = el && el.closest && el.closest('[data-tag="post-card"]');
      const t = card && (card.querySelector('[data-tag="post-title"]') || card.querySelector('a[href*="/posts/"]'));
      const h1 = postIdFrom(location.pathname) === id && document.querySelector('h1');
      res.title = ((t && t.textContent) || (h1 && h1.textContent) || '').replace(/\s+/g, ' ').trim();
    }
    const title = safeName(res.title, 100) || `Post ${id}`;
    // Folder: "<YYYY-MM-DD> <Post name>" using the publish date in this browser's time zone.
    const pub = res.published ? new Date(res.published) : null;
    const z = (n) => String(n).padStart(2, '0');
    const day = pub && !isNaN(pub) ? `${pub.getFullYear()}-${z(pub.getMonth() + 1)}-${z(pub.getDate())}` : '';
    const album = day ? `${day} ${title}` : title;
    const width = Math.max(2, String(res.urls.length).length);
    // Downloads\Patreon\<creator>\<post title>\<file> — worker adds YYYY-MM-DD_HHMM_ when the file has a date taken.
    const items = res.urls.map((x, i) => {
      let stem = (x.name || lastSeg(x.url)).replace(/\.[a-z0-9]{2,5}$/i, '');
      stem = safeName(stem, 100);
      if (!stem || isOpaqueName(stem)) stem = `${title} ${String(i + 1).padStart(width, '0')}`;
      return { url: x.url, path: `Patreon/${creator}/${album}/${stem}` };
    });
    return { items, title };
  }

  btnP.addEventListener('click', async (e) => {
    e.preventDefault(); e.stopPropagation();
    if (busy || !target) return;
    if (bulk.phase !== 'idle') { setState('error', 'Another download is still running'); return; }
    const el = target;
    busy = true;
    clearTimeout(resetTimer);
    setState('busy', 'Reading post gallery…');
    try {
      const { items, title } = await patreonGalleryItems(el);
      bulk.mode = 'patreon';
      bulk.kind = 'page';
      bulk.items = items;
      bulk.title = title;
      bulk.missed = 0;
      startBulk();
      setState('done', `Downloading ${items.length} image${items.length === 1 ? '' : 's'}`);
    } catch (err) {
      setState('error', (err && err.message) || 'Couldn’t read this post');
    } finally {
      busy = false;
      resetTimer = setTimeout(() => { if (target === el) setState('idle'); if (!overButton) scheduleCheck(); }, 2500);
    }
  });

  // ───────────────────────── ModelMayhem: portfolio + credited photos ─────────────────────────
  // Everything for one member goes in Downloads\Modelmayhem\<Member name>\.
  function mmIdFromUrl(path = location.pathname) {
    const m = String(path).match(/\/portfolio\/(?:credited\/)?(\d{3,})(?:[/?#]|$)/);
    return m ? m[1] : null;
  }

  // The "Model Mayhem #:" field on a profile page.
  let mmProfileCache = { href: '', id: null };
  function mmIdFromProfile() {
    if (mmProfileCache.href === location.href) return mmProfileCache.id;
    let id = null;
    for (const row of document.querySelectorAll('.details_row')) {
      const n = row.querySelector('.field-name');
      if (n && /Model Mayhem #/i.test(n.textContent)) {
        const m = ((row.querySelector('.field-data') || {}).textContent || '').match(/\d{3,}/);
        if (m) { id = m[0]; break; }
      }
    }
    if (!id) { const m = (document.body ? document.body.textContent : '').match(/Model Mayhem #:?\s*(\d{3,})/); if (m) id = m[1]; }
    mmProfileCache = { href: location.href, id };
    return id;
  }

  const mmCurrentId = () => mmIdFromUrl() || mmIdFromProfile();

  // The main profile picture: its avatar path spells out the member number (/avatars/3/0/8/0/4/4/7/…).
  function mmIsProfileImage(el) {
    if (!el || el.tagName !== 'IMG') return false;
    const m = (el.currentSrc || el.src || '').match(/\/avatars\/((?:\d\/)+)/);
    const id = mmIdFromProfile();
    return !!(m && id && m[1].replace(/\//g, '') === id);
  }

  const mmFullFromSrc = (src) => {
    const m = String(src || '').match(/^(.*\/photos\/.*?)_m(\.(?:jpe?g|png|gif|webp))(?=$|[?#])/i);
    return m ? m[1] + m[2] : null;
  };

  // Whole portfolio (the "view all" page lists every photo) + the member's name.
  const mmPortfolioCache = new Map();
  function mmPortfolio(id) {
    if (!mmPortfolioCache.has(id)) {
      mmPortfolioCache.set(id, (async () => {
        const res = await fetch(`/portfolio/${id}/viewall`, { credentials: 'include' });
        if (!res.ok) throw new Error(`ModelMayhem returned ${res.status}`);
        const doc = new DOMParser().parseFromString(await res.text(), 'text/html');
        const urls = [];
        const pids = {};
        for (const img of doc.querySelectorAll('img')) {
          const src = img.getAttribute('data-src') || img.getAttribute('src');
          if (!src) continue;
          const full = mmFullFromSrc(new URL(src, location.href).href);
          if (!full) continue;
          urls.push(full);
          const a = img.closest('a[href*="/portfolio/pic/"]');
          const pid = a && mmPidFrom(a.getAttribute('href'));
          if (pid && !pids[full]) pids[full] = pid;
        }
        const name = ((doc.title || '').match(/^(.+?)['’]s photo portfolio/i) || [])[1] || '';
        return { urls: [...new Set(urls)], name: name.trim(), pids };
      })().catch((e) => { mmPortfolioCache.delete(id); throw e; }));
    }
    return mmPortfolioCache.get(id);
  }

  // Credited photos (photos other members credited this member in), full size.
  // Same request ModelMayhem's own "Credited Photos" tab makes; hidden ones are skipped.
  async function mmCredits(id) {
    const tok = (document.querySelector('meta[name="MMSERVICE"]') || {}).content || '';
    try {
      const res = await fetch(`/services/credits/getCreditedPhotos/${id}`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'X-Requested-With': 'XMLHttpRequest', 'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8' },
        body: `MMSERVICE=${encodeURIComponent(tok)}`,
      });
      if (!res.ok) return [];
      const j = await res.json();
      return ((j && j.credited_photos) || [])
        .filter((p) => p && p.is_hidden === false)
        .map((p) => ({ u: p.big_image || mmFullFromSrc(p.image), pid: p.pid ? String(p.pid) : null,
          credit: { owner: String(p.owner_name || '').trim(), group: String(p.owner_group || '') } }))
        .filter((x) => x.u)
        .map((x) => ({ url: new URL(x.u, location.href).href, pid: x.pid, credit: x.credit }));
    } catch {
      return []; // no credits page for this member
    }
  }

  async function mmFolder(id) {
    let name = '';
    try { name = (await mmPortfolio(id)).name; } catch { /* use the number */ }
    return `Modelmayhem/${safeName(name, 80) || id}`;
  }

  // ── Tags: who is in each photo ──
  // Photographer's (or other creative's) portfolio → the profiles credited as Model on the photo page.
  // Model's portfolio → the model (the name at the top of the photo page). Other credits ignored.
  const mmPidFrom = (h) => (String(h || '').match(/\/portfolio\/pic\/(\d+)/) || [])[1] || null;
  const isModelType = (t) => /\bmodel\b/i.test(t || '') && !/photographer/i.test(t || '');

  function mmPidForEl(el) {
    const here = mmPidFrom(location.pathname);
    if (here) { // photo page: the main photo is this page's photo (its link may point to the next one)
      const og = (document.querySelector('meta[property="og:image"]') || {}).content || '';
      const src = (el && (el.currentSrc || el.src)) || '';
      if (og && (mmFullFromSrc(src) || src) === og) return here;
    }
    const a = el && el.closest && el.closest('a[href*="/portfolio/pic/"]');
    return (a && mmPidFrom(a.getAttribute('href'))) || here;
  }

  const mmPicCache = new Map();
  function mmPicInfo(pid) {
    if (!mmPicCache.has(pid)) {
      mmPicCache.set(pid, (async () => {
        const res = await fetch(`/portfolio/pic/${pid}`, { credentials: 'include' });
        if (!res.ok) return null;
        const doc = new DOMParser().parseFromString(await res.text(), 'text/html');
        const h1 = ((doc.querySelector('#pic_title') || doc.querySelector('h1') || {}).textContent || '').replace(/\s+/g, ' ').trim();
        if (!h1) return null; // owner blocked viewing ("you are not allowed…")
        const og = ((doc.querySelector('meta[property="og:title"]') || {}).content || '').trim();
        // Heading is "<Owner> - <photo title>", or just "<Owner>" when the photo has no title.
        const owner = og && og !== h1 && h1.endsWith(` - ${og}`) ? h1.slice(0, -(og.length + 3)).trim() : h1;
        const prof = [...doc.querySelectorAll('a[href]')].find((a) => a.textContent.trim() === 'Profile');
        const credits = [...doc.querySelectorAll('#pic_credits a')].map((a) => ({
          name: a.textContent.replace(/\s+/g, ' ').trim(),
          role: ((a.nextElementSibling && a.nextElementSibling.tagName === 'SPAN' && a.nextElementSibling.textContent) || '').trim(),
        })).filter((c) => c.name);
        return { owner, profile: prof ? prof.getAttribute('href') : '', credits };
      })().catch(() => null));
    }
    return mmPicCache.get(pid);
  }

  const mmTypeCache = new Map();
  function mmOwnerType(profileHref) {
    if (!profileHref) return Promise.resolve('');
    if (!mmTypeCache.has(profileHref)) {
      mmTypeCache.set(profileHref, (async () => {
        const res = await fetch(profileHref, { credentials: 'include' });
        if (!res.ok) return '';
        const doc = new DOMParser().parseFromString(await res.text(), 'text/html');
        const t = ((doc.querySelector('#user_type') || {}).textContent || '').replace(/\s+/g, ' ').trim();
        return t || ((doc.title || '').match(/ (Female |Male )?([A-Za-z/ ]+?) Profile - /) || [])[2] || '';
      })().catch(() => ''));
    }
    return mmTypeCache.get(profileHref);
  }

  async function mmTagsFor(pid) {
    if (!pid) return [];
    const info = await mmPicInfo(pid);
    if (!info) return [];
    const type = await mmOwnerType(info.profile);
    const models = [...new Set(info.credits.filter((c) => /\bmodel\b/i.test(c.role)).map((c) => c.name))];
    if (isModelType(type)) return info.owner ? [info.owner] : [];
    if (type) return models; // photographer / other creative: the credited model(s)
    // Owner's profile is hidden: judge from the photo's own credits.
    if (models.length) return models;
    if (info.credits.some((c) => /photographer/i.test(c.role))) return info.owner ? [info.owner] : [];
    return [];
  }

  // Tags for a batch. Photos from a model's own portfolio don't need their pages opened:
  // the tag is the model. Everything else reads its photo page (4 at a time).
  async function mmApplyTags(items, ownerId, onProgress) {
    let ownerModel = '';
    if (ownerId) {
      try {
        if (isModelType(await mmOwnerType(`/${ownerId}`))) ownerModel = (await mmPortfolio(ownerId)).name || '';
      } catch { /* look each photo up instead */ }
    }
    // Credited photo whose page can't be read: use the credits list's owner name and type.
    const creditFallback = (it) => {
      if (!it.credit) return [];
      if (isModelType(it.credit.group) && it.credit.owner) return [it.credit.owner];
      if (it.credit.group && ownerModel) return [ownerModel]; // photographer's photo crediting this model
      return [];
    };
    const todo = [];
    for (const it of items) {
      if (it.own && ownerModel) it.tags = [ownerModel];
      else if (it.pid) todo.push(it);
    }
    let i = 0, done = 0;
    await Promise.all(Array.from({ length: 4 }, async () => {
      while (i < todo.length && !bulk.abort) {
        const it = todo[i++];
        try { it.tags = await mmTagsFor(it.pid); } catch { it.tags = []; }
        if (!it.tags.length && !(await mmPicInfo(it.pid))) it.tags = creditFallback(it);
        onProgress && onProgress(++done, todo.length, 'Reading photo credits for tags…');
      }
    }));
    return items;
  }

  // pid for items found on the page (ALL / GALLERY); "own" = photo from this member's portfolio.
  function applyMmPids(items) {
    if (!IS_MODELMAYHEM) return;
    const ownPage = /\/portfolio\/\d{3,}(?:[/?#]|$)/.test(location.pathname); // not /credited/, not /pic/
    for (const it of items) {
      if (!it.el) continue;
      const pid = mmPidForEl(it.el);
      if (pid) it.pid = pid;
      if (ownPage) it.own = true;
    }
  }

  const mmItems = (list, folder) => list.map((x) => ({ ...x, path: `${folder}/${nameAndExt(x.url).name}` }));

  // ALL / GALLERY on a ModelMayhem member page: add their credited photos, same folder, then tag.
  async function mmExtend(items, onProgress) {
    const id = mmCurrentId();
    let all = items, added = 0;
    if (id) {
      onProgress && onProgress(0, 1, 'Reading credited photos…');
      const [folder, credits] = await Promise.all([mmFolder(id), mmCredits(id)]);
      const seen = new Set(items.map((it) => it.url));
      const extra = credits.filter((c) => !seen.has(c.url)).map((c) => ({ url: c.url, pid: c.pid, credit: c.credit }));
      all = [...items, ...extra];
      added = extra.length;
      for (const it of all) if (!it.path) it.path = `${folder}/${nameAndExt(it.url).name}`;
    }
    await mmApplyTags(all, id, onProgress);
    return { items: all, added };
  }

  btnMM.addEventListener('click', async (e) => {
    e.preventDefault(); e.stopPropagation();
    if (busy || !target) return;
    if (bulk.phase !== 'idle') { setState('error', 'Another download is still running'); return; }
    const el = target;
    const id = mmIdFromProfile();
    busy = true;
    clearTimeout(resetTimer);
    setState('busy', 'Reading portfolio and credits…');
    try {
      const [port, credits, folder] = await Promise.all([
        mmPortfolio(id).catch(() => ({ urls: [], pids: {} })), mmCredits(id), mmFolder(id)]);
      const credOnly = credits.filter((c) => !port.urls.includes(c.url));
      const list = [...port.urls.map((url) => ({ url, pid: port.pids[url] || null, own: true })), ...credOnly];
      if (!list.length) throw new Error('No portfolio or credited photos found');
      const items = mmItems(list, folder);
      await mmApplyTags(items, id, (n, total) => setState('busy', `Reading photo credits for tags… ${n} / ${total}`));
      bulk.mode = 'mm';
      bulk.kind = 'page';
      bulk.items = items;
      bulk.title = folder;
      bulk.missed = 0;
      startBulk();
      setState('done', `Downloading ${port.urls.length} portfolio + ${credOnly.length} credited`);
    } catch (err) {
      setState('error', (err && err.message) || 'Couldn’t read this profile');
    } finally {
      busy = false;
      resetTimer = setTimeout(() => { if (target === el) setState('idle'); if (!overButton) scheduleCheck(); }, 3000);
    }
  });

  // ───────────────────────── LIST: images the page has loaded ─────────────────────────
  // Records every image the page loads (img tags, CSS, scripts, lightboxes…). When a thumbnail
  // and a bigger copy of the same picture both load, only the biggest is kept.
  const captured = new Map(); // picture key -> { url, w, h, t }
  const capSeen = new Set();  // URLs already looked at
  const capSelected = new Set();
  const SIZE_PARAMS_RE = /^(w|h|width|height|size|s|q|quality|fit|crop|dpr|format|fm|auto|resize|scale|maxwidth|maxheight|mw|mh|wid|hei|sz|imwidth|name)$/i;

  // Same picture, different size → same key.
  function captureKey(url) {
    if (url.startsWith('data:') || url.startsWith('blob:')) return url;
    let u;
    try { u = new URL(url); } catch { return url; }
    const sm = smugmugParts(url);
    if (sm) return `sm:${u.host}${sm.prefix}${sm.base}`.toLowerCase();
    const yk = ypKeyFromUrl(url);
    if (yk) return `yp:${yk}`;
    let p = u.pathname;
    try { p = decodeURIComponent(p); } catch { /* keep raw */ }
    p = p.toLowerCase();
    const fl = /staticflickr\.com$/.test(u.hostname) && p.match(/\/(\d{5,})_[0-9a-f]+(?:_[a-z0-9]{1,2})?\.\w+$/);
    if (fl) return `flickr:${fl[1]}`;
    p = p.replace(/\.(jpe?g|jfif|png|gif|webp|avif|bmp|heic)$/, '')
      .replace(/\/thumb_([^/]+)$/, '/$1')
      .replace(/_m$/, '')
      .replace(/[-_](\d{2,4}x\d{2,4}|\d{2,4}w|small|medium|large|thumb|thumbnail|preview|orig|original|full|scaled|xl|lg|sm|md)$/, '')
      .replace(/\/(thumbs?|thumbnails?|small|medium|large|preview|full|orig|originals?|\d{2,4}x\d{0,4}|w\d{2,4}|s\d{2,4}(?:-c)?)\//g, '/')
      .replace(/=[swh]\d+.*$/, '');
    const keep = [...u.searchParams].filter(([k]) => !SIZE_PARAMS_RE.test(k)).map(([k, v]) => `${k}=${v}`).sort().join('&');
    return `${u.hostname.replace(/^www\./, '')}${p}${keep ? `?${keep}` : ''}`;
  }

  function capturedList() {
    const min = Number(settings.minSize) || DEFAULTS.minSize;
    return [...captured.values()].filter((c) => c.w >= min && c.h >= min).sort((a, b) => b.t - a.t);
  }

  // Images a page builds itself (blob: links) usually stop working right after they're shown,
  // so keep our own copy the moment they load.
  const capCopies = new Map(); // blob: url -> Blob
  async function keepCopy(url, img) {
    if (!url.startsWith('blob:') || capCopies.has(url)) return;
    let b = null;
    try { b = await (await fetch(url)).blob(); } catch { b = null; }
    if ((!b || !b.size) && img && img.naturalWidth) { // already revoked: copy the pixels from the picture on screen
      try {
        const c = document.createElement('canvas');
        c.width = img.naturalWidth; c.height = img.naturalHeight;
        c.getContext('2d').drawImage(img, 0, 0);
        b = await new Promise((r) => c.toBlob(r, 'image/png'));
      } catch { b = null; }
    }
    if (b && b.size) capCopies.set(url, b);
  }
  const blobToDataUrl = (b) => new Promise((res, rej) => { const fr = new FileReader(); fr.onload = () => res(fr.result); fr.onerror = () => rej(fr.error); fr.readAsDataURL(b); });
  async function savableUrl(url) {
    if (!url.startsWith('blob:')) return url;
    const b = capCopies.get(url);
    if (b) return blobToDataUrl(b);
    try { return await blobUrlToDataUrl(url); } catch { return null; }
  }

  // ── Auto-save: each image saved the moment it loads (largest version) ──
  let capAuto = false, capAutoSince = 0, capAutoCount = 0;
  const capAutoSaved = new Map(); // key -> largest area already saved
  const capAutoTimers = new Map();
  const AUTO_SETTLE_MS = 1500; // a bigger copy loading right after the thumbnail wins
  function capAutoSchedule(key) {
    clearTimeout(capAutoTimers.get(key));
    capAutoTimers.set(key, setTimeout(async () => {
      capAutoTimers.delete(key);
      const c = captured.get(key);
      if (!capAuto || !c) return;
      const min = Number(settings.minSize) || DEFAULTS.minSize;
      if (c.w < min || c.h < min) return;
      if ((capAutoSaved.get(key) || 0) >= c.w * c.h) return;
      capAutoSaved.set(key, c.w * c.h);
      const url = await savableUrl(c.url);
      if (!url) return;
      const [entry] = buildGenericList([{ url }]);
      try {
        const r = await sendMessage({ type: 'download', url, filename: entry.filename, autoExt: !!entry.autoExt, datePrefix: true, dateTaken: '', tags: [], saveAs: false, referer: c.ref || location.href });
        if (r && r.ok) { capAutoCount++; flashChip(`Auto-saved ${capAutoCount} · ${c.w} × ${c.h}`, 2500); }
      } catch { /* try again if a bigger version arrives */ capAutoSaved.delete(key); }
    }, AUTO_SETTLE_MS));
  }

  let capDockTimer = 0;
  function capRecord(url, w, h, ref) {
    if (!w || !h) return;
    if (!IS_TOP) { // inside an embedded frame: hand it to the main page's LIST
      const min = Number(settings.minSize) || DEFAULTS.minSize;
      if (w < min || h < min) return;
      (async () => {
        const u = url.startsWith('blob:') ? await savableUrl(url) : url;
        if (u) sendMessage({ type: 'cap-relay', url: u, w, h, ref: location.href }).catch(() => {});
      })();
      return;
    }
    const key = captureKey(url);
    const old = captured.get(key);
    if (old && old.w * old.h >= w * h) return;
    if (old && capSelected.has(old.url)) { capSelected.delete(old.url); capSelected.add(url); }
    const now = Date.now();
    captured.set(key, { url, w, h, t: old ? old.t : now, ref: ref || location.href });
    if (capAuto && now >= capAutoSince) capAutoSchedule(key);
    clearTimeout(capDockTimer);
    capDockTimer = setTimeout(() => { updateDock(); if (!cpanel.hidden) renderCapPanel(); }, 400);
  }

  const capQueue = [];
  let capRunning = 0;
  function capNote(url, w, h, ref) {
    if (settings.captureList === false || !isActive()) return;
    if (!url || typeof url !== 'string' || /^data:image\/svg|\.svg(?:[?#]|$)/i.test(url)) return;
    if (url.startsWith('data:') && url.length < 3000) return; // tiny placeholders
    const key = `${url}|${w || 0}`;
    if (capSeen.has(key)) return;
    capSeen.add(key);
    if (w && h) return capRecord(url, w, h, ref);
    capQueue.push({ url, ref });
    pumpCapQueue();
  }
  function pumpCapQueue() {
    while (capRunning < 3 && capQueue.length) {
      const { url, ref } = capQueue.shift();
      capRunning++;
      probeImage(url, 10000).then((r) => { if (r) capRecord(url, r.w, r.h, ref); }).finally(() => { capRunning--; pumpCapQueue(); });
    }
  }

  {
    try { performance.setResourceTimingBufferSize(5000); } catch { /* ignore */ }
    // Every image request the page makes (including ones it never puts on screen).
    const NOT_IMAGE = /\.(woff2?|ttf|otf|eot|css|js|mjs|json|html?|svg|mp4|webm|m3u8|mp3)(?:[?#]|$)/i;
    try {
      new PerformanceObserver((list) => {
        for (const e of list.getEntries()) {
          const ct = e.contentType || ''; // what the server said it sent (when the browser exposes it)
          if (ct) { if (/^image\//i.test(ct) && !/svg/i.test(ct)) capNote(e.name); continue; }
          if (e.initiatorType === 'img' || e.initiatorType === 'image' || IMG_EXT.test(e.name)) capNote(e.name);
          // Background pictures (common on sites that block right-click), whatever their address looks like.
          else if (e.initiatorType === 'css' && !NOT_IMAGE.test(e.name)) capNote(e.name);
        }
      }).observe({ type: 'resource', buffered: true });
    } catch { /* not supported */ }
    // Images as they finish loading, with their real size (also catches blob: and cached images).
    document.addEventListener('load', (e) => {
      const t = e.target;
      if (!t || !t.tagName) return;
      if (t.tagName === 'IMG') {
        const u = t.currentSrc || t.src;
        if (settings.captureList !== false && isActive() && u && u.startsWith('blob:')) keepCopy(u, t);
        capNote(u, t.naturalWidth, t.naturalHeight);
      } else if (t.tagName.toLowerCase() === 'image' && t.href && t.href.baseVal) { // <image> inside SVG viewers
        try { capNote(new URL(t.href.baseVal, document.baseURI).href); } catch { /* bad URL */ }
      }
    }, true);
    // Images already on the page when the extension started.
    const scanExisting = () => { for (const im of document.images) if (im.complete && im.naturalWidth) capNote(im.currentSrc || im.src, im.naturalWidth, im.naturalHeight); };
    // Deferred: runs after the rest of this script has finished setting up.
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => setTimeout(scanExisting, 0));
    else setTimeout(scanExisting, 0);
  }

  // ── Panel ──
  const capGrid = cpanel.querySelector('.grid');
  const capTitle = cpanel.querySelector('.c-title');
  const capSaveBtn = cpanel.querySelector('.c-save');

  function renderCapPanel() {
    const list = capturedList();
    const urls = new Set(list.map((c) => c.url));
    for (const u of [...capSelected]) if (!urls.has(u)) capSelected.delete(u);
    capTitle.textContent = `Captured images (${list.length})`;
    capSaveBtn.textContent = capSelected.size ? `Save selected (${capSelected.size})` : `Save all (${list.length})`;
    capSaveBtn.disabled = !list.length || bulk.phase !== 'idle';
    capGrid.textContent = '';
    if (!list.length) {
      const e = document.createElement('div');
      e.className = 'empty';
      e.textContent = 'No images captured yet. Browse the page, open photos or scroll, and they’ll appear here.';
      capGrid.appendChild(e);
      return;
    }
    const order = [...list].sort((a, b) => a.t - b.t).map((c) => c.url); // number = order loaded
    for (const c of list) {
      const card = document.createElement('div');
      card.className = `card${capSelected.has(c.url) ? ' sel' : ''}`;
      card.tabIndex = 0;
      card.setAttribute('role', 'checkbox');
      card.setAttribute('aria-checked', String(capSelected.has(c.url)));
      card.title = c.url;
      const ck = document.createElement('span');
      ck.className = 'ck';
      const num = document.createElement('span');
      num.className = 'num';
      num.textContent = String(order.indexOf(c.url) + 1);
      const pv = document.createElement('span');
      pv.className = 'pv';
      const img = document.createElement('img');
      img.loading = 'lazy';
      img.decoding = 'async';
      img.alt = '';
      img.src = c.url;
      pv.appendChild(img);
      const info = document.createElement('span');
      info.className = 'info';
      const nm = document.createElement('span');
      nm.className = 'nm';
      const n = nameAndExt(c.url);
      nm.textContent = c.url.startsWith('blob:') || c.url.startsWith('data:') ? 'Image made by the page' : (n.ext ? `${n.name}.${n.ext}` : n.name);
      const sz = document.createElement('span');
      sz.className = 'sz';
      let host = '';
      try { host = /^https?:/.test(c.url) ? new URL(c.url).hostname.replace(/^www\./, '') : ''; } catch { /* no host */ }
      sz.textContent = `${c.w} × ${c.h}${host ? ` · ${host}` : ''}`;
      info.append(nm, sz);
      const open = document.createElement('button');
      open.type = 'button';
      open.className = 'open';
      open.textContent = 'Open';
      open.title = 'View full size in a new tab';
      open.addEventListener('click', async (e) => {
        e.preventDefault(); e.stopPropagation();
        const u = c.url.startsWith('blob:') && capCopies.has(c.url) ? URL.createObjectURL(capCopies.get(c.url)) : c.url;
        window.open(u, '_blank', 'noopener');
      });
      card.append(ck, num, pv, info, open);
      const toggle = () => {
        if (capSelected.has(c.url)) capSelected.delete(c.url); else capSelected.add(c.url);
        renderCapPanel();
      };
      card.addEventListener('click', (e) => { e.preventDefault(); e.stopPropagation(); toggle(); });
      card.addEventListener('keydown', (e) => { if (e.key === ' ' || e.key === 'Enter') { e.preventDefault(); toggle(); } });
      capGrid.appendChild(card);
    }
  }

  const capBtn = (sel, fn) => cpanel.querySelector(sel).addEventListener('click', (e) => { e.preventDefault(); e.stopPropagation(); fn(); });
  capBtn('.c-close', () => { cpanel.hidden = true; updateDock(); });
  capBtn('.c-all', () => { for (const c of capturedList()) capSelected.add(c.url); renderCapPanel(); });
  capBtn('.c-none', () => { capSelected.clear(); renderCapPanel(); });
  capBtn('.c-clear', () => { captured.clear(); capSeen.clear(); capSelected.clear(); capCopies.clear(); renderCapPanel(); updateDock(); });
  capBtn('.c-save', async () => {
    if (bulk.phase !== 'idle') return;
    const list = capturedList();
    const pick = capSelected.size ? list.filter((c) => capSelected.has(c.url)) : list;
    if (!pick.length) return;
    const items = [];
    for (const c of pick) { const url = await savableUrl(c.url); if (url) items.push({ url, referer: c.ref }); }
    if (!items.length) { flashChip('Those images are no longer available on the page', 4000); return; }
    bulk.mode = 'list';
    bulk.kind = 'page';
    bulk.items = items;
    bulk.title = document.title.trim() || location.hostname;
    bulk.missed = 0;
    cpanel.hidden = true;
    startBulk();
  });
  const capAutoBox = cpanel.querySelector('.c-auto');
  const capAutoLbl = cpanel.querySelector('.c-autolbl');
  for (const ev of ['click', 'pointerdown', 'mousedown']) capAutoLbl.addEventListener(ev, (e) => e.stopPropagation());
  capAutoBox.addEventListener('change', () => {
    capAuto = capAutoBox.checked;
    capAutoLbl.classList.toggle('on', capAuto);
    if (capAuto) { capAutoSince = Date.now(); capAutoCount = 0; flashChip('Auto-save on: new images save as they load', 3500); }
    else { for (const t of capAutoTimers.values()) clearTimeout(t); capAutoTimers.clear(); flashChip(`Auto-save off · ${capAutoCount} saved`, 3000); }
    updateDock();
  });

  btnCap.addEventListener('click', (e) => {
    e.preventDefault(); e.stopPropagation();
    cpanel.hidden = !cpanel.hidden;
    if (!cpanel.hidden) { mount(); renderCapPanel(); }
    updateDock();
  });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !cpanel.hidden) { cpanel.hidden = true; updateDock(); } }, true);

  // ───────────────────────── SuicideGirls album pages ─────────────────────────
  // /girls/ or /members/<model>/album/<id>/<set-slug>/ — the album grid (ul.photos-container inside
  // #album-content-<id>) links each thumbnail straight to its full-size photo. Only that grid is
  // used: profile picture, comment avatars, tags and "You Might Also Like" sets are left out.
  // Uses your own logged-in session (the signed photo links on the page).
  const IS_SG = /(^|\.)suicidegirls\.com$/i.test(location.hostname);

  function sgAlbum() {
    if (!IS_SG) return null;
    const m = location.pathname.match(/^\/(?:girls|members)\/([^/]+)\/album\/(\d+)(?:\/([^/]*))?/i);
    const grid = document.querySelector('[id^="album-content-"] ul.photos-container') || document.querySelector('ul.photos-container');
    if (!m || !grid) return null;
    let model = m[1];
    try { model = decodeURIComponent(model); } catch { /* keep raw */ }
    let slug = m[3] || '';
    try { slug = decodeURIComponent(slug); } catch { /* keep raw */ }
    slug = slug.replace(/[-_]+/g, ' ').trim();
    const norm = (t) => t.toLowerCase().replace(/[^a-z0-9]+/g, '');
    // Set name as written on the page (e.g. "Something Blue"), else from the address.
    const set = [...document.querySelectorAll('h1,h2,h3')].map((h) => h.textContent.replace(/\s+/g, ' ').trim())
      .find((t) => slug && norm(t) === norm(slug)) || slug.replace(/\b\w/g, (c) => c.toUpperCase()) || `Album ${m[2]}`;
    return { model, set, grid, date: sgPostDate(set) };
  }

  // Date the set was posted, YYYY-MM-DD. The album header shows "Nov 8" for this year,
  // "Aug 19, 2017" for older sets, or "11 hrs" / "2 days" for very recent ones.
  function sgParseDate(text, now = new Date()) {
    const t = String(text || '').replace(/\s+/g, ' ').trim();
    const z = (n) => String(n).padStart(2, '0');
    const fmt = (d) => `${d.getFullYear()}-${z(d.getMonth() + 1)}-${z(d.getDate())}`;
    const months = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'];
    let m = t.match(/^([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2})(?:,?\s+(\d{4}))?$/);
    if (m) {
      const mi = months.indexOf(m[1].toLowerCase());
      if (mi < 0) return '';
      let d = new Date(m[3] ? +m[3] : now.getFullYear(), mi, +m[2]);
      if (!m[3] && d - now > 2 * 86400000) d = new Date(now.getFullYear() - 1, mi, +m[2]); // e.g. "Dec 20" seen in January
      return fmt(d);
    }
    if (/^(just now|now)$/i.test(t)) return fmt(now);
    if (/^yesterday$/i.test(t)) return fmt(new Date(now - 86400000));
    m = t.match(/^(\d+)\s*(s|secs?|seconds?|m|mins?|minutes?|h|hrs?|hours?|d|days?|w|wks?|weeks?)\b/i);
    if (m) {
      const unit = m[2].toLowerCase()[0];
      const ms = { s: 1e3, m: 6e4, h: 36e5, d: 864e5, w: 6048e5 }[unit] || 0;
      return fmt(new Date(now - (+m[1]) * ms));
    }
    return '';
  }

  function sgPostDate(set, doc = document) {
    const headers = [...doc.querySelectorAll('header')];
    const own = headers.find((h) => set && h.textContent.includes(set) && h.querySelector('.meta-data time'));
    const t = (own || doc).querySelector('.meta-data time');
    return t ? sgParseDate(t.textContent) : '';
  }

  // Album photos in the order you see the grid: left→right along row 1, then row 2, …
  function sgPhotoLinks(grid) {
    const links = [...grid.querySelectorAll('li.photo-container a[href], li a[href]')]
      .filter((a, i, all) => all.indexOf(a) === i)
      .filter((a) => { try { return /\/photos\/[^/]+\.(jpe?g|png|gif|webp)$/i.test(new URL(a.href).pathname); } catch { return false; } });
    const boxes = links.map((a, i) => {
      const r = (a.closest('li') || a).getBoundingClientRect();
      return { a, i, top: r.top, left: r.left, h: r.height };
    });
    if (boxes.some((b) => !b.h)) return links; // not laid out: keep page order
    boxes.sort((x, y) => x.top - y.top || x.left - y.left);
    const rows = [];
    for (const b of boxes) {
      const row = rows[rows.length - 1];
      if (row && Math.abs(b.top - row[0].top) < row[0].h / 2) row.push(b); else rows.push([b]);
    }
    return rows.flatMap((row) => row.sort((x, y) => x.left - y.left)).map((b) => b.a);
  }

  // Downloads\…\SuicideGirls\<model>\<set>\<set> NN  (photo files are only named by a code)
  function sgPath(album, n, total) {
    const set = safeName(album.set, 100) || 'Album';
    const folder = album.date ? `${album.date} ${set}` : set; // posted date on the folder only
    const nn = String(n).padStart(Math.max(2, String(total).length), '0');
    return `SuicideGirls/${safeName(album.model, 80) || 'Unknown'}/${folder}/${set} ${nn}`;
  }

  function sgItems() {
    const album = sgAlbum();
    if (!album) return null;
    const links = sgPhotoLinks(album.grid);
    return { items: links.map((a, i) => ({ url: a.href, path: sgPath(album, i + 1, links.length), datePrefix: false })), title: album.set };
  }

  // ── Download button in the album's black title bar ──
  let sgBar = null; // { host, btn, label, header }
  function ensureSgBar() {
    if (!isActive()) { if (sgBar) { sgBar.host.remove(); sgBar = null; } return; }
    const album = sgAlbum();
    if (!album) { if (sgBar) { sgBar.host.remove(); sgBar = null; } return; }
    const h2 = [...document.querySelectorAll('header h2, header .title')].find((h) => h.textContent.replace(/\s+/g, ' ').trim() === album.set);
    const header = h2 && h2.closest('header');
    const bar = header && (header.querySelector('.top-bar') || header);
    if (!bar) return;
    if (sgBar && sgBar.host.isConnected && sgBar.bar === bar) { sgBarPlace(); return; }
    if (sgBar) sgBar.host.remove();

    const host = document.createElement('max-image-saver-bar');
    host.setAttribute('style', 'all:initial;position:absolute;z-index:5;top:0;right:14px;display:block;line-height:0;');
    const sr = host.attachShadow({ mode: 'closed' });
    const accent = (() => { try { return getComputedStyle(h2).color || '#e8798f'; } catch { return '#e8798f'; } })();
    sr.innerHTML = `
      <style>
        button{all:unset;box-sizing:border-box;height:34px;padding:0 14px 0 11px;display:inline-flex;align-items:center;gap:7px;
          background:${accent};color:#fff;border-radius:3px;cursor:pointer;white-space:nowrap;
          font:700 11.5px/1 gotham_boldregular,"Gotham",system-ui,-apple-system,"Segoe UI",sans-serif;letter-spacing:.08em;text-transform:uppercase;
          box-shadow:0 1px 3px rgba(0,0,0,.35);transition:filter .12s}
        button:hover{filter:brightness(1.1)}
        button:focus-visible{outline:2px solid #fff;outline-offset:2px}
        button:disabled{opacity:.45;cursor:default;filter:none}
        button[data-phase=listing],button[data-phase=running]{background:#b91c1c}
        svg{width:15px;height:15px;flex:none}
      </style>
      <button type="button" title="Download every photo in this set, full size, numbered in grid order">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4.5v10m0 0-4.2-4.2M12 14.5l4.2-4.2M5.5 19h13" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>
        <span class="lbl"></span>
      </button>`;
    const btn = sr.querySelector('button');
    const label = sr.querySelector('.lbl');
    // Keep the site's own click handlers (lightbox, links) away from this button.
    for (const ev of ['pointerdown', 'pointerup', 'mousedown', 'mouseup', 'dblclick', 'contextmenu']) {
      host.addEventListener(ev, (e) => e.stopPropagation());
    }
    btn.addEventListener('click', (e) => { e.preventDefault(); e.stopPropagation(); onBulkClick('all'); });
    if (getComputedStyle(header).position === 'static') header.style.position = 'relative';
    header.appendChild(host);
    sgBar = { host, btn, label, header, bar };
    sgBarSync();
    sgBarPlace();
  }

  // Vertically centred in the black title bar, at its right end.
  function sgBarPlace() {
    if (!sgBar) return;
    const { host, header, bar } = sgBar;
    const hb = header.getBoundingClientRect();
    const bb = bar.getBoundingClientRect();
    host.style.top = `${Math.round(bb.top - hb.top + (bb.height - 34) / 2)}px`;
    host.style.right = '14px';
  }

  function sgBarSync() {
    if (!sgBar) return;
    const { btn, label } = sgBar;
    const n = (() => { const a = sgAlbum(); return a ? sgPhotoLinks(a.grid).length : 0; })();
    const mine = bulk.mode === 'all';
    const phase = mine ? bulk.phase : 'idle';
    btn.dataset.phase = phase;
    btn.disabled = !mine && bulk.phase !== 'idle';
    label.textContent = phase === 'listing' || phase === 'running' ? 'Stop' : `Download set${n ? ` (${n})` : ''}`;
  }

  // ───────────────────────── Model Society ─────────────────────────
  // Gallery: /photographer/<slug>/photos or /model/<slug>/photos (pages ?page=2, 3, …).
  // Each thumbnail opens /image/<id>, whose page data names the image (largest public size,
  // 1200 px wide), its posted date, and the tagged MODEL / PHOTOGRAPHER.
  // Saved as Modelsociety/<gallery owner>/<YYYY-MM-DD model>[ NN] — NN only when one model has
  // several photos posted the same day — plus a shortcut to the owner's first gallery page.
  const IS_MS = /(^|\.)modelsociety\.com$/i.test(location.hostname);
  const MS_MAX = 'rs:fit:1200:0'; // biggest size the image server hands out

  function msOwner(pathname = location.pathname, doc = document) {
    if (!IS_MS) return null;
    const m = pathname.match(/^\/(photographer|model)\/([^/]+)(?:\/photos)?\/?$/i);
    if (!m) return null;
    const h1 = doc.querySelector('h1');
    let slug = m[2];
    try { slug = decodeURIComponent(slug); } catch { /* keep raw */ }
    const name = (h1 && h1.textContent.replace(/\s+/g, ' ').trim()) || slug.replace(/-/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
    return { type: m[1].toLowerCase(), slug: m[2], name, gallery: `${location.origin}/${m[1].toLowerCase()}/${m[2]}/photos` };
  }

  function msFull(src) {
    return String(src || '').replace(/\/edge\/rs:fit:\d+:\d+\//, `/edge/${MS_MAX}/`);
  }

  function msIdFrom(str) {
    const m = String(str || '').match(/\/images?\/(\d+)(?:[/?#]|$)/);
    return m ? m[1] : '';
  }

  // Local YYYY-MM-DD of the posted time.
  function msDay(iso) {
    const d = new Date(iso);
    if (isNaN(d)) return '';
    const z = (n) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${z(d.getMonth() + 1)}-${z(d.getDate())}`;
  }

  // One photo's page: image, posted day, title, models, photographers, uploader.
  function msParsePhoto(doc, id) {
    const objs = [];
    for (const s of doc.querySelectorAll('script[type="application/ld+json"]')) {
      try { objs.push(JSON.parse(s.textContent)); } catch { /* skip */ }
    }
    const img = objs.flat(4).find((o) => o && o['@type'] === 'ImageObject') || {};
    const role = (re) => [...new Set([...doc.querySelectorAll('main a[href], a[href]')]
      .map((a) => a.textContent.replace(/\s+/g, ' ').trim())
      .filter((t) => re.test(t)).map((t) => t.replace(re, '').trim()).filter(Boolean))];
    let url = img.contentUrl || '';
    if (!url) {
      const pic = [...doc.images].find((i) => msIdFrom(i.getAttribute('src')) === id);
      url = pic ? msFull(pic.getAttribute('src')) : '';
    }
    let date = msDay(img.uploadDate || img.datePublished || '');
    if (!date) {
      const pm = (doc.body ? doc.body.textContent : '').match(/Posted\s+([A-Z][a-z]+ \d{1,2}, \d{4})/);
      if (pm) date = msDay(pm[1]);
    }
    return {
      id, url: msFull(url), date,
      title: String(img.name || '').trim(),
      models: role(/^MODEL\s*\/\s*/i),
      photographers: role(/^PHOTOGRAPHER\s*\/\s*/i),
      creator: (img.creator && img.creator.name) || '',
      creatorUrl: (img.creator && img.creator.url) || '',
    };
  }

  async function msPhotoInfo(id) {
    const r = await fetch(`/image/${id}`, { credentials: 'include' });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return msParsePhoto(new DOMParser().parseFromString(await r.text(), 'text/html'), id);
  }

  // "2026-06-24 ohlivia" — model(s) when tagged, else the photo title, else the owner.
  function msBaseName(info, ownerName) {
    const who = info.models.length ? info.models.join(' & ')
      : (info.title && !/^untitled$/i.test(info.title) ? info.title : ownerName || info.creator || 'Untitled');
    return [info.date, safeName(who, 90)].filter(Boolean).join(' ') || `Image ${info.id}`;
  }

  // NN added only where one name (date + model) would repeat; numbered oldest upload first.
  function msPaths(infos, owner) {
    const folder = `Modelsociety/${safeName(owner.name, 80) || 'Unknown'}`;
    const groups = new Map();
    for (const it of infos) {
      const base = msBaseName(it, owner.name);
      if (!groups.has(base)) groups.set(base, []);
      groups.get(base).push(it);
    }
    for (const [base, list] of groups) {
      list.sort((a, b) => Number(a.id) - Number(b.id));
      const w = Math.max(2, String(list.length).length);
      list.forEach((it, i) => { it.path = `${folder}/${base}${list.length > 1 ? ' ' + String(i + 1).padStart(w, '0') : ''}`; });
    }
    return folder;
  }

  function msTags(info) {
    return [...new Set([...info.models, ...info.photographers])];
  }

  // Shortcut to the owner's first gallery page, kept next to the photos.
  function saveMsLink(folder, owner) {
    if (!folder || !owner) return;
    const url = owner.gallery;
    const esc = (t) => String(t).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    const title = `${owner.name} - Model Society ${owner.type === 'model' ? 'model' : 'photographer'} gallery`;
    const html = `<!doctype html>\n<html><head><meta charset="utf-8">\n<meta http-equiv="refresh" content="0; url=${esc(url)}">\n` +
      `<title>${esc(title)}</title></head>\n<body><p>Saved from: <a href="${esc(url)}">${esc(url)}</a></p></body></html>\n`;
    sendMessage({
      type: 'save-link',
      url: `data:text/html;charset=utf-8,${encodeURIComponent(html)}`,
      filename: `${folder}/${safeName(owner.name, 80) || 'Gallery'} gallery.html`,
    }).catch(() => {});
  }

  // Every photo id in the owner's gallery, page after page until no new photos turn up.
  async function msGalleryIds(owner, onProgress) {
    const ids = [];
    for (let page = 1; page <= 300 && !bulk.abort; page++) {
      onProgress(ids.length, 0, `Reading gallery page ${page}…`);
      let html;
      try {
        const r = await fetch(`/${owner.type}/${owner.slug}/photos${page > 1 ? `?page=${page}` : ''}`, { credentials: 'include' });
        if (!r.ok) break;
        html = await r.text();
      } catch { break; }
      const doc = new DOMParser().parseFromString(html, 'text/html');
      const cards = doc.querySelectorAll('[data-ms-feed-card] a[href*="/image/"]');
      const scope = cards.length ? cards : doc.querySelectorAll('a[href^="/image/"]');
      let fresh = 0;
      for (const a of scope) {
        const id = msIdFrom(new URL(a.getAttribute('href'), location.href).pathname);
        if (id && !ids.includes(id)) { ids.push(id); fresh++; }
      }
      if (!fresh) break;
    }
    return ids;
  }

  async function msItems(onProgress) {
    const owner = msOwner();
    if (!owner) return null;
    const ids = await msGalleryIds(owner, onProgress);
    const infos = [];
    let next = 0;
    let done = 0;
    const worker = async () => {
      while (next < ids.length && !bulk.abort) {
        const id = ids[next++];
        try {
          const info = await msPhotoInfo(id);
          if (info.url) infos.push(info);
        } catch { /* counted as missed */ }
        onProgress(++done, ids.length, 'Opening photos…');
      }
    };
    await Promise.all(Array.from({ length: 4 }, worker));
    const folder = msPaths(infos, owner);
    infos.sort((a, b) => ids.indexOf(a.id) - ids.indexOf(b.id));
    return {
      items: infos.map((it) => ({ url: it.url, path: it.path, datePrefix: false, tags: msTags(it) })),
      title: owner.name, missed: ids.length - infos.length, msFolder: folder, msOwner: owner,
    };
  }

  // ───────────────────────── SuicideGirls: every set of a model ─────────────────────────
  // /girls/ or /members/<model>/photos/ lists the model's sets; more appear through "Load more"
  // (/photos/?partial=true&offset=N), sometimes several times. Each set is then opened (fetched
  // with your session) just before it downloads — its signed photo links only last about an hour —
  // and saved exactly like the single-set button does.
  function sgListModel(pathname = location.pathname) {
    if (!IS_SG) return null;
    const m = pathname.match(/^\/(?:girls|members)\/([^/]+)\/photos\/?$/i);
    if (!m) return null;
    try { return decodeURIComponent(m[1]); } catch { return m[1]; }
  }

  // Album links (in page order) found in a list page or a "Load more" chunk.
  function sgAlbumLinks(root, model) {
    const out = [];
    const cards = root.querySelectorAll('article.type-album, article[class*="album"]');
    const scope = cards.length ? [...cards] : [root];
    for (const card of scope) {
      for (const a of card.querySelectorAll('a[href]')) {
        let u;
        try { u = new URL(a.getAttribute('href'), location.href); } catch { continue; }
        const m = u.pathname.match(/^\/(?:girls|members)\/([^/]+)\/album\/(\d+)(?:\/([^/?#]*))?/i);
        if (!m || u.hostname !== location.hostname) continue;
        let who = m[1];
        try { who = decodeURIComponent(who); } catch { /* keep raw */ }
        if (model && who.toLowerCase() !== model.toLowerCase()) continue;
        if (out.some((x) => x.id === m[2])) continue;
        const t = card !== root && card.querySelector('time');
        out.push({ id: m[2], url: `${u.origin}${m[0].replace(/\/?$/, '/')}`, cardDate: t ? sgParseDate(t.textContent) : '' });
      }
    }
    return out;
  }

  // Everything behind "Load more", however many times it would need clicking.
  async function sgAllAlbums(model, onProgress) {
    const albums = sgAlbumLinks(document, model);
    const base = location.pathname.replace(/\/?$/, '/');
    for (let guard = 0; guard < 200 && !bulk.abort; guard++) {
      onProgress(albums.length);
      let html;
      try {
        const r = await fetch(`${base}?partial=true&offset=${albums.length}`, { credentials: 'include' });
        if (!r.ok) break;
        html = await r.text();
      } catch { break; }
      const doc = new DOMParser().parseFromString(html, 'text/html');
      const fresh = sgAlbumLinks(doc, model).filter((a) => !albums.some((x) => x.id === a.id));
      if (!fresh.length) break;
      albums.push(...fresh);
    }
    return albums;
  }

  // Open one set and build its download list (same names and order as the single-set button).
  async function sgFetchAlbumItems(album) {
    const r = await fetch(album.url, { credentials: 'include' });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const doc = new DOMParser().parseFromString(await r.text(), 'text/html');
    const u = new URL(r.url || album.url);
    const m = u.pathname.match(/^\/(?:girls|members)\/([^/]+)\/album\/(\d+)(?:\/([^/]*))?/i) || album.url.match(/\/(?:girls|members)\/([^/]+)\/album\/(\d+)(?:\/([^/]*))?/i);
    let model = m ? m[1] : 'Unknown';
    try { model = decodeURIComponent(model); } catch { /* keep raw */ }
    let slug = (m && m[3]) || '';
    try { slug = decodeURIComponent(slug); } catch { /* keep raw */ }
    slug = slug.replace(/[-_]+/g, ' ').trim();
    const norm = (t) => t.toLowerCase().replace(/[^a-z0-9]+/g, '');
    const set = [...doc.querySelectorAll('h1,h2,h3')].map((h) => h.textContent.replace(/\s+/g, ' ').trim())
      .find((t) => slug && norm(t) === norm(slug)) || slug.replace(/\b\w/g, (c) => c.toUpperCase()) || `Album ${album.id}`;
    const grid = doc.querySelector(`#album-content-${album.id} ul.photos-container`) ||
      doc.querySelector('[id^="album-content-"] ul.photos-container') || doc.querySelector('ul.photos-container');
    if (!grid) return { set, items: [] };
    // The fetched page isn't laid out, so its order is the page order — the same as the grid rows.
    const urls = [...grid.querySelectorAll('li a[href]')]
      .map((a) => { try { return new URL(a.getAttribute('href'), u).href; } catch { return ''; } })
      .filter((h, i, all) => h && all.indexOf(h) === i)
      .filter((h) => /\/photos\/[^/]+\.(jpe?g|png|gif|webp)$/i.test(new URL(h).pathname));
    const info = { model, set, date: sgPostDate(set, doc) || album.cardDate };
    return { set, items: urls.map((url, i) => ({ url, path: sgPath(info, i + 1, urls.length), datePrefix: false })) };
  }

  // Runs one download job in the background and resolves with its "finished" report.
  function runPortJob(list, onProgress) {
    return new Promise((resolve) => {
      const port = chrome.runtime.connect({ name: 'flickr-bulk' });
      bulk.port = port;
      let done = false;
      port.onMessage.addListener((m) => {
        if (m.type === 'progress') onProgress(m);
        else if (m.type === 'finished') { done = true; try { port.disconnect(); } catch { /* ignore */ } bulk.port = null; resolve(m); }
      });
      port.onDisconnect.addListener(() => { if (!done) { bulk.port = null; resolve({ done: 0, failed: list.length, total: list.length, cancelled: true }); } });
      port.postMessage({ type: 'start', items: list, referer: location.href });
    });
  }

  async function sgRunAll() {
    const model = sgListModel();
    if (!model) return;
    bulk.phase = 'listing';
    bulk.mode = 'all';
    bulk.abort = false;
    bulk.items = null;
    resetBulkButton();
    BULK_BTNS.all.label.textContent = 'STOP';
    const tot = { done: 0, failed: 0, sets: 0, empty: 0, misplaced: 0 };
    let albums = [];
    try {
      flashChip('Finding sets…', 600000);
      albums = await sgAllAlbums(model, (n) => flashChip(`Finding sets… ${n}`, 600000));
      if (bulk.abort) throw new Error('Stopped');
      if (!albums.length) throw new Error('No sets found on this page');
      dockChip.classList.remove('show');
      bulk.phase = 'running';
      resetBulkButton();
      const folder = `SuicideGirls/${safeName(model, 80) || 'Unknown'}`;
      for (let i = 0; i < albums.length && !bulk.abort; i++) {
        const head = `Set ${i + 1} / ${albums.length}`;
        showToast(`${head} · opening…`, (i / albums.length) * 100, 'Stop');
        let res;
        try { res = await sgFetchAlbumItems(albums[i]); } catch { tot.empty++; continue; }
        if (bulk.abort) break;
        if (!res.items.length) { tot.empty++; continue; }
        const list = buildGenericList(res.items, res.set);
        const m = await runPortJob(list, (p) => showToast(
          `${head} · ${res.set} · ${p.done + p.failed} / ${p.total}${tot.failed + p.failed ? ` (${tot.failed + p.failed} failed)` : ''}`,
          ((i + (p.done + p.failed) / p.total) / albums.length) * 100, 'Stop'));
        tot.done += m.done || 0;
        tot.failed += m.failed || 0;
        tot.misplaced += m.misplaced || 0;
        if (!m.cancelled) tot.sets++;
      }
      let msg = bulk.abort
        ? `Stopped — saved ${tot.done} photos from ${tot.sets} of ${albums.length} sets`
        : `Saved ${tot.done} photos from ${tot.sets} set${tot.sets === 1 ? '' : 's'} to Downloads/${folder}` +
          (tot.failed ? ` · ${tot.failed} failed` : '') + (tot.empty ? ` · ${tot.empty} set${tot.empty === 1 ? '' : 's'} had no photos you can open` : '');
      if (tot.misplaced) {
        msg = `Saved ${tot.done}, but ${tot.misplaced} landed outside Downloads/${folder}. ` +
          'Another download extension (or download-manager integration) is overriding the save location — disable it and try again.';
      }
      showToast(msg, 100, 'Close');
      finishBulk();
      if (!tot.misplaced) bulk.timer = setTimeout(() => { if (bulk.phase === 'idle') toast.hidden = true; }, 10000);
    } catch (err) {
      bulk.phase = 'idle';
      resetBulkButton();
      flashChip((err && err.message) || 'Couldn’t list the sets here', 4000);
    }
  }

  // ── "Download all sets" button above the model's set list ──
  let sgListBar = null; // { host, btn, label, anchor }
  function ensureSgListBar() {
    const model = isActive() ? sgListModel() : null;
    const first = model && document.querySelector('article.type-album, article.single-album');
    if (!first) { if (sgListBar) { sgListBar.host.remove(); sgListBar = null; } return; }
    const anchor = first.parentElement;
    if (sgListBar && sgListBar.host.isConnected && sgListBar.anchor === anchor) { sgListBarSync(); return; }
    if (sgListBar) sgListBar.host.remove();
    const host = document.createElement('max-image-saver-sets');
    host.setAttribute('style', 'all:initial;display:flex;justify-content:flex-end;margin:0 0 12px;line-height:0;');
    const sr = host.attachShadow({ mode: 'closed' });
    sr.innerHTML = `
      <style>
        button{all:unset;box-sizing:border-box;height:36px;padding:0 16px 0 12px;display:inline-flex;align-items:center;gap:8px;
          background:#e8798f;color:#fff;border-radius:3px;cursor:pointer;white-space:nowrap;
          font:700 12px/1 gotham_boldregular,"Gotham",system-ui,-apple-system,"Segoe UI",sans-serif;letter-spacing:.08em;text-transform:uppercase;
          box-shadow:0 1px 3px rgba(0,0,0,.35);transition:filter .12s}
        button:hover{filter:brightness(1.1)}
        button:focus-visible{outline:2px solid #fff;outline-offset:2px}
        button:disabled{opacity:.45;cursor:default;filter:none}
        button[data-phase=listing],button[data-phase=running]{background:#b91c1c}
        svg{width:16px;height:16px;flex:none}
      </style>
      <button type="button" title="Open every set of this model and download each one, full size, numbered in grid order">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4.5v10m0 0-4.2-4.2M12 14.5l4.2-4.2M5.5 19h13" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>
        <span class="lbl"></span>
      </button>`;
    const btn = sr.querySelector('button');
    const label = sr.querySelector('.lbl');
    for (const ev of ['pointerdown', 'pointerup', 'mousedown', 'mouseup', 'dblclick', 'contextmenu']) host.addEventListener(ev, (e) => e.stopPropagation());
    btn.addEventListener('click', (e) => { e.preventDefault(); e.stopPropagation(); onBulkClick('all'); });
    anchor.parentNode.insertBefore(host, anchor);
    sgListBar = { host, btn, label, anchor };
    sgListBarSync();
  }

  function sgListBarSync() {
    if (!sgListBar) return;
    const { btn, label } = sgListBar;
    const n = sgAlbumLinks(document, sgListModel()).length;
    const more = !!document.querySelector('#load-more, [data-load-more-method]');
    const mine = bulk.mode === 'all';
    const phase = mine ? bulk.phase : 'idle';
    btn.dataset.phase = phase;
    btn.disabled = !mine && bulk.phase !== 'idle';
    label.textContent = phase === 'listing' || phase === 'running' ? 'Stop' : `Download all sets${n ? ` (${n}${more ? '+' : ''})` : ''}`;
  }

  // ───────────────────────── context menu entry ─────────────────────────
  let lastContextEl = null;
  document.addEventListener('contextmenu', (e) => {
    lastContextEl = e.target && e.target.tagName === 'IMG' ? e.target : findImageAt(e.clientX, e.clientY);
  }, true);

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg && msg.type === 'cap-add' && IS_TOP) { capNote(msg.url, msg.w, msg.h, msg.ref); return; }
    if (msg && msg.type === 'context-download') {
      const el = lastContextEl && (lastContextEl.currentSrc === msg.srcUrl || lastContextEl.src === msg.srcUrl || bgUrlOf(lastContextEl))
        ? lastContextEl : null;
      sendResponse({ ok: true });
      downloadFrom(el, msg.srcUrl).catch(() => {});
    }
  });

  // ───────────────────────── candidate discovery ─────────────────────────
  const IMG_EXT = /\.(jpe?g|jfif|png|gif|webp|avif|bmp|tiff?|heic|svg)(?:[?#]|$)/i;
  const DATA_ATTRS = [
    'data-src', 'data-original', 'data-full', 'data-full-src', 'data-fullsrc', 'data-full-url',
    'data-large', 'data-large-file', 'data-orig-file', 'data-orig-src', 'data-original-src',
    'data-hi-res', 'data-hires', 'data-highres', 'data-zoom', 'data-zoom-image', 'data-zoom-src',
    'data-big', 'data-big-src', 'data-lazy-src', 'data-max-src', 'data-src-large', 'data-image',
    'data-raw', 'data-old-hires', 'data-photo-src',
  ];

  function parseSrcset(s, baseWidth) {
    const out = [];
    if (!s) return out;
    let i = 0;
    const n = s.length;
    while (i < n) {
      while (i < n && /[\s,]/.test(s[i])) i++;
      if (i >= n) break;
      let start = i;
      while (i < n && !/\s/.test(s[i])) i++;
      let url = s.slice(start, i);
      let desc = '';
      if (/,$/.test(url)) {
        url = url.replace(/,+$/, '');
      } else {
        while (i < n && /\s/.test(s[i])) i++;
        start = i;
        while (i < n && s[i] !== ',') i++;
        desc = s.slice(start, i).trim();
        i++;
      }
      let score = 1;
      const m = desc.match(/([\d.]+)\s*([wxh])/i);
      if (m) score = m[2].toLowerCase() === 'x' ? parseFloat(m[1]) * (baseWidth || 1000) : parseFloat(m[1]);
      if (url) out.push({ url, score });
    }
    return out;
  }

  function resolve(u) {
    if (!u || typeof u !== 'string') return null;
    u = u.trim();
    if (!u || /[\s{}]/.test(u)) return null;
    if (u.startsWith('data:image/') || u.startsWith('blob:')) return u;
    try {
      const abs = new URL(u, document.baseURI);
      return /^https?:$/.test(abs.protocol) ? abs.href : null;
    } catch { return null; }
  }

  // Lightbox scripts keep the enlarged image's address on the link or box around the thumbnail.
  const ENLARGE_ATTRS = ['data-pswp-src', 'data-mfp-src', 'data-lg-src', 'data-download-url', 'data-full-image', 'data-fullsize',
    'data-full-size', 'data-image-full', 'data-large-image', 'data-highres-src', 'data-original-url', 'data-orig', 'data-img',
    'data-img-src', 'data-href', 'data-url', 'data-featherlight', 'data-remote', 'data-glightbox'];
  function enlargeSources(el) {
    const out = [];
    const looksLikeUrl = (v) => v && (v.includes('/') || IMG_EXT.test(v)) && !/^(#|javascript:|mailto:)/i.test(v);
    let n = el.parentElement;
    for (let d = 0; n && d < 4 && n !== document.body; d++, n = n.parentElement) {
      for (const a of [...DATA_ATTRS, ...ENLARGE_ATTRS]) {
        let v = n.getAttribute(a);
        if (a === 'data-glightbox' && v) v = (v.match(/href:\s*([^;]+)/) || [])[1];
        if (looksLikeUrl(v)) out.push(v.trim());
      }
      // Link around the thumbnail that a lightbox opens (its address may not end in .jpg).
      if (n.tagName === 'A' && n.getAttribute('href') && (IMG_EXT.test(n.href) || looksEnlargeable(el, false))) {
        const h = n.getAttribute('href');
        if (looksLikeUrl(h)) out.push(n.href);
      }
    }
    return out;
  }

  function collectRaw(el, hintUrl) {
    const raw = [];
    if (el && el.tagName === 'IMG') {
      const sets = [];
      const baseW = el.naturalWidth || el.width;
      const pic = el.parentElement && el.parentElement.tagName === 'PICTURE' ? el.parentElement : null;
      if (pic) {
        for (const s of pic.querySelectorAll('source')) {
          sets.push(...parseSrcset(s.getAttribute('srcset'), baseW), ...parseSrcset(s.getAttribute('data-srcset'), baseW));
        }
      }
      sets.push(...parseSrcset(el.getAttribute('srcset'), baseW), ...parseSrcset(el.getAttribute('data-srcset'), baseW));
      sets.sort((a, b) => b.score - a.score).forEach((x) => raw.push(x.url));

      // Amazon: JSON map of {url: [w, h]}
      const dyn = el.getAttribute('data-a-dynamic-image');
      if (dyn) {
        try {
          Object.entries(JSON.parse(dyn))
            .sort((a, b) => (b[1][0] * b[1][1]) - (a[1][0] * a[1][1]))
            .forEach(([u]) => raw.push(u));
        } catch { /* ignore */ }
      }
      for (const a of DATA_ATTRS) raw.push(el.getAttribute(a));
      const link = el.closest('a[href]');
      if (link && IMG_EXT.test(link.href)) raw.push(link.href);
      raw.push(...enlargeSources(el));
      raw.push(el.currentSrc, el.src);
    } else if (el) {
      raw.push(bgUrlOf(el));
      const link = el.closest('a[href]');
      if (link && IMG_EXT.test(link.href)) raw.push(link.href);
      raw.push(...enlargeSources(el));
    }
    if (hintUrl) raw.push(hintUrl);
    return raw.map(resolve).filter(Boolean);
  }

  // Known CDN / site URL patterns → larger variants. Every guess is verified by
  // actually loading it, so a wrong guess only costs a failed probe.
  function upgradeVariants(src) {
    const out = [];
    let u;
    try { u = new URL(src); } catch { return out; }
    if (!/^https?:$/.test(u.protocol)) return out;
    const h = u.hostname;
    const p = u.pathname;
    const add = (x) => { const s = typeof x === 'string' ? x : x && x.href; if (s && s !== src) out.push(s); };
    const mod = (fn) => { const x = new URL(src); fn(x); add(x); };

    // Image proxies (Next.js, Vercel, WordPress Photon, generic ?url=)
    if (/\/_next\/image|\/_vercel\/image|\/cdn-cgi\/image\//.test(p)) {
      const inner = u.searchParams.get('url');
      if (inner) add(resolve(new URL(inner, u.origin).href));
      const cf = p.match(/\/cdn-cgi\/image\/[^/]+\/(.+)$/);
      if (cf) add(resolve(new URL(cf[1].startsWith('http') ? cf[1] : '/' + cf[1], u.origin).href));
    }

    // Twitter / X
    if (h === 'pbs.twimg.com') {
      if (p.startsWith('/media/')) {
        mod((x) => {
          x.pathname = x.pathname.replace(/:\w+$/, '');
          const ext = x.pathname.match(/\.(\w+)$/);
          if (ext && !x.searchParams.get('format')) { x.pathname = x.pathname.replace(/\.\w+$/, ''); x.searchParams.set('format', ext[1]); }
          x.searchParams.set('name', 'orig');
        });
      }
      if (/_(normal|bigger|mini|x96|\d+x\d+)\.\w+$/.test(p)) mod((x) => { x.pathname = x.pathname.replace(/_(normal|bigger|mini|x96|\d+x\d+)(\.\w+)$/, '$2'); });
    }

    // Wikimedia / MediaWiki thumbnails
    if (/\/thumb\//.test(p) && /(wikimedia|wikipedia|fandom|wiki)/.test(h + p)) {
      mod((x) => { x.pathname = x.pathname.replace(/\/thumb(\/.+?\/[^/]+)\/[^/]+$/, '$1'); });
    }
    if (h.endsWith('nocookie.net')) mod((x) => { x.pathname = x.pathname.replace(/\/revision\/latest\/.*$/, '/revision/latest'); x.search = ''; });

    // Reddit
    if (h === 'preview.redd.it') mod((x) => { x.hostname = 'i.redd.it'; x.search = ''; });

    // Pinterest
    if (h === 'i.pinimg.com' && /^\/\d+x\d*\//.test(p)) {
      mod((x) => { x.pathname = x.pathname.replace(/^\/\d+x\d*\//, '/originals/'); });
      mod((x) => { x.pathname = x.pathname.replace(/^\/\d+x\d*\//, '/736x/'); });
    }

    // Imgur thumbnails (7-char id + size letter)
    if (/(^|\.)imgur\.com$/.test(h)) mod((x) => { x.pathname = x.pathname.replace(/^\/([a-zA-Z0-9]{7})[sbtmlhg](\.\w+)$/, '/$1$2'); });

    // Google (Photos, Blogger, YouTube avatars, Play)
    if (/googleusercontent\.com$|ggpht\.com$|blogspot\.com$|bp\.blogspot/.test(h)) {
      mod((x) => { x.pathname = x.pathname.replace(/=[swh]\d+[^/]*$/, '=s0').replace(/\/[swh]\d+(-[a-z0-9-]+)?\//, '/s0/'); });
      if (!/=/.test(p.split('/').pop())) mod((x) => { x.pathname += '=s0'; });
    }

    // YouTube thumbnails
    if (h === 'i.ytimg.com') {
      mod((x) => { x.pathname = x.pathname.replace(/\/(hq720|hqdefault|mqdefault|sddefault|default|hq\d)(_\w+)?\.(jpg|webp)$/, '/maxresdefault.$3'); x.search = ''; });
    }

    // Amazon
    if (/media-amazon\.com$|ssl-images-amazon\.com$/.test(h)) mod((x) => { x.pathname = x.pathname.replace(/\._[^/]+_(?=\.\w+$)/, ''); });

    // eBay
    if (h === 'i.ebayimg.com') mod((x) => { x.pathname = x.pathname.replace(/s-l\d+(\.\w+)$/, 's-l1600$1'); });

    // Etsy
    if (h.endsWith('etsystatic.com')) mod((x) => { x.pathname = x.pathname.replace(/il_\d+x\d+/, 'il_fullxfull'); });

    // Tumblr
    if (h.endsWith('media.tumblr.com')) {
      mod((x) => { x.pathname = x.pathname.replace(/\/s\d+x\d+(\/|$)/, '/s2048x3072$1').replace(/_(\d{2,4})(\.\w+)$/, '_1280$2'); });
    }

    // ArtStation
    if (h.endsWith('artstation.com')) {
      mod((x) => { x.pathname = x.pathname.replace(/\/(smaller_square|small_square|small|medium|large|micro_square)\//, '/4k/'); });
      mod((x) => { x.pathname = x.pathname.replace(/\/(smaller_square|small_square|small|medium|micro_square)\//, '/large/'); });
    }

    // Medium
    if (h === 'miro.medium.com') mod((x) => { x.pathname = x.pathname.replace(/\/(resize|format|fit|quality):[^/]+/g, ''); });

    // Discord
    if (h === 'media.discordapp.net') mod((x) => { x.hostname = 'cdn.discordapp.com'; ['width', 'height', 'format', 'quality'].forEach((k) => x.searchParams.delete(k)); });

    // Squarespace
    if (/squarespace(-cdn)?\.com$/.test(h)) mod((x) => { x.searchParams.set('format', 'original'); });

    // Cloudinary: drop transformation segments
    if (/res\.cloudinary\.com$/.test(h) || /\/image\/upload\//.test(p)) {
      mod((x) => { x.pathname = x.pathname.replace(/\/upload\/((?:[a-z]{1,3}_[^/]+\/)+)/, '/upload/'); });
    }

    // Flickr: sizes that share the thumbnail secret (b=1024, c=800, z=640).
    // h/k/3k–6k/o use different secrets — those come from the photo page lookup.
    if (/staticflickr\.com$/.test(h)) {
      const m = p.match(/^(.*\/\d+_[0-9a-f]+)(?:_([a-z0-9]{1,2}))?\.(jpg|png|gif)$/);
      if (m && !/^(o|k|h|3k|4k|5k|6k)$/.test(m[2] || '')) {
        for (const s of ['b', 'c', 'z']) mod((x) => { x.pathname = `${m[1]}_${s}.jpg`; });
      }
    }

    // FetLife: picture size suffix (e.g. _60, _200, _345, _720) → larger sizes.
    if (/(^|\.)fetlife\.com$/.test(h)) {
      const m = p.match(/^(.*?)_(\d{2,4})(\.(?:jpe?g|png|gif|webp))$/i);
      if (m && Number(m[2]) < 2048) {
        for (const s of ['2048', '1280', '958', '720']) {
          if (Number(s) > Number(m[2])) mod((x) => { x.pathname = `${m[1]}_${s}${m[3]}`; });
        }
      }
    }

    // YouPic: /i/<image key><6-hex size>. Small sizes are WebP previews; a huge
    // size request returns the full-resolution original JPEG.
    if (/(^|\.)youpic\.com$/.test(h)) {
      const m = p.match(/^\/i\/([0-9a-f]{16,})([0-9a-f]{6})$/i);
      if (m && m[1].length % 2 === 0 && m[2].toLowerCase() !== '00ffff') mod((x) => { x.pathname = `/i/${m[1]}00ffff`; x.search = ''; });
    }

    // Model Society: the image server only hands out fixed sizes; 1200 px wide is the largest.
    if (h === 'images.modelsociety.com' && /\/edge\/rs:fit:\d+:\d+\//.test(p) && !p.includes(`/${MS_MAX}/`)) {
      mod((x) => { x.pathname = x.pathname.replace(/\/edge\/rs:fit:\d+:\d+\//, `/edge/${MS_MAX}/`); });
    }

    // ModelMayhem: "<name>_m.jpg" is the gallery thumbnail; "<name>.jpg" is the full image.
    if (/modelmayhem/i.test(h) || IS_MODELMAYHEM) {
      if (/_m\.(jpe?g|png|gif|webp)$/i.test(p)) mod((x) => { x.pathname = x.pathname.replace(/_m(\.(?:jpe?g|png|gif|webp))$/i, '$1'); });
    }

    // SmugMug: the original lives in /O/ without the "-SIZE" suffix (checked like any other candidate).
    { const smp = smugmugParts(src); if (smp && smp.size !== 'O') add(smugmugUrl(smp, 'O')); }

    // Shopify size suffixes
    if (h === 'cdn.shopify.com' || /\/cdn\/shop\//.test(p)) {
      mod((x) => {
        x.pathname = x.pathname.replace(/_(\d+x\d*|\d*x\d+|pico|icon|thumb|small|compact|medium|large|grande|master)(_crop_\w+)?(@\dx)?(?=\.\w+$)/i, '');
        ['width', 'height', 'crop'].forEach((k) => x.searchParams.delete(k));
      });
    }

    // Generic sites: "thumb_123.jpg" → also try "123.jpg" (kept only if it really loads and is larger)
    if (!IS_FLICKR && !IS_YOUPIC && !IS_PATREON && !IS_MODELMAYHEM && /\/thumb_[^/]+$/i.test(p)) {
      mod((x) => { x.pathname = x.pathname.replace(/\/thumb_([^/]+)$/i, '/$1'); });
    }

    // Generic: WordPress-style "-300x200" and "_300x200" filename suffixes
    if (/[-_]\d{2,4}x\d{2,4}(?=\.(jpe?g|png|gif|webp|avif)$)/i.test(p)) {
      mod((x) => { x.pathname = x.pathname.replace(/[-_]\d{2,4}x\d{2,4}(?=\.\w+$)/, ''); });
    }
    if (/-scaled(?=\.\w+$)/.test(p)) mod((x) => { x.pathname = x.pathname.replace(/-scaled(?=\.\w+$)/, ''); });

    // Generic: strip resizing query params
    const SIZE_PARAMS = ['w', 'width', 'h', 'height', 'resize', 'size', 'fit', 'crop', 'dpr', 'q', 'quality',
      'auto', 'sz', 'maxwidth', 'maxheight', 'mw', 'mh', 'wid', 'hei', 'scale', 'imwidth', 'downsize', 'strip', 'zoom'];
    if (SIZE_PARAMS.some((k) => u.searchParams.has(k))) mod((x) => SIZE_PARAMS.forEach((k) => x.searchParams.delete(k)));

    return out;
  }

  function expand(urls) {
    const out = [];
    for (const u of urls) {
      for (const v of upgradeVariants(u)) {
        for (const v2 of upgradeVariants(v)) out.push(v2);
        out.push(v);
      }
      out.push(u);
    }
    return [...new Set(out)];
  }

  // ── Site lookups ──
  // FetLife: thumbnails link to a picture page on the same site; fetch it with the
  // user's session and harvest every URL for the same picture.
  let fetlifePageCache = null;
  async function fetlifeLookup(el, urls) {
    if (!/(^|\.)fetlife\.com$/.test(location.hostname)) return [];
    const stems = new Set();
    for (const u of urls) {
      const m = u.match(/\/([^/?#]+?)(?:_\d{2,4})?\.(?:jpe?g|png|gif|webp)(?:[?#]|$)/i);
      if (m && m[1].length >= 6) stems.add(m[1]);
    }
    const found = [];
    const harvest = (text) => {
      const norm = text.replace(/\\u002[fF]/g, '/').replace(/\\\//g, '/').replace(/&amp;/g, '&');
      const re = /https?:\/\/[^\s"'<>()\\]+?\.(?:jpe?g|png|gif|webp)(?:\?[^\s"'<>()\\]*)?/gi;
      for (const m of norm.match(re) || []) {
        if (/fetlife/i.test(m) && [...stems].some((s) => m.includes(s))) found.push(m);
      }
    };
    // URLs already present in this page (lightbox data, JSON blobs)
    // Cache the page HTML briefly — "download all" calls this once per image.
    if (!fetlifePageCache || Date.now() - fetlifePageCache.t > 5000) {
      fetlifePageCache = { t: Date.now(), html: document.documentElement.innerHTML.slice(0, 3_000_000) };
    }
    harvest(fetlifePageCache.html);
    // Linked picture page
    const link = el && el.closest && el.closest('a[href*="/pictures/"]');
    if (link && link.origin === location.origin && link.href !== location.href) {
      try {
        const res = await fetch(link.href, { credentials: 'include' });
        if (res.ok) harvest(await res.text());
      } catch { /* ignore */ }
    }
    return [...new Set(found)];
  }

  async function siteLookups(el, urls) {
    const results = await Promise.all([
      fetlifeLookup(el, urls).catch(() => []),
      // Flickr page lookups run in the background worker (cross-origin fetch).
      urls.some((u) => /staticflickr\.com\//.test(u))
        ? sendMessage({ type: 'flickr-sizes', urls }).then((r) => (r && r.urls) || []).catch(() => [])
        : Promise.resolve([]),
    ]);
    return results.flat();
  }

  // ───────────────────────── probing & download ─────────────────────────
  function probeImage(url, timeout = 15000) {
    return new Promise((resolveP) => {
      const img = new Image();
      let done = false;
      const finish = (v) => {
        if (done) return;
        done = true;
        clearTimeout(timer);
        img.onload = img.onerror = null;
        if (!v) img.src = '';
        resolveP(v);
      };
      const timer = setTimeout(() => finish(null), timeout);
      img.onload = () => finish(img.naturalWidth ? { url, w: img.naturalWidth, h: img.naturalHeight } : null);
      img.onerror = () => finish(null);
      img.src = url;
    });
  }

  function sendMessage(msg) {
    if (msg && !msg.referer) msg = { ...msg, referer: location.href };
    return new Promise((res, rej) => {
      try {
        chrome.runtime.sendMessage(msg, (r) => {
          const err = chrome.runtime.lastError;
          if (err) rej(new Error(err.message)); else res(r);
        });
      } catch (e) { rej(e); }
    });
  }

  // ───────────────────────── SmugMug ─────────────────────────
  // …/i-<id>/0/<key>/<SIZE>/<name>-<SIZE>.jpg  →  …/O/<name>.jpg (the original upload).
  const SM_SIZE = '(?:Ti|Th|XS|S|M|L|XL|X2|X3|X4|X5|4K|5K|O|D)';
  const SM_PATH = new RegExp(`^(.*\\/i-[A-Za-z0-9]+\\/(?:[^/]+\\/)*)(${SM_SIZE})\\/([^/]+)$`);
  function smugmugParts(url) {
    let u;
    try { u = new URL(url); } catch { return null; }
    if (!/^https?:$/.test(u.protocol)) return null;
    const m = u.pathname.match(SM_PATH);
    if (!m) return null;
    const size = m[2];
    const f = m[3].match(/^(.*?)(?:-([A-Za-z0-9]{1,3}))?(\.[A-Za-z0-9]+)$/);
    if (!f) return null;
    // Drop "-X3" only when it repeats the size folder (so "Smith-L.jpg" in /O/ stays intact).
    const base = f[2] && f[2] === size ? f[1] : (f[2] ? `${f[1]}-${f[2]}` : f[1]);
    return { origin: u.origin, prefix: m[1], size, base, ext: f[3] };
  }
  const smugmugUrl = (p, size) => `${p.origin}${p.prefix}${size}/${p.base}${size === 'O' ? '' : `-${size}`}${p.ext}`;

  // Downloads\…\Smugmug\<site>\<gallery>\<file>
  function smugmugSite() {
    const host = location.hostname.replace(/^www\./i, '');
    if (/\.smugmug\.com$/i.test(host)) return host.split('.')[0]; // <name>.smugmug.com
    const parts = host.split('.');
    const sld = parts.length > 2 && /^(co|com|net|org|gov|ac|edu)$/.test(parts[parts.length - 2]);
    return parts[parts.length - (sld ? 3 : 2)] || parts[0] || host; // www.<site>.com
  }
  function smugmugPath(url) {
    const p = smugmugParts(url);
    if (!p) return null;
    const segs = p.prefix.split('/').filter(Boolean);
    const at = segs.findIndex((x) => /^i-[A-Za-z0-9]+$/.test(x));
    let gallery = at > 0 ? segs[at - 1] : '';
    try { gallery = decodeURIComponent(gallery); } catch { /* keep raw */ }
    gallery = /^photos$/i.test(gallery) ? '' : gallery.replace(/-+/g, ' ').trim();
    if (!gallery) gallery = (document.title || '').split(/\s[|\-–—]\s/)[0].trim();
    let name = p.base;
    try { name = decodeURIComponent(name); } catch { /* keep raw */ }
    return ['Smugmug', safeName(smugmugSite(), 80), safeName(gallery, 100), safeName(name, 120) || 'image'].filter(Boolean).join('/');
  }

  // Try the original first, then the biggest display sizes — first one that loads wins.
  async function smugmugBest(direct) {
    const p = direct.map(smugmugParts).find(Boolean);
    if (!p) return null;
    for (const size of ['O', '5K', '4K', 'X5', 'X4', 'X3']) {
      const url = smugmugUrl(p, size);
      let r = await probeImage(url);
      if (!r) {
        try { const w = await sendMessage({ type: 'probe', urls: [url] }); r = (w && w.results && w.results[0]) || null; } catch { r = null; }
      }
      if (r) return r;
    }
    return null;
  }

  async function pickLargest(el, hintUrl) {
    const direct = collectRaw(el, hintUrl);
    if (!direct.length) throw new Error('No image found');
    const sm = await smugmugBest(direct);
    if (sm) return sm;
    const extra = await siteLookups(el, direct);
    const cands = [...new Set([...extra, ...expand(direct)])].slice(0, 24);

    const known = [];
    if (el && el.tagName === 'IMG' && el.complete && el.naturalWidth) {
      known.push({ url: el.currentSrc || el.src, w: el.naturalWidth, h: el.naturalHeight });
    }
    const toProbe = cands.filter((u) => !known.some((k) => k.url === u));
    let results = await Promise.all(toProbe.map((u) => probeImage(u)));

    // Page CSP or hotlink rules can block probes — retry failures from the worker.
    const failed = toProbe.filter((_, i) => !results[i] && !toProbe[i].startsWith('blob:'));
    if (failed.length) {
      try {
        const r = await sendMessage({ type: 'probe', urls: failed });
        const map = new Map((r && r.results || []).filter(Boolean).map((x) => [x.url, x]));
        results = results.map((v, i) => v || map.get(toProbe[i]) || null);
      } catch { /* ignore */ }
    }

    const all = [...results.filter(Boolean), ...known];
    if (!all.length) {
      const fallback = (el && (el.currentSrc || el.src)) || direct[direct.length - 1];
      return { url: fallback, w: 0, h: 0 };
    }
    const order = (u) => { const i = cands.indexOf(u); return i < 0 ? 1e6 : i; };
    all.sort((a, b) => (b.w * b.h) - (a.w * a.h) || order(a.url) - order(b.url));
    return all[0];
  }

  const MIME_EXT = { jpeg: 'jpg', jpg: 'jpg', png: 'png', gif: 'gif', webp: 'webp', avif: 'avif', 'svg+xml': 'svg', bmp: 'bmp', 'x-icon': 'ico', tiff: 'tif' };
  const stamp = () => new Date().toISOString().replace(/[-:T]/g, '').slice(0, 14);

  function makeFilename(url) {
    if (url.startsWith('data:')) {
      const m = url.match(/^data:image\/([\w+.-]+)/i);
      return `image-${stamp()}.${(m && MIME_EXT[m[1].toLowerCase()]) || 'png'}`;
    }
    let u;
    try { u = new URL(url); } catch { return ''; }
    let name = u.pathname.split('/').filter(Boolean).pop() || '';
    try { name = decodeURIComponent(name); } catch { /* keep raw */ }
    name = name.replace(/:(orig|large|medium|small|thumb)$/i, '').replace(/=s0$/, '');
    let ext = (name.match(/\.([a-z0-9]{2,5})$/i) || [])[1];
    const fmt = u.searchParams.get('format') || u.searchParams.get('fm');
    if (!ext && fmt && /^(jpe?g|png|gif|webp|avif)$/i.test(fmt)) { ext = fmt.toLowerCase(); name += '.' + ext; }
    if (!ext || !/^(jpe?g|jfif|png|gif|webp|avif|bmp|tiff?|svg|heic|ico)$/i.test(ext)) return '';
    name = name.replace(/[\\/:*?"<>|~\x00-\x1f]/g, '_').replace(/^[.\s]+|[.\s]+$/g, '');
    if (name.length > 150) name = name.slice(0, 140) + '.' + ext;
    if (!name || name.startsWith('.')) name = 'image' + name;
    return name;
  }

  async function blobUrlToDataUrl(url) {
    const blob = await (await fetch(url)).blob();
    return new Promise((res, rej) => {
      const fr = new FileReader();
      fr.onload = () => res(fr.result);
      fr.onerror = () => rej(fr.error);
      fr.readAsDataURL(blob);
    });
  }

  async function downloadFrom(el, hintUrl) {
    const best = await pickLargest(el, hintUrl);
    let url = best.url;
    if (url.startsWith('blob:')) url = await blobUrlToDataUrl(url);
    // On Flickr and YouPic, look up the photo's "date taken" so it can be written into the file.
    let dateTaken = '';
    // Default: <original file name>; the worker adds YYYY-MM-DD_HHMM_ when the file carries a date taken.
    const ne = nameAndExt(url);
    const folder = thumbFolder(el) || genericFolder(); // "thumb_" thumbnails: <Site>/<segment before the file>
    let filename = `${folder}/${ne.ext ? `${ne.name}.${ne.ext}` : ne.name}`;
    let autoExt = !ne.ext;
    let datePrefix = true;
    let tags = [];
    if (IS_MODELMAYHEM) { try { tags = await mmTagsFor(mmPidForEl(el)); } catch { tags = []; } }
    const flickrId = IS_FLICKR && flickrIdFromUrl(url);
    const ypKey = IS_YOUPIC && ypKeyFromUrl(url);
    if (flickrId) {
      const info = await flickrPhotoInfo(flickrId);
      dateTaken = info.dateTaken;
      const forcedAlbum = await currentFlickrAlbumTitle(); // '' unless viewing inside an album
      filename = flickrPath({ ...info, id: flickrId, url, forcedAlbum });
      autoExt = false;
      datePrefix = flickrDatePrefix(info);
    }
    if (ypKey) {
      const info = await youpicInfo(url, el);
      dateTaken = info.dateTaken;
      filename = youpicPath(info); // extension added once the type is known
      autoExt = true;
    }
    const smPath = !flickrId && !ypKey && smugmugPath(url);
    if (smPath) { filename = smPath; autoExt = true; }
    const sgA = IS_SG && sgAlbum();
    if (sgA && el && sgA.grid.contains(el)) {
      const links = sgPhotoLinks(sgA.grid);
      const a = el.closest('a[href]');
      const idx = links.indexOf(a) + 1 || links.findIndex((x) => x.closest('li') === el.closest('li')) + 1;
      if (idx > 0) {
        url = links[idx - 1].href; // the full-size photo the thumbnail opens
        filename = sgPath(sgA, idx, links.length);
        autoExt = true;
        datePrefix = false; // keep the grid number at the front
      }
    }
    // Model Society: open the photo's own page for its posted date and tagged model.
    let msLink = null;
    const msId = IS_MS && (msIdFrom(el && el.closest && (el.closest('a[href*="/image/"]') || {}).href) || msIdFrom(url) ||
      msIdFrom(location.pathname));
    if (msId) {
      try {
        const info = await msPhotoInfo(msId);
        const cm = String(info.creatorUrl).match(/\/(photographer|model)\/([^/?#]+)/i);
        const owner = msOwner() || (cm ? { type: cm[1].toLowerCase(), slug: cm[2], name: info.creator, gallery: `${location.origin}/${cm[1].toLowerCase()}/${cm[2]}/photos` } : null);
        if (owner && info.url) {
          const f = msPaths([info], owner);
          url = info.url;
          filename = info.path;
          autoExt = true;
          datePrefix = false;
          tags = msTags(info);
          msLink = [f, owner];
        }
      } catch { /* fall back to the generic name */ }
    }
    const res = await sendMessage({
      type: 'download',
      url,
      filename,
      autoExt,
      datePrefix,
      saveAs: !!settings.saveAs,
      dateTaken,
      tags,
    });
    if (!res || !res.ok) throw new Error((res && res.error) || 'Download failed');
    if (IS_MODELMAYHEM) saveModelMayhemLink(folder);
    if (msLink) saveMsLink(...msLink);
    best.dated = !!res.dated;
    best.tags = res.tagged ? tags : [];
    best.datedSite = !!(flickrId || ypKey);
    return best;
  }

  // ── FaceSort: the gallery watcher drives the same listing as the ALL / GALLERY buttons ──
  window.__fsMIS = {
    run: (mode) => { onBulkClick(mode === 'gallery' ? 'gallery' : 'all'); return true; },
    state: () => bulk.phase,
    stop: () => { bulk.abort = true; if (bulk.phase === 'running') cancelBulk(); return true; },
    message: () => (dockChip && dockChip.textContent) || (chip && chip.textContent) || '',
    flickr: () => IS_FLICKR,
  };
})();
