<div align="center">

<img src="docs/logo.png" alt="" width="128">

# Kara

**Free offline speech to text for Windows.**
Hold a key, speak, and it types for you.

No account, no cloud, no subscription.

<img src="docs/screenshot-main-dark.png" alt="The main window" width="300">

[**Download for Windows**](https://github.com/bryramirezp/kara/releases/download/v0.2.4/Kara-Setup-0.2.4.exe)
 · [Website](https://bryramirezp.github.io/kara/)
 · [All files](https://github.com/bryramirezp/kara/releases/latest)

</div>

## What it does

You hold down a key. You talk. You let go. A moment later your words appear in
whatever app you were using — your browser, Word, a chat, anywhere you can type.

It uses [faster-whisper](https://github.com/SYSTRAN/faster-whisper) to turn speech
into text. Everything happens on your own computer. Your voice is never sent
anywhere, and you can unplug the internet and it still works.

People usually find this while looking for a **free alternative to Dragon
NaturallySpeaking**, an **offline speech to text tool for Windows**, or something
better than **Windows Voice Typing (Win+H)**, which needs an internet connection
and sends your audio to Microsoft.

| | Kara | Windows Voice Typing | Dragon | Otter.ai |
|---|---|---|---|---|
| Price | Free | Free | Paid | Free tier, then paid |
| Works offline | Yes | No | Yes | No |
| Needs an account | No | No | Yes | Yes |
| Types into any app | Yes | Yes | Yes | No |
| Voice commands | No | Some | Yes | No |
| Open source | Yes | No | No | No |

Dragon is bought mostly for driving a computer by voice, and this does not do
that. It dictates text, and that part is free and works with no connection.

## Download

**[Download the installer](https://github.com/bryramirezp/kara/releases/download/v0.2.4/Kara-Setup-0.2.4.exe)**
— <!--dl-size-->72 MB<!--/dl-size-->, for Windows 10 and 11. The link starts the download straight away.

Run it and you are done. You do not need to install Python or anything else.

> The installer is not signed, so Windows may show a blue box that says
> "Windows protected your PC". Click **More info**, then **Run anyway**.
> Signing costs money every year, which is a lot for a free tool.

The first time you open the app it downloads the speech model, about 460 MB.
This happens once. After that it works offline.

## How to use it

1. Hold **Insert** and speak.
2. Let go. Your words are typed where your cursor is.
3. Click the **⚙** button to change anything.

The app hides near the clock. Click that icon to bring it back.

To start it with Windows, tick the box during install. If you missed it, press
`Win+R`, type `shell:startup`, and drop a shortcut to the app in that folder.

## Settings

<img src="docs/screenshot-settings-light.png" alt="The settings panel" width="300">

| Setting | What it is for |
|---|---|
| **Appearance** | Light or dark. Changes right away. |
| **Hotkey** | The key you hold to record. Extra mouse buttons work too. |
| **Microphone** | Leave it on auto unless you have several and want a specific one. |
| **Language** | Pick the one you speak. This makes the app faster and more accurate. |
| **Device** | Your processor, or your graphics card if you have an NVIDIA one. |
| **Model** | Bigger models are more accurate but slower. |

### About the hotkey

Do not pick a normal letter or number. The app does not block the key, so if you
choose `q`, every `q` you type will start a recording. Good choices are `Insert`,
the `F13`–`F24` keys, or a side button on your mouse.

## Using a graphics card

The installer runs on your processor only. That works, but a large model will be
slow. If you have an NVIDIA graphics card and want the fast version, run it from
the source code instead:

```bat
git clone https://github.com/bryramirezp/kara.git
cd kara
py -3 -m pip install -r requirements.txt
py -3 -m pip install nvidia-cublas-cu12
launch.bat
```

That one extra package is all you need. The CUDA Toolkit you may already have on
your computer is not used, so it does not matter which version it is.

If the graphics card is not ready, the app quietly uses your processor instead.
It only complains when you set Device to **gpu** yourself, and then it tells you
what to install.

## Running from source

You need Python 3.10 or newer from [python.org](https://www.python.org/downloads/).
Tick "Add python.exe to PATH" while installing it.

```bat
py -3 -m pip install -r requirements.txt
launch.bat
```

`launch.bat` starts the app with no window. To see errors while you work on it,
run `py -3 kara.py` instead.

If you keep your packages in one specific Python, tell the launcher which one:

```bat
setx KARA_PYTHON "C:\Path\To\pythonw.exe"
```

Your settings live in `%LOCALAPPDATA%\Kara\settings.json`.

To rebuild the icons after changing the drawing in the app:

```bat
py -3 tools/make_logo.py
```

## Questions

### Does it work offline?

Yes. The first run downloads the speech model, about 460 MB. After that it never
needs the internet again.

### Is it free?

Yes, and open source under the MIT license. No account, no subscription, no
trial that runs out.

### Is this a free alternative to Dragon NaturallySpeaking?

For dictation, yes. It does not replace Dragon's voice commands for controlling
Windows by voice.

### How is it different from Windows Voice Typing (Win+H)?

Windows Voice Typing sends your audio to Microsoft and needs a connection. This
runs on your own machine, works with no connection at all, and lets you pick a
larger and more accurate model.

### Which languages does it support?

Spanish, English, Portuguese, French, German and Italian.

### Do I need a graphics card?

No. The download runs on your processor. See below if you have an NVIDIA card
and want it faster.

## Your privacy

- Your voice never leaves your computer.
- The microphone is only on while you hold the key. The rest of the time it is
  closed, so nothing is listening.
- The text is pasted with the clipboard, and whatever you had copied before is
  put back afterwards.

## License

MIT. See [LICENSE](LICENSE).
