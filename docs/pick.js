/* Which of the two installers this computer actually wants.
 *
 * There are two downloads and only one of them is right for any given machine,
 * which is a worse problem than it sounds. Two buttons of equal weight make
 * people stop and work out which is which, and the honest answer is that most
 * people do not know what is inside their computer. The release this page
 * announces exists because of exactly that: a beta tester ran the processor
 * build on an RTX 3060 for a week and reported the app was slow.
 *
 * So the page works it out instead of asking. WebGL will name the graphics
 * adapter, this reads the vendor out of that name, and the build that suits the
 * machine becomes the filled button while the other drops to a ghost.
 *
 * Three things it is careful about:
 *
 *   Intel is not an answer. A laptop with both an Intel chip and a separate
 *   NVIDIA card usually reports the Intel one here, because that is what the
 *   browser is drawing with. Concluding "no NVIDIA" from it would hide the GPU
 *   build from the very people this release was built for, so Intel is treated
 *   as "do not know" and the offer stays on screen.
 *
 *   Nothing is sent. The check runs here and the answer never leaves the page.
 *   That is worth saying out loud rather than being quietly true: a page that
 *   suddenly names your graphics card is unsettling unless it also tells you
 *   where that came from, and for this product in particular -- one whose whole
 *   claim is that your voice stays on your machine -- the explanation is the
 *   point rather than an apology.
 *
 *   It degrades. Without JavaScript, or in a browser that refuses to say (any
 *   Firefox with privacy.resistFingerprinting, Safari, Tor), the markup is
 *   already correct: the processor build is the filled button, because it is
 *   the answer that is never wrong.
 */
(function () {
  var blocks = document.querySelectorAll('[data-pick]');
  if (!blocks.length) return;

  function rendererString() {
    try {
      var canvas = document.createElement('canvas');
      var gl = canvas.getContext('webgl') || canvas.getContext('experimental-webgl');
      if (!gl) return '';
      var ext = gl.getExtension('WEBGL_debug_renderer_info');
      if (!ext) return '';
      return String(gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) || '');
    } catch (e) {
      return '';
    }
  }

  /* "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)"
     is what Chrome on Windows actually hands over. The middle field is the only
     part a person would recognise; the rest is the driver talking to itself. */
  function prettyName(raw) {
    var m = raw.match(/^ANGLE\s*\(([^,]*),\s*(.+?)(?:\s*,\s*[^,]*)?\)$/);
    var name = m ? m[2] : raw;
    return name
      .replace(/\s*\((?:R|TM)\)/gi, '')
      .replace(/\s+Direct3D\d+.*$/i, '')
      .replace(/\s+vs_\d+_\d+.*$/i, '')
      .replace(/\s+\(0x[0-9A-F]+\)/i, '')
      .replace(/\s+D3D\d+.*$/i, '')
      .replace(/\s+OpenGL.*$/i, '')
      .trim();
  }

  var raw = rendererString();
  var name = raw ? prettyName(raw) : '';
  var verdict = 'unknown';
  if (/nvidia|geforce|\brtx\b|\bgtx\b|quadro|tesla/i.test(raw)) {
    verdict = 'nvidia';
  } else if (/\bamd\b|radeon|\bati\b|apple\s+m\d/i.test(raw)) {
    verdict = 'other';           // a real card Kara cannot use yet
  }
  // Everything else, Intel included, stays "unknown" on purpose. See the note
  // at the top: on a laptop the Intel chip is often not the only card present.

  var PRIVATE = '<span class="pick-private">Read from your browser just now. ' +
                'It was not sent anywhere — the same thing Kara does with ' +
                'your voice.</span>';

  function apply(block) {
    var cpu = block.querySelector('.pick-cpu');
    var gpu = block.querySelector('.pick-gpu');
    var row = block.querySelector('.pick-row');
    var read = block.querySelector('.pick-read');
    if (!cpu || !gpu || !row) return;

    var cpuLabel = cpu.querySelector('.pick-label');
    var gpuLabel = gpu.querySelector('.pick-label');

    if (verdict === 'nvidia') {
      // The one case worth rearranging the page for.
      gpu.className = 'btn pick-btn pick-gpu';
      cpu.className = 'btn btn-ghost pick-btn pick-cpu';
      row.classList.add('gpu-first');
      if (gpuLabel) gpuLabel.textContent = 'Download the GPU build';
      if (cpuLabel) cpuLabel.textContent = 'Processor build instead';
      if (read) {
        read.innerHTML = 'Your graphics card is an <b>' + escapeHtml(name) +
          '</b>, and Kara can use it. This build is the one that does.' + PRIVATE;
        read.hidden = false;
      }
    } else if (verdict === 'other') {
      // Naming the card and then offering a 572 MB download it cannot use would
      // be worse than saying nothing. The GPU build stays reachable, quietly.
      gpu.className = 'pick-aside pick-gpu';
      if (gpuLabel) gpuLabel.textContent = 'There is also a build for NVIDIA cards';
      if (read) {
        read.innerHTML = 'Your graphics card is an <b>' + escapeHtml(name) +
          '</b>. Kara cannot use AMD or Intel cards yet, so this one — which ' +
          'runs on your processor — is the one you want.' + PRIVATE;
        read.hidden = false;
      }
    }
    // "unknown" changes nothing: the markup already asks the question plainly.
  }

  function escapeHtml(s) {
    return s.replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  Array.prototype.forEach.call(blocks, apply);
})();
