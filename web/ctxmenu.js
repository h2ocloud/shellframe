/* A small pop-up menu at the pointer: the app's own, drawn by the page.
 *
 * What it is for: the terminal's right-click. A click with nothing selected used to paste on
 * its own, and the selection is often gone by the time the click arrives (the TUI redraws under
 * it), so a copy turned into text nobody meant to send. Now that click only opens this menu,
 * and a paste happens when an item is picked by hand.
 *
 *   SFMenu.open(x, y, [{ label, hint, onSelect }], { onClose(why) })
 *   SFMenu.close()        SFMenu.isOpen()
 *
 * One menu at a time (opening another closes the first). It closes on Esc, a click outside,
 * the window losing focus, a scroll and a resize; `why` is 'select', 'escape', 'outside' or
 * 'other'. The first item has the keyboard, so Enter or Space picks it, Esc leaves; the arrow
 * keys move. The keyboard goes back to where it was (the terminal, the chat view's box) unless
 * the close was a click elsewhere, which decides for itself. Words come from the caller (the
 * page's language is the caller's). The colours are the app's dark ones (ctxmenu.css).
 */
(function (root) {
  'use strict';
  const doc = root.document;
  if (!doc) return;

  let cur = null;       // { el, off(): remove the listeners }

  function isOpen() { return !!cur; }

  function close(why) {
    const c = cur;
    if (!c) return;
    cur = null;
    c.off();
    if (c.el.parentNode) c.el.parentNode.removeChild(c.el);
    const back = c.prev;
    if (why !== 'outside' && back && back.isConnected && typeof back.focus === 'function') {
      try { back.focus({ preventScroll: true }); } catch (_) { /* gone from the page */ }
    }
    if (c.onClose) { try { c.onClose(why || 'other'); } catch (_) { /* a caller's bug must not leave the menu up */ } }
  }

  function open(x, y, items, opts) {
    close('other');
    items = (items || []).filter(it => it && it.label);
    if (!items.length) return null;
    const el = doc.createElement('div');
    el.className = 'sf-menu';
    el.setAttribute('role', 'menu');
    const buttons = items.map(it => {
      const b = doc.createElement('button');
      b.type = 'button';
      b.className = 'sf-menu-item';
      b.setAttribute('role', 'menuitem');
      const label = doc.createElement('span');
      label.textContent = it.label;
      b.appendChild(label);
      if (it.hint) {
        const h = doc.createElement('span');
        h.className = 'sf-menu-hint';
        h.textContent = it.hint;
        b.appendChild(h);
      }
      b.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        close('select');
        // after the menu is gone: what the item does may open a dialog or move the focus
        if (it.onSelect) { try { it.onSelect(); } catch (_) { /* nothing to do about it here */ } }
      });
      el.appendChild(b);
      return b;
    });
    doc.body.appendChild(el);

    // at the pointer, a little off it (the button is still down on some systems when the menu
    // appears, and its release must not land on an item), kept inside the window
    const vw = root.innerWidth || doc.documentElement.clientWidth;
    const vh = root.innerHeight || doc.documentElement.clientHeight;
    const w = el.offsetWidth, h = el.offsetHeight;
    let left = x + 2, top = y + 2;
    if (left + w > vw - 4) left = x - w - 2;
    if (top + h > vh - 4) top = y - h - 2;
    el.style.left = Math.max(4, left) + 'px';
    el.style.top = Math.max(4, top) + 'px';

    const onDown = (e) => { if (!el.contains(e.target)) close('outside'); };
    const onKey = (e) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        e.stopPropagation();           // never on to the CLI under it
        close('escape');
      } else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        e.stopPropagation();
        const i = buttons.indexOf(doc.activeElement);
        const n = buttons.length;
        const down = e.key === 'ArrowDown';
        buttons[i < 0 ? (down ? 0 : n - 1) : (i + (down ? 1 : n - 1)) % n].focus();
      } else if (e.key === 'Tab') {
        e.preventDefault();
        close('escape');
      }
    };
    const onOther = () => close('other');
    doc.addEventListener('mousedown', onDown, true);
    doc.addEventListener('keydown', onKey, true);
    doc.addEventListener('wheel', onOther, { capture: true, passive: true });
    root.addEventListener('blur', onOther);
    root.addEventListener('resize', onOther);
    cur = {
      el, onClose: opts && opts.onClose, prev: doc.activeElement,
      off() {
        doc.removeEventListener('mousedown', onDown, true);
        doc.removeEventListener('keydown', onKey, true);
        doc.removeEventListener('wheel', onOther, { capture: true });
        root.removeEventListener('blur', onOther);
        root.removeEventListener('resize', onOther);
      },
    };
    try { buttons[0].focus({ preventScroll: true }); } catch (_) { /* a hidden page cannot take focus */ }
    return { close };
  }

  root.SFMenu = { open, close, isOpen };
})(typeof window !== 'undefined' ? window : globalThis);
