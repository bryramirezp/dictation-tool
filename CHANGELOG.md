# Changelog

All notable changes to Kara. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[semantic versioning](https://semver.org/spec/v2.0.0.html).

Every version below is a published release with an installer:
[all releases](https://github.com/bryramirezp/kara/releases).

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

[0.2.4]: https://github.com/bryramirezp/kara/releases/tag/v0.2.4
[0.2.3]: https://github.com/bryramirezp/kara/releases/tag/v0.2.3
[0.2.2]: https://github.com/bryramirezp/kara/releases/tag/v0.2.2
[0.2.1]: https://github.com/bryramirezp/kara/releases/tag/v0.2.1
[0.2.0]: https://github.com/bryramirezp/kara/releases/tag/v0.2.0
[0.1.0]: https://github.com/bryramirezp/kara/releases/tag/v0.1.0
