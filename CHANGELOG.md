# Changelog

All notable changes to Kara. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[semantic versioning](https://semver.org/spec/v2.0.0.html).

Every version below is a published release with an installer:
[all releases](https://github.com/bryramirezp/kara/releases).

## [0.3.1] - 2026-08-21

Kara's own text — buttons, labels, hints, messages — was English no matter what
you dictated in. This release fixes that: the installer now offers all six
languages Kara already speaks, and the choice carries into the app itself, not
just into what it types for you.

### Added

- **The installer's own language picker now sets Kara's UI language.** Choose
  Spanish, English, Portuguese, French, German or Italian at setup, and every
  label, button, menu and message in Kara comes up in that language — separate
  from the LANGUAGE setting, which is still just what you dictate in.
- **An APP LANGUAGE selector in Settings**, so the UI language can be changed
  later without reinstalling. Applies right away, the same as the theme switch.

### Note

- Spanish and English were written and checked by hand. Portuguese, French,
  German and Italian are a first-pass translation — flag anything that reads
  oddly and it'll get fixed in a follow-up patch.

## [0.3.0] - 2026-08-21

Two beta testers said the app was slow and lost words. Both of them turned out to
be running the smallest model on their processors — one of them on an RTX 3060 —
because the download left the CUDA libraries out and hid the graphics card option
along with them. So the first thing this release fixes is not the speed. It is
that nobody, the developer included, could tell what they were actually running.

### Added

- **A second installer, for NVIDIA cards.** `Kara-Setup-GPU` carries the CUDA
  libraries, which is why it weighs about 570 MB against 72 MB, and on a card
  it is not a small difference. Nothing else is needed: no CUDA Toolkit, no separate
  download, just a current driver. Either installer can be run over the other and
  they share their settings. AMD and Intel cards are still not supported, because
  ctranslate2 has no backend for them on Windows.
- **Numbers are typed as numbers.** "mil doscientos pesos" comes out as
  1200 pesos, "son las tres cuarenta y cinco" as son las 3:45, "tres coma cinco"
  as 3,5 and "un veinte por ciento" as un 20%. Spanish and English. A bare "un",
  "una" or "uno" is left alone, because those are articles and pronouns far more
  often than they are the number one. Settings -> TEXT turns it off.
- **Spoken punctuation.** Say "punto y aparte", "nueva linea", "abre parentesis"
  or "signo de interrogacion" and you get the punctuation rather than the words.
  Only phrases nobody says by accident: plain "coma" and "punto" are ordinary
  Spanish, so they live behind the "all" setting and are off by default.
- Opening question and exclamation marks. Whisper supplies the closing one and
  drops the opener perhaps half the time; a Spanish sentence ending in ? now gets
  its inverted mark back.
- **Export diagnostics**, in Settings. Writes a zip with a report on the machine
  (processor, graphics card, microphones and their audio backends, resolved
  device and model, library versions), the settings as saved, and the timing log.
  Then it opens the folder and stops: nothing is sent, and the file is yours to
  pass on or delete.

### Changed

- **The graphics card option now appears whenever a graphics card can be used.**
  Until now the packaged build hid it unconditionally and forced Device back to
  `auto`, because no packaged build could use a card. That was true, and it was
  also why two beta testers spent an evening reporting that the app was slow and
  lost words while both of them ran the smallest model on their processors -- one
  of them on an RTX 3060, with everyone involved, the developer included,
  believing the card was in use. The three places that inferred this from "is
  this a packaged build" now ask whether the CUDA libraries load.
- Auto no longer refuses `large-v3-turbo` unless 6 GB of video memory is free.
  Its weights are about 1.6 GB, so the old threshold was several times what it
  needs, and the effect was that any card with a browser open fell all the way
  to `small`. The scale is now 2.5 GB for `large-v3-turbo` and 1.2 GB for
  `small`. There is deliberately no `medium` step between them: it wants as much
  memory as large-v3-turbo while being both slower and less accurate, so
  anywhere medium would fit, the better model fits too.
- The timing log now runs always, rather than only when the `KARA_DEBUG_LOG`
  environment variable was set. Nobody was ever going to run `setx` before using
  a dictation tool, which meant the machines that were slow were exactly the ones
  that had recorded nothing. It is capped at 2 MB, it still records timings and
  device names and never what was said, and Kara still has no way to send it.
  `KARA_DEBUG_LOG` is ignored from now on, since there is nothing left for it
  to switch on; `KARA_MACHINE` still names the machine inside the file.
- ctranslate2 gets one thread per physical core, up to eight, instead of the
  four it defaults to regardless of the machine.
- Beam width is 1 on the processor and stays at 5 on a graphics card. Beam 5
  weighs five candidate transcriptions for what is usually a comma's worth of
  difference, and on a processor that is most of the wait.
- Silence is trimmed before transcription. Push-to-talk recordings start and end
  with some, because the key goes down before you speak and up after you stop.

  Together those three take a 66-second recording on a six-core processor from
  14.8s to 9.1s, with byte-identical output on the test sample.

- Whisper is now primed with a formatted sentence before long recordings, on
  the theory that it copies the punctuation style of whatever it is primed with.
  Said plainly: this could not be shown to help. On 66 seconds of clean, well
  articulated speech the output was byte-identical with and without it. The case
  it is aimed at is fast unpunctuated speech, which is the one case a synthesised
  test voice cannot produce, so it ships unproven. It is skipped on recordings
  under three seconds, where it was measured steering the result somewhere worse
  and where there is no punctuation to recover anyway.

### Fixed

- Dictating no longer wipes the clipboard when it held an image or a copied
  file. The old code read the clipboard as text, got an empty string back, and
  wrote that empty string over the picture once the paste was done.

## [0.2.4] - 2026-08-16

The window stopped looking like a Tk utility and started looking like the thing
the website advertises.

### Added

- Rounded corners on the window itself, through `DwmSetWindowAttribute` on
  Windows 11 and a `SetWindowRgn` region on Windows 10. The window is
  borderless, which means Windows does not round it for free.
- The brand mark in the header, in place of a plain bullet. It turns red while
  recording. It is the same drawing as the tray icon, rendered by the app rather
  than loaded from a file.
- Everything you have dictated stays in the window, newest at the bottom.
- Developer instrumentation behind the `KARA_DEBUG_LOG` environment variable,
  timing each step between the key going down and the window saying LISTENING.
  It writes to `%LOCALAPPDATA%\Kara\trace.jsonl`, records durations and device
  names but never what was said, has no network code, and is off unless that
  variable is set.

### Changed

- The main view is flat. No cards, no boxes inside the window: the window is the
  card. Two bordered panels stacked inside one window read as two windows.
- Sentences you dictated are 11pt and white; the app's own lines are 9pt and
  grey. `Ready to use.` lost its lime, since it was the brightest thing on
  screen for a line that only means the model finished loading. Failures keep
  their red.
- The website shows photographs of the real window where it used to animate a
  CSS drawing of an older one.
- Download links carry the version, so the file that lands in your Downloads
  folder is `Kara-Setup-0.2.4.exe` rather than an undated `Kara-Setup.exe`.

### Removed

- The typing animation and its caret. It delayed text that had already been
  pasted into the other window, and then replaced it with the next sentence.
- The separate history view and the button that opened it, now that the history
  is simply there.
- The winget manifests, the package submissions, and every mention of winget on
  the site and in the README. The package was never merged and is not being
  pursued.

## [0.2.3] - 2026-08-15

### Fixed

- A model file held open by another process is now reported as locked rather
  than as corrupt, so the fix offered matches the problem.

## [0.2.2] - 2026-08-15

### Fixed

- The key is refused until the model has finished loading, and says so. It used
  to accept the recording and only admit there was no model afterwards, once you
  had already said your sentence.

## [0.2.1] - 2026-08-15

### Fixed

- The selected segment in the settings panel was unreadable: near-white text on
  the brand lime, which is a light colour.

## [0.2.0] - 2026-08-15

### Changed

- Renamed from Dictation Tool to **Kara**, with an identity built around the
  status ring. Settings written by the old name are carried across once.
- The progress bar became a level meter driven by the actual microphone, so it
  answers "is it even hearing me?" — a question an animated bar cannot, because
  it moves the same whether you speak or not.

### Added

- A light theme and a switch for it, in the app and on the website.

## [0.1.0] - 2026-08-14

First release, under the name Dictation Tool.

[0.3.1]: https://github.com/bryramirezp/kara/releases/tag/v0.3.1
[0.3.0]: https://github.com/bryramirezp/kara/releases/tag/v0.3.0
[0.2.4]: https://github.com/bryramirezp/kara/releases/tag/v0.2.4
[0.2.3]: https://github.com/bryramirezp/kara/releases/tag/v0.2.3
[0.2.2]: https://github.com/bryramirezp/kara/releases/tag/v0.2.2
[0.2.1]: https://github.com/bryramirezp/kara/releases/tag/v0.2.1
[0.2.0]: https://github.com/bryramirezp/kara/releases/tag/v0.2.0
[0.1.0]: https://github.com/bryramirezp/kara/releases/tag/v0.1.0
