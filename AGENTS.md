# RADU agent guide

This file is for coding agents. Human setup and download instructions belong in `README.md` and `README.en.md`.

## Product contract

RADU is a Windows GUI voice-command application. The user-facing entry point is `RADU.exe` from the packaged Windows release. End users must not need Python, a terminal, CMD files, or PowerShell scripts. Keep developer build steps out of user instructions.

## Architecture

- `prototypes/Radu.Wpf/` owns the desktop UI, command editor, history, palette, and process lifecycle for the speech engine.
- `assistant/` owns microphone capture, VAD segmentation, Vosk recognition, decision gates, action execution, and event/history recording.
- `settings.py` owns runtime paths and recognition constants. Packaged builds use `RADU_DATA_ROOT`, `RADU_COMMANDS_FILE`, and `RADU_VOSK_MODEL` to keep mutable data outside the installation folder.
- `packaging/RADU.Engine.spec` bundles the Python engine. `packaging/build-release.ps1` combines it with the self-contained WPF publish and model. The GitHub workflow creates the downloadable archive.

## Source map

- `tests/` contains the retained timing check for speech segments.
- `docs/IDEAS_AND_SOURCES.md` contains future research notes. These ideas are not part of the live recognition path.
- User commands, audio, logs, model files, and release artifacts are local data and are intentionally not tracked.

## Change boundaries

- Preserve the current speech filter and command decision policy unless the user explicitly asks to change recognition behavior.
- Keep user commands, recordings, review labels, and logs out of Git. They live under the current user's Local AppData folder.
- Treat `model/`, `data/`, `bin/`, and `releases/` as local or generated content, not canonical source.
- Do not copy user WAV files, command configuration, secrets, or machine-specific paths into a release.
- Keep Windows-facing UI controls accessible and use Lucide SVGs for interface icons.
- Keep the UI as the only user-facing launch and management surface. Build scripts are for maintainers and CI only.

## Build and delivery

The canonical application package is `RADU-Windows.zip`. It contains the WPF executable, packaged engine, and Vosk model. The user launches `RADU.exe`. Version tags matching `v*` trigger the Windows packaging workflow and attach that archive to a GitHub prerelease. A local build requires .NET 9, Python 3.12, dependencies from `requirements.txt`, and the Russian Vosk model under `model/`.

Use scoped builds for changes in one component. For release packaging, confirm the Windows publish completed and the archive includes `RADU.exe`, `RADU.Engine.exe`, its `_internal` runtime folder, and `model/vosk-model-small-ru-0.22`. Never report a release as available until the GitHub release and downloadable asset are visible.
