/* The app's own confirm and alert, drawn by the page.
 *
 * Why: a native confirm() or alert() in WebView2 (Windows) is titled with the page's address
 * ("127.0.0.1:47342 says"), which tells the person nothing and looks like a fault. These take
 * the same words and give the same answers, but they return a Promise, so a caller awaits:
 *
 *   if (await SFDialog.confirm(message, { title, ok, cancel })) { ... }     -> true | false
 *   await SFDialog.alert(message, { title, ok })                            -> undefined
 *   SFDialog.isOpen()
 *
 * One at a time, in the order asked (a blocking native one did that for free): a second one
 * asked while one is up waits behind it. Enter confirms (on the Cancel button it cancels), Esc
 * cancels (an alert just closes), Tab and Shift+Tab move between the buttons and stay inside the
 * dialog, a click on the dim area cancels. The confirm button has the keyboard when it opens, and
 * the keyboard goes back to where it was when it closes. The message is text, never HTML, and
 * keeps its line breaks. The look is the app's modal (index.html .modal); the app has one theme,
 * the dark one. The button words follow the page's language unless the caller gives them.
 */
(function (root) {
  'use strict';
  const doc = root.document;
  if (!doc) return;

  const isZh = () => {
    try { return /^zh/i.test(root.__sfHost.lang()); } catch (_) { return /^zh/i.test((root.navigator && root.navigator.language) || ''); }
  };
  let chain = Promise.resolve();      // the dialogs asked for, one after another
  let shown = 0;

  function present(kind, message, opts) {
    return new Promise(resolve => {
      const zh = isZh();
      const prev = doc.activeElement;
      const ov = doc.createElement('div');
      ov.className = 'modal-overlay open sf-dialog';
      const box = doc.createElement('div');
      box.className = 'modal sf-dialog-box';
      box.setAttribute('role', 'alertdialog');
      box.setAttribute('aria-modal', 'true');
      if (opts.title) {
        const h = doc.createElement('h2');
        h.textContent = opts.title;
        box.appendChild(h);
      }
      const msg = doc.createElement('div');
      msg.className = 'sf-dialog-msg';
      msg.textContent = message == null ? '' : String(message);
      box.appendChild(msg);
      const row = doc.createElement('div');
      row.className = 'sf-dialog-btns';
      const button = (cls, text, answer) => {
        const b = doc.createElement('button');
        b.type = 'button';
        b.className = 'btn ' + cls;
        b.textContent = text;
        b.addEventListener('click', (e) => { e.preventDefault(); e.stopPropagation(); finish(answer); });
        row.appendChild(b);
        return b;
      };
      const cancelBtn = kind === 'confirm' ? button('btn-ghost', opts.cancel || (zh ? '取消' : 'Cancel'), false) : null;
      const okBtn = button('btn-primary', opts.ok || (zh ? '確定' : 'OK'), true);
      const buttons = cancelBtn ? [cancelBtn, okBtn] : [okBtn];
      box.appendChild(row);
      ov.appendChild(box);
      msg.id = 'sf-dialog-msg-' + (++shown);
      box.setAttribute('aria-describedby', msg.id);

      let done = false;
      function finish(answer) {
        if (done) return;
        done = true;
        doc.removeEventListener('keydown', onKey, true);
        doc.removeEventListener('focusin', onFocusIn, true);
        if (ov.parentNode) ov.parentNode.removeChild(ov);
        // after this turn, so the keystroke that closed it does not reach what has the keyboard next:
        // back to where it was (the terminal, a dialog this one was asked from); if nothing had it, or
        // that is gone from the page (the rename box that asked), the terminal, unless another dialog is up
        setTimeout(() => {
          if (prev && prev !== doc.body && prev.isConnected && typeof prev.focus === 'function') {
            try { prev.focus({ preventScroll: true }); } catch (_) { /* nothing to do */ }
          } else if (!doc.querySelector('.modal-overlay.open')) {
            const h = root.__sfHost, s = h && h.sessions && h.sessions()[h.active && h.active()];
            try { if (s && s.term) s.term.focus(); } catch (_) { /* no terminal to go back to */ }
          }
        }, 0);
        resolve(kind === 'confirm' ? !!answer : undefined);
      }
      const stop = (e) => { e.preventDefault(); e.stopPropagation(); };
      function onKey(e) {
        if (e.isComposing || e.keyCode === 229) return;
        if (e.key === 'Escape') { stop(e); finish(false); }
        else if (e.key === 'Enter') { stop(e); finish(doc.activeElement === cancelBtn ? false : true); }
        else if (e.key === 'Tab') {
          stop(e);
          const i = buttons.indexOf(doc.activeElement);
          buttons[(i + (e.shiftKey ? buttons.length - 1 : 1) + (i < 0 ? 1 : 0)) % buttons.length].focus();
        }
      }
      // something else took the keyboard (a window regaining focus, a deferred focus of the
      // terminal): it comes back to the dialog, whose buttons are all there is to type into
      function onFocusIn(e) { if (!ov.contains(e.target)) okBtn.focus(); }
      ov.addEventListener('mousedown', (e) => { if (e.target === ov) { stop(e); finish(false); } });
      doc.addEventListener('keydown', onKey, true);
      doc.addEventListener('focusin', onFocusIn, true);
      doc.body.appendChild(ov);
      okBtn.focus();
    });
  }

  // one at a time, in the order asked; a failure inside one never stops the rest
  function ask(kind, message, opts) {
    const run = () => present(kind, message, opts || {});
    const result = chain.then(run, run);
    chain = result.catch(() => {});
    return result;
  }

  root.SFDialog = {
    confirm: (message, opts) => ask('confirm', message, opts),
    alert: (message, opts) => ask('alert', message, opts),
    isOpen: () => !!doc.querySelector('.sf-dialog'),
  };
})(typeof window !== 'undefined' ? window : globalThis);
