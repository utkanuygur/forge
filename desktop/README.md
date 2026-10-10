# Forge for macOS

A desktop app that runs Forge on your Mac: it checks Docker, prepares the sandbox image, starts a
bundled Forge server on 127.0.0.1 and opens the Forge UI in a native window. No Python, uv or Node is
needed on the machine that runs the app; Docker Desktop is.

## Requirements

To run the app: an Apple Silicon Mac (arm64) with macOS 12 or later, and Docker Desktop installed and
running. cloudflared is optional (`brew install cloudflared`) and only needed for public links.

To build it: everything above, plus uv, Node 22 or later with npm, and the repository's Python
dependencies (`uv sync` at the repo root).

## Build

```bash
cd desktop
npm install                 # Electron, electron-builder, playwright-core (pinned)
npm run icon                # optional: regenerate assets/icon.png and assets/icon.icns
npm run build:server        # web/dist + PyInstaller server into build/server/forge-server
npm run dist                # build:server, then dist/Forge-<version>-arm64.dmg
```

`npm run build` makes only the `.app`, without a dmg, and `npm run package` repackages without
rebuilding the server. Packaging runs in `~/Library/Caches/forge-desktop/dist` (where the `.app`
stays) and only the dmg is copied to `desktop/dist`: folders synced by iCloud Drive, such as
Desktop, add Finder attributes to app bundles, which breaks their code signature. An afterPack hook
(`scripts/after-pack.js`) clears those attributes and ad hoc signs the bundle. `build:server` runs
`npm run build` in `web/` first; if that fails it keeps the existing `web/dist`. Set
`SKIP_WEB_BUILD=1` to skip the web build entirely.

For fast iteration, `npm run dev` starts Electron from this folder and runs the server with
`uv run uvicorn forge.server.app:app` from the repo instead of the frozen binary. Dev mode keeps its
own settings in `~/Library/Application Support/Forge Dev` and logs in `~/Library/Logs/Forge Dev`.

## Install and first launch

Open the dmg and drag Forge to Applications. The app is ad hoc signed but not signed with an Apple
Developer ID or notarized, so macOS blocks the first launch. Either right click Forge.app and
choose Open (on recent macOS versions: try to open it once, then click Open Anyway in System
Settings > Privacy & Security), or run:

```bash
xattr -dr com.apple.quarantine /Applications/Forge.app
```

On first launch Forge builds the `forge-sandbox` Docker image from the bundled Dockerfile, which
takes a few minutes, then opens Settings to ask for a Claude API key.

## How it works

| Piece | File | What it does |
|---|---|---|
| Main process | `src/main.js` | Startup flow, windows, menus, settings IPC, quit cleanup |
| Docker | `src/docker.js` | `docker info`, `docker image inspect`, first-run `docker build` |
| Server | `src/server.js` | Picks a free port, spawns the server, polls `/healthz`, writes the log |
| Settings | `src/settings.js` | `settings.json` with the key encrypted by `safeStorage` |
| Public link | `src/tunnel.js` | `cloudflared tunnel --url http://127.0.0.1:<port>` |
| Pages | `src/ui/` | Startup and Settings windows (local HTML, no remote content) |
| Server entry | `../forge/desktop_entry.py` | Runs uvicorn on `--host`/`--port`; exits if the app process is gone |

Startup runs four steps in a small window: check Docker, make sure the sandbox image exists, start
the server, and wait until `/healthz` answers. If Docker is missing or stopped, the window explains
that Forge needs Docker Desktop and offers Get Docker Desktop (or Open Docker Desktop) and Retry.
GUI apps on macOS get a minimal PATH, so the app looks for `docker`, `cloudflared` and `uv` in
`/usr/local/bin`, `/opt/homebrew/bin`, `/Applications/Docker.app/Contents/Resources/bin` and
`~/.docker/bin`, and passes that PATH to the server so it can start sandbox containers.

The server binary is a PyInstaller onedir build in `Forge.app/Contents/Resources/forge-server`. It
listens on 127.0.0.1 only and gets this environment: `FORGE_DATA_DIR`, `FORGE_EXECUTOR=docker`,
the PATH above, `ANTHROPIC_API_KEY` (when saved), `FORGE_DEFAULT_MODEL` and `FORGE_GRADER_MODEL`
(when set in Settings). Its working directory is the data folder, so a `.env` file placed there is
also read. `forge/schema.sql` and `web/dist` are bundled as data at the paths `forge/db.py` and
`forge/server/app.py` compute from `__file__`, so nothing in the server changes when frozen.

Example agents ship inside the bundle too, so the Agents page can add them with one click (the
"Add example agents" card on an empty Agents page, or the Examples button). `build-server.sh` runs
`python -m forge.examples stage build/examples-data/demo`, which copies only what an install needs
(each example's `agent.json`, `evals.json` and seed files, plus `demo/repo-maintainer.json`,
`demo/evals.json` and `demo/acme-orders`), and bundles that folder as `_internal/demo`. Reference
solutions, build scripts, `prompt_checks.json` and caches are left out. When frozen,
`forge/examples.py` reads from `sys._MEIPASS/demo`; `FORGE_EXAMPLES_DIR` overrides the location.

| Location | Contents |
|---|---|
| `~/Library/Application Support/Forge/data` | Forge database and run artifacts (File > Open Data Folder) |
| `~/Library/Application Support/Forge/settings.json` | Models and the encrypted API key |
| `~/Library/Application Support/Forge/window-state.json` | Main window size and position |
| `~/Library/Logs/Forge/server.log` | Server output, rotated at 5 MB, 3 old files kept (Help > Show Logs) |

The API key is encrypted with Electron `safeStorage`, whose key lives in the macOS Keychain item
"Forge Safe Storage". Only ciphertext is written to disk and the key is never logged. Settings can
test a key against `GET https://api.anthropic.com/v1/models`. Saving restarts the server. Without a
key Forge still works for agents on local models (OpenAI compatible servers such as Ollama).

Share > Start Public Link runs a Cloudflare quick tunnel and shows the `trycloudflare.com` address.
Requests through the tunnel carry proxy headers, so the server treats them as remote: share links
and the API with an agent key work, the builder does not. Share > Stop Public Link ends it.

Security: every window uses `contextIsolation`, a sandboxed renderer and no Node integration. The
main window has no preload and may only navigate within the local server origin; other links open
in the default browser and new windows are refused. The Startup and Settings windows get small
preloads exposing only their own actions. The only web permission granted is clipboard writing.

Quitting stops the tunnel and the server with SIGTERM, then SIGKILL after 5 seconds. If the app is
killed outright, the server notices its parent is gone (`FORGE_PARENT_PID`) and exits by itself.

## Testing

`npm run verify` drives the app with Playwright's Electron support using a temporary settings folder
(`FORGE_DESKTOP_USERDATA`), so your real settings are untouched. It sets `FORGE_DESKTOP_TEST=1`, which
keeps every window hidden, hides the Dock icon and skips dialogs, so nothing comes to the foreground
while you work; screenshots come from `webContents.capturePage`. Flags:

| Flag | Effect |
|---|---|
| `--app <path to Forge.app>` | Test a built app instead of dev mode |
| `--tunnel` | Also start a public link and call the server through it |
| `--no-docker` | Simulate missing Docker (`FORGE_DESKTOP_SIMULATE_NO_DOCKER=1`) and capture that screen |
| `--image <tag>` | Use a throwaway sandbox image tag to exercise the first-run build |
| `--out <dir>` | Where screenshots and `results.json` go (default `build/verify`) |

It checks Docker and the server start, saves a dummy key and confirms it is stored encrypted,
creates an agent on `fake:hello`, runs it in a Docker sandbox, takes screenshots, quits, and fails if
any server or cloudflared process is left behind.

## Limits

Apple Silicon only (no Intel or universal build), macOS only, no auto update, no Developer ID
signing or notarization. Docker Desktop must be installed separately. Local models must run on the
host; the app does not bundle Ollama. Public links last only while the app is open and get a new
address each time.
