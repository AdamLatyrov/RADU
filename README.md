# RADU

RADU is a Windows desktop voice-command app. Users configure phrases and actions in the graphical app. The Windows download contains the interface, speech engine, and Russian speech model. Python and command-line tools are not needed to run it.

## Download and start

1. Open the [latest RADU release](https://github.com/AdamLatyrov/RADU/releases/latest).
2. Download `RADU-Windows.zip` and extract the whole archive to a folder you can write to.
3. Start `RADU.exe` from that folder.
4. Add or edit commands in the Commands page.

Windows may show its first-run application warning because this preview is not signed. RADU stores commands and recordings in the current Windows user profile. Keep the extracted app folder together because the speech engine and model are beside the interface executable.

## For coding agents

Read [`AGENTS.md`](AGENTS.md) before changing code. It describes the architecture, the user-facing launch path, data boundaries, build workflow, and restrictions on the recognition pipeline. Start with the WPF shell in `prototypes/Radu.Wpf/` and the Python engine in `assistant/`. Do not treat archived release folders, local recordings, or ignored model data as canonical source.

## Project map

- `prototypes/Radu.Wpf/` contains the user interface and its process integration with the speech engine.
- `assistant/` and `settings.py` contain speech capture, recognition, command decisions, actions, and local history.
- `tests/` contains existing Python checks.
- `docs/IDEAS_AND_SOURCES.md` preserves research directions and references.
- `packaging/` contains the Windows release build definition. Users do not run it.
- `.github/workflows/windows-release.yml` builds a downloadable Windows archive and publishes it for version tags.

The Vosk model, recordings, logs, and personal command routes are not committed to the source repository. The release workflow downloads the public Russian Vosk model during packaging.

## Build a release

The release workflow runs on Windows, builds a self-contained .NET desktop app and packaged Python engine, places the model beside them, then creates `RADU-Windows.zip`. Push a `v*` tag to publish a prerelease. A local build requires the .NET 9 SDK, Python 3.12, the pinned build dependencies in `requirements-release.txt`, and the Russian Vosk model under `model/`.

## Current status

This is a preview. Recognition accuracy depends on the configured phrase, microphone, and Vosk model. Research-only acoustic checks and alternative models are not in the command execution path.
