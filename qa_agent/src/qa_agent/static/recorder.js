/**
 * QA Agent — Flow Recorder
 * Injected into the app under test via Playwright add_init_script().
 * Captures user interactions and pushes them to window.__qa_events[].
 * Python polls this array every 500 ms and drains it.
 */
(function () {
  'use strict';

  window.__qa_events = window.__qa_events || [];

  /* ── Selector generation ─────────────────────────────────────── */
  function bestSelector(el) {
    if (!el || el === document.body) return null;

    // 1. data-testid
    var tid = el.getAttribute('data-testid') || el.getAttribute('data-test-id') || el.getAttribute('data-cy');
    if (tid) return '[data-testid="' + tid + '"]';

    // 2. id
    if (el.id && !/^\d/.test(el.id)) return '#' + el.id;

    // 3. aria-label
    var al = el.getAttribute('aria-label');
    if (al) return el.tagName.toLowerCase() + '[aria-label="' + al.replace(/"/g, '\\"') + '"]';

    // 4. name attribute
    var nm = el.getAttribute('name');
    if (nm) return el.tagName.toLowerCase() + '[name="' + nm + '"]';

    // 5. placeholder
    var ph = el.getAttribute('placeholder');
    if (ph) return el.tagName.toLowerCase() + '[placeholder="' + ph.replace(/"/g, '\\"') + '"]';

    // 6. type for inputs
    if (el.tagName === 'INPUT' && el.type) return 'input[type="' + el.type + '"]';

    // 7. button / link with text
    var tag = el.tagName.toLowerCase();
    var txt = (el.innerText || el.textContent || '').trim().slice(0, 50);
    if (txt && (tag === 'button' || tag === 'a')) {
      return tag + ':has-text("' + txt.replace(/"/g, '\\"') + '")';
    }

    // 8. role
    var role = el.getAttribute('role');
    if (role && txt) return '[role="' + role + '"]:has-text("' + txt.replace(/"/g, '\\"') + '")';

    // 9. short CSS path (max 3 levels)
    var path = [];
    var cur = el;
    for (var i = 0; i < 3 && cur && cur !== document.body; i++) {
      var part = cur.tagName.toLowerCase();
      if (cur.className) {
        var cls = cur.className.toString().trim().split(/\s+/).filter(function(c) {
          return c && !/^(ng-|v-|js-|is-|has-)/.test(c);
        }).slice(0, 2);
        if (cls.length) part += '.' + cls.join('.');
      }
      path.unshift(part);
      cur = cur.parentElement;
    }
    return path.join(' > ') || tag;
  }

  function labelFor(el) {
    var txt = (el.innerText || el.textContent || el.value || '').trim().slice(0, 60);
    var al  = (el.getAttribute('aria-label') || '').trim().slice(0, 60);
    return al || txt || el.tagName.toLowerCase();
  }

  /* ── Event listeners ─────────────────────────────────────────── */

  // Clicks
  document.addEventListener('click', function (e) {
    var el = e.target;
    // Walk up to find meaningful element (button, a, [role=button])
    for (var i = 0; i < 4 && el; i++) {
      var tag = el.tagName ? el.tagName.toLowerCase() : '';
      if (tag === 'button' || tag === 'a' || tag === 'input' ||
          el.getAttribute('role') === 'button' || el.getAttribute('role') === 'menuitem' ||
          el.getAttribute('role') === 'tab' || el.getAttribute('role') === 'option') {
        break;
      }
      el = el.parentElement;
    }
    if (!el || el === document.body) return;

    var sel = bestSelector(el);
    if (!sel) return;

    // Skip if it's an input/textarea (fill events handle those)
    var tag2 = el.tagName ? el.tagName.toLowerCase() : '';
    if (tag2 === 'input' || tag2 === 'textarea' || tag2 === 'select') return;

    window.__qa_events.push({
      op: 'interact',
      action: 'click',
      selector: sel,
      label: labelFor(el),
      ts: Date.now(),
    });
  }, true);

  // Fill (input / textarea)
  document.addEventListener('change', function (e) {
    var el = e.target;
    var tag = el.tagName ? el.tagName.toLowerCase() : '';
    if (tag !== 'input' && tag !== 'textarea' && tag !== 'select') return;

    var sel = bestSelector(el);
    if (!sel) return;

    var isPassword = el.type === 'password';
    var value = isPassword ? '{{password}}' : (el.value || '');

    window.__qa_events.push({
      op: 'interact',
      action: tag === 'select' ? 'select' : 'fill',
      selector: sel,
      text: value,
      inputType: el.type || tag,
      isPassword: isPassword,
      label: labelFor(el),
      ts: Date.now(),
    });
  }, true);

  // Select (change already covers it but keep explicit for selects)
  document.addEventListener('input', function (e) {
    var el = e.target;
    var tag = el.tagName ? el.tagName.toLowerCase() : '';
    if (tag !== 'input' && tag !== 'textarea') return;
    // Only capture on input for debounce — change event does the final capture
  }, true);

  console.debug('[QA Recorder] injected and listening');
})();
