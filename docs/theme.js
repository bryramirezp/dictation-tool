/* The light/dark switch, shared by every page on the site.
 *
 * The choice itself is applied by a small inline script in each <head>, before
 * anything paints, so a returning visitor never sees the wrong theme flash
 * first. This file only draws the buttons and handles the clicks, which can
 * wait until the document is parsed.
 */
(function () {
  var SUN = '<svg class="i-light" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
            'stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="4.2"/>' +
            '<path d="M12 2.6v2.2M12 19.2v2.2M2.6 12h2.2M19.2 12h2.2' +
            'M5.4 5.4l1.6 1.6M17 17l1.6 1.6M18.6 5.4L17 7M7 17l-1.6 1.6"/></svg>';
  var MOON = '<svg class="i-dark" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
             'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
             '<path d="M20.5 14.3A8.5 8.5 0 1 1 9.7 3.5a6.8 6.8 0 0 0 10.8 10.8z"/></svg>';

  var root = document.documentElement;
  var toggles = document.querySelectorAll('[data-theme-toggle]');

  function paint() {
    var dark = root.getAttribute('data-theme') === 'dark';
    var label = dark ? 'Switch to light mode' : 'Switch to dark mode';
    Array.prototype.forEach.call(toggles, function (b) {
      b.innerHTML = SUN + MOON;        // CSS hides whichever does not apply
      b.setAttribute('aria-label', label);
      b.setAttribute('title', label);
    });
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute('content', dark ? '#0f0f0f' : '#ffffff');
  }

  Array.prototype.forEach.call(toggles, function (b) {
    b.addEventListener('click', function () {
      var next = root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
      root.setAttribute('data-theme', next);
      try { localStorage.setItem('theme', next); } catch (e) {}
      paint();
    });
  });
  paint();
})();
