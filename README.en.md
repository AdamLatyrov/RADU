# RADU

[Русский](README.md) · **English version**

RADU is a Windows desktop app that runs actions from spoken phrases. Users configure commands and actions in the graphical interface.

## Download and run

1. Open the [latest RADU release](https://github.com/AdamLatyrov/RADU/releases/latest).
2. Download and run `RADU-Setup.exe`. It installs the app and creates a Start menu shortcut.
3. Launch RADU from the Start menu or from the desktop if you selected that shortcut.
4. Add and edit commands on the Commands tab.

The release includes the interface, speech engine, and Russian recognition model. Python, a terminal, command files, and separate model setup are not needed. For a portable copy, download `RADU-Windows.zip`, extract the whole archive, and run `RADU.exe` from the extracted folder. Windows may show a first-run warning because the app is not signed yet.

## Data and privacy

Commands and recordings are stored locally in the Windows user profile. User commands, recordings, the recognition model, and logs are not included in the source repository. Keep the extracted application folder intact.

## Project status

RADU is distributed as a Windows preview. Recognition accuracy depends on the spoken phrase, microphone, and Vosk model.

## Source layout

- `prototypes/Radu.Wpf/` — Windows interface.
- `assistant/` and `settings.py` — speech capture, recognition, and command execution.
- `packaging/` — release packaging.
- `docs/IDEAS_AND_SOURCES.md` — saved ideas and material for future research.

Development and architecture guidance is in [AGENTS.md](AGENTS.md).
