// Injected DOM-walk. One pass over the live DOM producing a compact, flattened
// tree that carries BOTH "what can I do" (interactive elements, each tagged with
// data-bta-index so Python can build a Playwright locator) and "what is true"
// (roles, accessible names, states, and all visible text in reading order).
//
// Runs as an expression via page.evaluate(). Defensive throughout: one bad
// element must not kill the walk.
(() => {
  const MAX_DEPTH = 120;
  const NAME_CAP = 500;   // labels, option text, error messages — rarely longer
  const TEXT_CAP = 2000;  // visible text nodes — keep full messages/paragraphs

  const SKIP_TAGS = new Set([
    'script', 'style', 'head', 'meta', 'link', 'title', 'noscript', 'template',
    'br', 'wbr',
  ]);

  const INTERACTIVE_ROLES = new Set([
    'button', 'link', 'checkbox', 'radio', 'tab', 'menuitem', 'menuitemcheckbox',
    'menuitemradio', 'option', 'switch', 'combobox', 'textbox', 'searchbox',
    'slider', 'spinbutton',
  ]);

  const LANDMARK_ROLES = new Set([
    'banner', 'navigation', 'main', 'contentinfo', 'complementary', 'form',
    'search', 'region',
  ]);

  // Big structural containers routinely inherit or set cursor:pointer without
  // being the actual click target — exclude them from the pointer heuristic so
  // one stray cursor:pointer on <body> doesn't index the whole page.
  const CURSOR_SKIP = new Set([
    'html', 'body', 'main', 'nav', 'header', 'footer', 'aside', 'section',
    'form', 'ul', 'ol', 'table', 'thead', 'tbody', 'tfoot', 'tr',
  ]);

  // Native form controls are driven by Playwright's element-targeted setters
  // (fill / check / select_option / set_input_files), not a blind coordinate
  // click, and include deliberately-overlaid widgets (a MUI checkbox's
  // opacity:0 <input> sitting under a styled SVG). Skip occlusion for them.
  const NATIVE_FORM_TAGS = new Set(['input', 'select', 'textarea']);

  const cap = (s, n) => (s.length > n ? s.slice(0, n) + '…' : s);
  const collapse = (s) => (s || '').replace(/\s+/g, ' ').trim();

  // Classify how an element affects itself and its subtree:
  //   2 = visible.
  //   1 = self-hidden, but descendants may still show (a zero-size / opacity:0 /
  //       visibility:hidden wrapper) — keep walking so any visible child survives.
  //   0 = subtree-hidden: display:none, or a region collapsed/clipped to zero size.
  //       Drop the whole subtree. Text nodes bypass per-element visibility, so
  //       without this a display:none panel or a CSS-collapsed accordion leaks its
  //       text and controls into the snapshot even though the user can't see them.
  function visClass(el, tag) {
    try {
      const st = getComputedStyle(el);
      if (st.display === 'none') {
        // File inputs are routinely display:none behind a styled dropzone, yet
        // set_input_files drives them regardless — keep so the upload tool can
        // target them. (type=hidden is dropped later by role/interactivity.)
        return (tag === 'input' && (el.getAttribute('type') || '').toLowerCase() === 'file') ? 2 : 0;
      }
      // A region clipped to zero inner size with real content inside is collapsed
      // (an accordion/panel using max-height:0; overflow:hidden, or MUI Collapse).
      // Its descendants keep real layout rects, so drop the subtree here.
      const clip = (s) => s === 'hidden' || s === 'clip' || s === 'scroll' || s === 'auto';
      if ((el.clientHeight === 0 && el.scrollHeight > 0 && clip(st.overflowY)) ||
          (el.clientWidth === 0 && el.scrollWidth > 0 && clip(st.overflowX))) return 0;
      // visibility:hidden is inherited but a descendant can re-set visibility:visible,
      // so treat it as self-hidden (keep walking), not subtree-hidden.
      if (st.visibility === 'hidden' || st.visibility === 'collapse') return 1;
      // A form control that's only opacity:0 or zero-area is a visually-hidden but
      // clickable widget — e.g. a MUI checkbox/radio/switch whose real <input> is an
      // opacity:0 overlay on a styled SVG, or a 0-sized custom control. Keep it so it
      // gets an index (Playwright ignores opacity when deciding clickability).
      if (tag === 'input' || tag === 'select' || tag === 'textarea') return 2;
      if (parseFloat(st.opacity) === 0) return 1;
      const r = el.getBoundingClientRect();
      if (r.width === 0 && r.height === 0) return 1;
      return 2;
    } catch (e) { return 1; }
  }

  const isVisible = (el, tag) => visClass(el, tag) === 2;

  function idText(ids) {
    return ids.split(/\s+/).map((id) => {
      const t = document.getElementById(id);
      return t ? collapse(t.textContent) : '';
    }).filter(Boolean).join(' ');
  }

  function computeRole(el, tag) {
    const explicit = el.getAttribute('role');
    if (explicit) return explicit.trim().toLowerCase().split(/\s+/)[0];
    switch (tag) {
      case 'a': return el.hasAttribute('href') ? 'link' : null;
      case 'button': return 'button';
      case 'nav': return 'navigation';
      case 'main': return 'main';
      case 'header': return 'banner';
      case 'footer': return 'contentinfo';
      case 'aside': return 'complementary';
      case 'article': return 'article';
      case 'section': return el.getAttribute('aria-label') || el.getAttribute('aria-labelledby') ? 'region' : null;
      case 'h1': case 'h2': case 'h3': case 'h4': case 'h5': case 'h6': return 'heading';
      case 'img': return 'img';
      case 'table': return 'table';
      case 'tr': return 'row';
      case 'td': return 'cell';
      case 'th': return 'columnheader';
      case 'ul': case 'ol': return 'list';
      case 'li': return 'listitem';
      case 'select': return el.multiple ? 'listbox' : 'combobox';
      case 'textarea': return 'textbox';
      case 'form': return 'form';
      case 'dialog': return 'dialog';
      case 'input': {
        const t = (el.getAttribute('type') || 'text').toLowerCase();
        if (t === 'checkbox') return 'checkbox';
        if (t === 'radio') return 'radio';
        if (t === 'file') return 'file-upload';
        if (t === 'submit' || t === 'button' || t === 'reset' || t === 'image') return 'button';
        if (t === 'range') return 'slider';
        if (t === 'number') return 'spinbutton';
        if (t === 'search') return 'searchbox';
        if (t === 'hidden') return null;
        return 'textbox';
      }
      default: return null;
    }
  }

  function landmarkOf(el, role) {
    if (role && LANDMARK_ROLES.has(role)) {
      if (role === 'region' || role === 'form') {
        // Only a landmark if it has an accessible name.
        return (el.getAttribute('aria-label') || el.getAttribute('aria-labelledby')) ? role : null;
      }
      return role;
    }
    if (role === 'dialog' || role === 'alertdialog') return 'dialog';
    if (role === 'alert') return 'alert';
    return null;
  }

  function isInteractive(el, tag, role) {
    if (tag === 'a') return el.hasAttribute('href');
    if (tag === 'button' || tag === 'select' || tag === 'textarea') return true;
    if (tag === 'input') return (el.getAttribute('type') || 'text').toLowerCase() !== 'hidden';
    if (role && INTERACTIVE_ROLES.has(role)) return true;
    if (el.hasAttribute('onclick')) return true;
    try { if (el.isContentEditable) return true; } catch (e) {}
    const ti = el.getAttribute('tabindex');
    if (ti !== null && parseInt(ti, 10) >= 0) return true;
    // Cheapest checks first; interactiveCursor calls getComputedStyle, so it runs
    // last and only for elements nothing else already flagged.
    if (!CURSOR_SKIP.has(tag) && interactiveCursor(el)) return true;
    return false;
  }

  function isDisabled(el) {
    try {
      if (el.disabled === true) return true;
      if (el.getAttribute('aria-disabled') === 'true') return true;
      if (getComputedStyle(el).pointerEvents === 'none') return true;
    } catch (e) {}
    return false;
  }

  // React attaches handlers via its synthetic-event system (delegated to the
  // root), so a clickable/editable <div>/<span> has no inline onclick, tabindex,
  // or role. The reliable signal is the cursor: `pointer` for a clickable div,
  // `text` for a custom editable/comment field that mounts its editor on click.
  // Because cursor inherits, a child reads the same value — so only treat the
  // node where it originates (parent has a different cursor) as the actionable one.
  function interactiveCursor(el) {
    try {
      const c = getComputedStyle(el).cursor;
      if (c !== 'pointer' && c !== 'text') return false;
      const p = el.parentElement;
      if (p && getComputedStyle(p).cursor === c) return false;
      return true;
    } catch (e) { return false; }
  }

  // A control covered by an overlay/modal isn't actionable: a real click at its
  // centre would land on whatever is painted on top. elementFromPoint mirrors
  // exactly what a click hits (it already respects pointer-events, so a
  // transparent pointer-events:none overlay passes through and doesn't count).
  function isOccluded(el) {
    try {
      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) return false;
      const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
      // elementFromPoint only resolves points inside the viewport; an off-screen
      // element returns null, and "scrolled away" is not "occluded" — keep it.
      if (cx < 0 || cy < 0 || cx > window.innerWidth || cy > window.innerHeight) return false;
      const top = document.elementFromPoint(cx, cy);
      if (!top) return false;
      // Not occluded if the hit is this node, a descendant (an inner <span>), or
      // an ancestor that forwards the click (a wrapping <label>).
      if (top === el || el.contains(top) || top.contains(el)) return false;
      // Shadow DOM: elementFromPoint can't see inside a shadow tree, so for a
      // shadow-resident element it returns the host, and .contains() doesn't
      // cross shadow boundaries. Climb el's shadow-host chain — if the hit is
      // (or wraps) a host that owns el, the click still lands on el's own widget.
      let root = el.getRootNode();
      while (root instanceof ShadowRoot) {
        const host = root.host;
        if (top === host || host.contains(top) || top.contains(host)) return false;
        root = host.getRootNode();
      }
      // The hit sits in a different subtree — a genuine overlay on top.
      return true;
    } catch (e) { return false; }
  }

  function accName(el, tag, role) {
    try {
      const lb = el.getAttribute('aria-labelledby');
      if (lb) { const t = idText(lb); if (t) return cap(t, NAME_CAP); }
      const al = el.getAttribute('aria-label');
      if (al && al.trim()) return cap(al.trim(), NAME_CAP);

      if (tag === 'input' || tag === 'textarea' || tag === 'select') {
        if (el.id) {
          const lab = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
          if (lab && collapse(lab.textContent)) return cap(collapse(lab.textContent), NAME_CAP);
        }
        const wrap = el.closest('label');
        if (wrap) { const t = collapse(wrap.textContent); if (t) return cap(t, NAME_CAP); }
        const ph = el.getAttribute('placeholder');
        if (ph && ph.trim()) return cap(ph.trim(), NAME_CAP);
        if (tag === 'input') {
          const ty = (el.getAttribute('type') || '').toLowerCase();
          const v = el.getAttribute('value');
          if ((ty === 'submit' || ty === 'button' || ty === 'reset') && v) return cap(v, NAME_CAP);
        }
        const ti = el.getAttribute('title');
        if (ti && ti.trim()) return cap(ti.trim(), NAME_CAP);
        return '';
      }
      if (tag === 'img') { const alt = el.getAttribute('alt'); if (alt != null) return cap(alt.trim(), NAME_CAP); }
      // role check (not tag[0] === 'h'): <header>/<hgroup> would otherwise get
      // their entire subtree text as a name, duplicating everything under them.
      if (tag === 'a' || tag === 'button' || role === 'button' || role === 'link' || (role || '').includes('menuitem') || role === 'tab' || role === 'option' || role === 'heading') {
        const t = collapse(el.textContent);
        if (t) return cap(t, NAME_CAP);
      }
      const ti = el.getAttribute('title');
      if (ti && ti.trim()) return cap(ti.trim(), NAME_CAP);
    } catch (e) {}
    return '';
  }

  function fieldValue(el, tag) {
    try {
      const ty = tag === 'input' ? (el.getAttribute('type') || 'text').toLowerCase() : '';
      if (ty === 'password') return el.value ? '••• (redacted)' : '';
      if (tag === 'select') {
        const opts = Array.from(el.options || []);
        const sel = opts.filter((o) => o.selected).map((o) => collapse(o.textContent)).filter(Boolean);
        const label = sel.length ? sel.join(', ') : '';
        return label ? `${cap(label, NAME_CAP)} (${opts.length} options)` : `(${opts.length} options)`;
      }
      if (tag === 'input' || tag === 'textarea') {
        if (ty === 'checkbox' || ty === 'radio') return '';
        return el.value ? cap(collapse(el.value), NAME_CAP) : '';
      }
    } catch (e) {}
    return '';
  }

  function statesOf(el, tag, role) {
    const s = [];
    try {
      if (isDisabled(el)) s.push('disabled');
      const ty = tag === 'input' ? (el.getAttribute('type') || '').toLowerCase() : '';
      if (ty === 'checkbox' || ty === 'radio' || role === 'checkbox' || role === 'radio' || role === 'switch') {
        const checked = el.checked === true || el.getAttribute('aria-checked') === 'true';
        s.push(checked ? 'checked' : 'unchecked');
      }
      const exp = el.getAttribute('aria-expanded');
      if (exp === 'true') s.push('expanded');
      else if (exp === 'false') s.push('collapsed');
      if (el.getAttribute('aria-selected') === 'true' || (tag === 'option' && el.selected)) s.push('selected');
      if (el.required === true || el.getAttribute('aria-required') === 'true') s.push('required');
      if (el.readOnly === true) s.push('readonly');
      if (el.getAttribute('aria-invalid') === 'true') s.push('invalid');
      if (document.activeElement === el) s.push('focused');  // where press/typing lands
    } catch (e) {}
    return s;
  }

  // Help / error / format text tied to a field — the "why" behind an invalid
  // state, and format hints (MM/DD/YYYY) a label alone doesn't convey. Pulled
  // from aria-describedby / aria-errormessage, a format placeholder that isn't
  // already the name, and a title tooltip.
  function fieldHint(el, tag, name) {
    try {
      const parts = [];
      const norm = collapse(name || '');
      for (const attr of ['aria-errormessage', 'aria-describedby']) {
        const ids = el.getAttribute(attr);
        if (ids) { const t = idText(ids); if (t && t !== norm) parts.push(t); }
      }
      if (tag === 'input' || tag === 'textarea') {
        const ph = collapse(el.getAttribute('placeholder') || '');
        if (ph && ph !== norm && !parts.includes(ph)) parts.push(ph);
      }
      const ti = collapse(el.getAttribute('title') || '');
      if (ti && ti !== norm && !parts.includes(ti)) parts.push(ti);
      const hint = parts.join(' · ');
      return hint ? cap(hint, NAME_CAP) : '';
    } catch (e) { return ''; }
  }

  function isScrollable(el) {
    try {
      const st = getComputedStyle(el);
      const oy = st.overflowY;
      return (oy === 'auto' || oy === 'scroll') && el.scrollHeight > el.clientHeight + 8;
    } catch (e) { return false; }
  }

  let counter = 0;   // interactive index (actionable elements only)

  function walk(node, depth) {
    if (depth > MAX_DEPTH) return null;

    if (node.nodeType === Node.TEXT_NODE) {
      const t = collapse(node.nodeValue);
      if (t.length > 1 || (t.length === 1 && /\S/.test(t))) return { type: 'text', text: cap(t, TEXT_CAP) };
      return null;
    }
    if (node.nodeType !== Node.ELEMENT_NODE) return null;

    const el = node;
    const tag = el.tagName.toLowerCase();
    if (SKIP_TAGS.has(tag)) return null;
    if (el.getAttribute('aria-hidden') === 'true') return null;
    // A <label for="X"> only names another control — X's accessible name already
    // carries this text, so emitting the label too duplicates it. Skip it (unless
    // it also wraps a control, in which case dropping it would lose that control).
    if (tag === 'label' && el.hasAttribute('for')
        && !el.querySelector('input, select, textarea, button')) return null;

    if (tag === 'svg') {
      if (!isVisible(el, tag)) return null;
      const name = accName(el, tag, 'img');
      // A decorative icon with no accessible name carries no signal — skip it.
      if (!name) return null;
      return { type: 'element', tag: 'svg', role: 'img', name, interactive: false, index: null, states: [], landmark: null, children: [] };
    }
    if (tag === 'iframe' || tag === 'frame') {
      if (!isVisible(el, tag)) return null;
      const name = el.getAttribute('title') || el.getAttribute('name') || el.getAttribute('src') || '';
      return { type: 'element', tag, role: 'iframe', name: cap(collapse(name), NAME_CAP), interactive: false, index: null, states: [], landmark: null, children: [], note: 'iframe contents not traversed' };
    }

    const vc = visClass(el, tag);
    if (vc === 0) return null;  // display:none / collapsed region — drop the whole subtree

    // Native <select>: its <option>s have no layout box while the select is
    // closed, so the generic walk drops them. Emit each <option> as its own
    // indexed child so it can be picked with choose_option — the single dropdown
    // tool. The <combobox>/<listbox> line groups them but takes no index of its
    // own (nothing selects a <select> by value anymore).
    if (tag === 'select') {
      const srole = el.multiple ? 'listbox' : 'combobox';
      const sname = accName(el, tag, srole);
      const optionNodes = Array.from(el.options || []).map((opt) => {
        const ostates = statesOf(opt, 'option', 'option');
        let oindex = null;
        if (!ostates.includes('disabled')) {
          oindex = counter++;
          try { opt.setAttribute('data-bta-index', String(oindex)); } catch (e) {}
        }
        return { type: 'element', tag: 'option', role: 'option',
                 name: accName(opt, 'option', 'option'), value: '', states: ostates,
                 hint: '', interactive: oindex !== null, index: oindex,
                 landmark: null, scrollable: false, children: [] };
      });
      return { type: 'element', tag, role: srole, name: sname, value: '',
               states: statesOf(el, tag, srole), hint: fieldHint(el, tag, sname),
               interactive: false, index: null, landmark: null, scrollable: false,
               children: optionNodes };
    }

    const children = [];
    const pushKids = (list) => {
      for (const child of list) {
        const c = walk(child, depth + 1);
        if (!c) continue;
        if (c.type === 'group') { for (const gc of c.children) children.push(gc); }
        else children.push(c);
      }
    };
    // A <slot> with assigned nodes displays those light-DOM nodes, which are
    // already walked via the host — its own childNodes are inert fallback that
    // isn't rendered. Walk a slot's children only when nothing is assigned.
    const filledSlot = tag === 'slot' && el.assignedNodes && el.assignedNodes().length > 0;
    if (!filledSlot) pushKids(el.childNodes);
    // Web components render their real content in a shadow tree that childNodes
    // never reaches. Pierce open shadow roots so that content is stitched inline
    // as if it were the host's children. Closed roots return null — genuinely
    // unreachable from page script.
    try { if (el.shadowRoot) pushKids(el.shadowRoot.childNodes); } catch (e) {}

    if (vc === 1) {
      // Self-hidden wrapper (zero-size / opacity:0 / visibility:hidden): keep any
      // visible descendants we already collected, drop self.
      return children.length ? { type: 'group', children } : null;
    }

    const role = computeRole(el, tag);
    const landmark = landmarkOf(el, role);
    let interactive = isInteractive(el, tag, role);
    const name = accName(el, tag, role);
    const value = fieldValue(el, tag);
    const states = statesOf(el, tag, role);
    const hint = fieldHint(el, tag, name);
    const scrollable = isScrollable(el);

    // Covered by an overlay/modal → not actionable. Strip interactivity (so it
    // gets no index) but keep the node with an 'occluded' state as context —
    // the control exists, it's just behind something. Native form controls are
    // exempt (driven by element-targeted setters, not a coordinate click).
    if (interactive && !NATIVE_FORM_TAGS.has(tag) && isOccluded(el)) {
      interactive = false;
      states.push('occluded');
    }

    // Interactive elements get an index so they can be clicked/filled; a
    // scrollable container gets one too so scroll(index) can target it.
    let index = null;
    if (interactive || scrollable) {
      index = counter++;
      try { el.setAttribute('data-bta-index', String(index)); } catch (e) {}
    }

    const hasDirectText = Array.from(el.childNodes).some(
      (n) => n.nodeType === Node.TEXT_NODE && collapse(n.nodeValue).length > 1,
    );

    // Is this node worth its own line, or just a structural wrapper we flatten?
    const meaningful =
      interactive || landmark || scrollable ||
      role === 'heading' || role === 'img' || role === 'alert' ||
      role === 'table' || role === 'row' || role === 'cell' || role === 'columnheader' ||
      role === 'list' || role === 'listitem' || role === 'article' || role === 'dialog' ||
      (!!name && !!role) || hasDirectText;

    if (!meaningful) {
      return children.length ? { type: 'group', children } : null;
    }

    return { type: 'element', tag, role, name, value, states, hint, interactive, index, landmark, scrollable, children };
  }

  // Clear stale indices from a previous snapshot before re-tagging.
  try { document.querySelectorAll('[data-bta-index]').forEach((e) => e.removeAttribute('data-bta-index')); } catch (e) {}

  const root = walk(document.body, 0);
  const nodes = root ? (root.type === 'group' ? root.children : [root]) : [];

  return { url: location.href, title: document.title, nodes, interactive_count: counter };
})();
