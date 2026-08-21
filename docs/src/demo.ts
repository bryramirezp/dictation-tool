// Replays one real dictation in the hero, captured on Bryan's own machine by
// tools/capture_demo.py (see docs/demo-data.json). Falls back to doing nothing
// -- leaving the real screenshots in place, exactly as they render without
// this script -- whenever real data isn't there or reduced motion is asked
// for. Never invents text or wording: the log lines and status words below
// ("Recorded 3.6s", "WRITING IT DOWN", "READY") are the app's own strings
// from karai18n.py, not made up for the site. See the comment above .demo in
// index.html.
(function () {
  "use strict";

  interface DemoData {
    text: string;
    listenMs: number;
    waveform: number[];
    gapMs: number;
  }

  var IDLE_LOG = '<p class="win-fake-dim">Ready to use.</p>';

  var root = document.querySelector<HTMLElement>("[data-demo]");
  if (!root) return;

  var shotsGroup = root.querySelector<HTMLElement>("[data-demo-shots]");
  var anim = root.querySelector<HTMLElement>("[data-demo-anim]");
  var logEl = root.querySelector<HTMLElement>("[data-demo-log]");
  var barsEl = root.querySelector<HTMLElement>("[data-demo-bars]");
  var statusEl = root.querySelector<HTMLElement>("[data-demo-status]");
  var captionEl = root.querySelector<HTMLElement>("[data-demo-caption]");

  if (!shotsGroup || !anim || !logEl || !barsEl || !statusEl || !captionEl) return;

  var reduceQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
  var stopped = false;
  var timers: number[] = [];

  function clearTimers(): void {
    for (var i = 0; i < timers.length; i++) window.clearTimeout(timers[i]);
    timers = [];
  }

  function schedule(fn: () => void, ms: number): void {
    timers.push(window.setTimeout(fn, ms));
  }

  // Whatever state the animation was in, hand the hero back to the real
  // screenshots -- the same thing a visitor sees with JavaScript off.
  function revertToStatic(): void {
    stopped = true;
    clearTimers();
    anim!.hidden = true;
    captionEl!.hidden = true;
    shotsGroup!.style.display = "";
  }

  reduceQuery.addEventListener("change", function (e) {
    if (e.matches) revertToStatic();
  });

  if (reduceQuery.matches) return;

  fetch("demo-data.json")
    .then(function (res) {
      if (!res.ok) throw new Error("no demo-data.json yet");
      return res.json();
    })
    .then(function (data: DemoData) {
      if (reduceQuery.matches) return;
      if (typeof data.text !== "string" || !data.text) return;
      if (!Array.isArray(data.waveform) || data.waveform.length === 0) return;

      var waveform = data.waveform;
      var bars: HTMLElement[] = [];
      for (var i = 0; i < waveform.length; i++) {
        var bar = document.createElement("i");
        barsEl!.appendChild(bar);
        bars.push(bar);
      }

      shotsGroup!.style.display = "none";
      anim!.hidden = false;
      captionEl!.hidden = false;

      function listenPhase(): void {
        if (stopped || reduceQuery.matches) { revertToStatic(); return; }
        anim!.classList.add("is-listening");
        statusEl!.textContent = "LISTENING";

        var start = performance.now();
        var duration = Math.max(200, data.listenMs);

        function frame(now: number): void {
          if (stopped) return;
          var t = Math.min(1, (now - start) / duration);
          var reached = Math.floor(t * bars.length);
          for (var i = 0; i < bars.length; i++) {
            var level = i <= reached ? waveform[i] : 0;
            bars[i].style.height = Math.max(2, (level || 0) * 26) + "px";
          }
          if (t < 1) {
            requestAnimationFrame(frame);
          } else {
            endListening();
          }
        }
        requestAnimationFrame(frame);
      }

      // The moment the key is let go: Kara's own log gets its "Recorded
      // 3.6s" line right away, and the status word goes to "WRITING IT
      // DOWN" while it transcribes -- see kara.py's _set_recording(False).
      function endListening(): void {
        anim!.classList.remove("is-listening");
        statusEl!.textContent = "WRITING IT DOWN";
        var seconds = (data.listenMs / 1000).toFixed(1);
        logEl!.insertAdjacentHTML(
          "beforeend",
          '<p class="win-fake-dim">Recorded ' + seconds + "s</p>"
        );
        schedule(pastePhase, Math.max(150, data.gapMs));
      }

      // Kara pastes the whole sentence at once -- it never types letter by
      // letter -- so the replay does the same rather than faking a typing
      // effect the app doesn't have.
      function pastePhase(): void {
        if (stopped || reduceQuery.matches) { revertToStatic(); return; }
        statusEl!.textContent = "READY";
        var said = document.createElement("p");
        said.className = "win-fake-said";
        said.textContent = data.text;
        logEl!.appendChild(said);
        schedule(holdPhase, 3400);
      }

      function holdPhase(): void {
        if (stopped || reduceQuery.matches) { revertToStatic(); return; }
        logEl!.innerHTML = IDLE_LOG;
        schedule(listenPhase, 1200);
      }

      listenPhase();
    })
    .catch(function () {
      // No capture yet, or it failed to load: the static screenshots are
      // already what's showing, so there is nothing left to do.
    });
})();
