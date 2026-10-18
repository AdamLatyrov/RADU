# RADU

[Русский](README.md) · **English version**

RADU is a Windows desktop app that runs actions from spoken phrases. Users configure commands and actions in the graphical interface.

## Download and run

1. Open the [latest RADU release](https://github.com/AdamLatyrov/RADU/releases/latest).
2. Download `RADU-Windows.zip` and extract the entire archive to a writable folder.
3. Run `RADU.exe`.
4. Add and edit commands on the Commands tab.

The release includes the interface, speech engine, and Russian recognition model. Python, a terminal, and command-line scripts are not needed to run the app. Windows may show a first-run warning because the app is not signed yet.

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
