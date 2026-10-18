# RADU guide for coding agents

Use this file to answer questions about RADU and to make changes safely. It is the technical project guide for coding agents. Human download and setup instructions are in `README.md` and `README.en.md`.

## What RADU is

RADU is a Windows voice-command app. A person configures spoken phrases and actions in the WPF interface. A Python process captures microphone audio, detects speech, transcribes it with Vosk, checks the transcript, and executes a configured action. The WPF app owns the interface and process lifecycle. Python owns audio and command decisions.

The product is an early preview. The public GitHub repository has no selected software license. Do not describe it as open source under a license, promise production readiness, or imply that experimental recognition ideas are implemented.

## User-facing downloads

- `RADU-Setup.exe` is the graphical installer. It installs RADU for the current Windows user, creates a Start menu shortcut, offers an optional desktop shortcut, and can launch RADU after installation.
- `RADU-Windows.zip` is a portable package for people who prefer extracting the application themselves.
- Both packages contain the WPF app, the packaged Python engine, its support files, and the Russian Vosk model. `RADU.exe` is the interface executable and cannot run by itself without the rest of the package.
- User commands, settings, history, logs, and recordings live under `%LOCALAPPDATA%\RADU-V3-WPF`. Uninstalling the app must not delete that data.
- The speech model and application binaries are distributed together. The installer does not need Python, a terminal, a network connection, or separate model setup after download.

## Architecture and request flow

1. `prototypes/Radu.Wpf/` contains the Windows UI. `MainWindow.xaml` defines the shell; partial `MainWindow.*.cs` files hold page, history, command editing, scrolling, theme, visualizer, and window behavior.
2. `VoiceEngine.cs` starts `RADU.Engine.exe` beside the UI and exchanges newline-delimited JSON events and controls over standard output and input. Keep stdout reserved for this protocol in packaged mode. Diagnostics go to stderr and the local rotating log.
3. `assistant/main.py` opens the microphone, tracks live audio level, configures the Vosk model and command registry, runs voice activity detection, archives segments, and coordinates recognition workers.
4. `assistant/audio/speech_detector.py` segments microphone input. `assistant/recognition_pipeline.py` handles Vosk results, checks the phrase, executes accepted actions, records history, and emits the result back to WPF.
5. `assistant/recognition.py` contains the command acceptance decision. `assistant/commands.py` normalizes and resolves configured phrases. `assistant/actions.py` implements action types. `assistant/service.py` owns the Python side of the UI protocol.

### Recognition behavior

- Audio is mono at 16 kHz. Frames are 20 ms. WebRTC VAD uses mode 2, starts capture after three speech frames, includes a 300 ms pre-roll, and ends a phrase after 200 ms of silence.
- Active speech is limited to four seconds, with the end-silence allowance handled separately. Vosk can close a segment early when its partial transcript exactly matches a configured phrase.
- Vosk receives a restricted grammar built from configured phrases, explicit negative variants, and `[unk]`. A negative phrase such as «не открой блокнот» is present as a non-action path and must never trigger the positive action.
- RADU executes only a complete configured phrase after normalization. Normalization lowercases text, folds «ё» to «е», and collapses whitespace. There is no semantic nearest-command search or fuzzy matching in the live path.
- Word confidence must be present and every available word confidence must meet `WORD_CONFIDENCE_MIN`, currently 1.0. Do not silently change this threshold, the grammar, or any execution gate while doing unrelated packaging or UI work.
- Current protections do not guarantee that Vosk will transcribe the sounds as spoken. Restricted grammar can still produce a wrong configured phrase. Acoustic MFCC/DTW gating and alternative-model experiments are not active in the runtime.
- Success feedback, the brief microphone color change, and the application confirmation sound are driven by separate UI/service events. Preserve the action-start handshake and command-result event when changing the engine protocol.

### Commands and actions

- The installed command catalog is copied on first launch to `%LOCALAPPDATA%\RADU-V3-WPF\commands.json`. Later UI changes are written there and reloaded by the engine. Never ship a user's live catalog.
- The source starter catalog is `prototypes/Radu.Wpf/commands.default.json`. Its top-level property is `commands`; each command has an `id`, a `phrases` list, and an `action` object with a `type` and `value`.
- Duplicate phrases are rejected after normalization. All phrases in a command resolve to that command's action.
- Supported action types are `open_app` and `open_url`. Keep execution and validation in `assistant/actions.py`; use the UI editor to expose these actions to the user.
- Deleting a command removes its configured phrases from the live catalog. It does not delete prior recordings or execution history.

### Local data and privacy

- `settings.py` defines paths and audio/recognition constants. Packaged mode sets `RADU_DATA_ROOT`, `RADU_COMMANDS_FILE`, and `RADU_VOSK_MODEL` explicitly.
- `assistant/archive.py` writes captured utterances and JSON sidecars under the local data root, organized by UTC date. Sidecars retain the transcript, Vosk word confidence, decision checks, VAD details, and stage timings needed for diagnosis.
- `assistant/execution_history.py` appends successful executions to `command_history/executed_commands.jsonl`, including the recognized text and references to its WAV and JSON files. This is the data source for the History page.
- Session summaries and rotating logs are local. Never add WAV recordings, personal command routes, user catalogs, logs, model files, machine-specific paths, or generated packages to Git.
- Do not delete existing user data, add automatic retention, or change data locations as cleanup. The installer and uninstaller must leave Local AppData intact.

## Repository map

- `assistant/` — microphone engine, speech segmentation, Vosk pipeline, decisions, actions, diagnostics, event protocol, archive, and history.
- `prototypes/Radu.Wpf/` — the production-facing WPF app despite the historical `prototypes` folder name. It is the only graphical user interface.
- `packaging/RADU.Engine.spec` — PyInstaller configuration for the Python engine.
- `packaging/build-release.ps1` — builds the app folder, portable ZIP, and installer EXE.
- `packaging/RADU.Setup.iss` — Inno Setup installer definition.
- `.github/workflows/windows-release.yml` — Windows CI package build and GitHub release publishing.
- `requirements.txt` — Python engine and packaging dependencies.
- `tests/test_audio_segment_timing.py` — focused check for speech-to-decision timing boundaries.
- `prototypes/Radu.Wpf/DESIGN_SYSTEM.md` — current interface design rules.
- `docs/IDEAS_AND_SOURCES.md` — future research links only; these approaches are not live features.

## Safe change boundaries

- Preserve the existing speech filter, phrase matching, confidence gate, command grammar, and action behavior unless the user explicitly asks to change recognition.
- Keep the Python-to-WPF JSON-lines event names and meanings compatible across both processes. Update both sides together when a protocol change is necessary.
- Keep user-visible controls accessible and use the existing Lucide SVG icon set. Keep user flows inside the graphical app; scripts are for maintainers and CI.
- Avoid introducing another runtime, framework, service, database, or model unless the user asks for that change and its resource cost is understood.
- Prefer a small change in the owning module. Do not add wrappers or duplicate configuration when an existing module already owns the behavior.
- Do not store raw logs or transcripts in project memory. After a meaningful project change, update the NeuroCorp `_agent/PROJECT_MEMORY.md` and append one short dated line to `_agent/PROJECT_HISTORY.md`.

## Build, release, and verification

- The canonical release is Windows x64. It contains `RADU.exe`, `RADU.Engine.exe`, the PyInstaller `_internal` folder, and `model/vosk-model-small-ru-0.22`.
- A local full package build needs .NET 9, Python 3.12, dependencies from `requirements.txt`, the Russian Vosk model at `model/vosk-model-small-ru-0.22`, and Inno Setup 6 on PATH or in its standard installation directory.
- GitHub Actions installs Inno Setup, builds both download options on Windows, and attaches both files to a prerelease for tags beginning with `v`.
- After packaging changes, verify the installer and ZIP are both created and contain the same complete runtime. For a published release, inspect the GitHub release itself and confirm both downloadable assets are present before telling the user it is ready.
- For code changes, use the narrow relevant checks. Do not claim tests, a build, a microphone session, a user-interface inspection, or a release succeeded unless that exact check was run and passed.
