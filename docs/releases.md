# What is in a release, and what to do with it

Every tagged version on the [Releases page](https://github.com/PundarikakshNTripathi/silicon-forge/releases) carries three files, built by
`.github/workflows/release.yml` on a Windows runner. They are for people who want the cockpit as an ordinary program, with no terminal and no
Python setup. Linux and macOS packaging is configured in `apps/desktop/package.json` but has not been built or tested, so there is no download for them yet.

| File | Size (v0.6.0) | What it is | When to use it |
| :--- | ---: | :--- | :--- |
| `SiliconForge.Setup.<version>.exe` | about 150 MB | The installer (NSIS). It asks where to install, adds a Start-menu entry and an uninstaller. | You want it installed like any other program, and you want updates. |
| `SiliconForge.<version>.exe` | about 136 MB | The portable build: one executable, nothing installed, nothing written outside your user data folder. | You want to try it, run it from a USB stick or a locked-down machine, or delete it by deleting one file. |
| `latest.yml` | under 1 KB | Update information (version, file name, checksum) that an *installed* copy reads to find a newer release. | Nothing. You never open it or download it by hand. |

## Using the app

1. Download one of the two `.exe` files and double-click it. No terminal is involved.
2. Windows SmartScreen will say the publisher is unknown, because the files are **not code-signed** (signing costs money and is a decision still open).
   Click "More info", then "Run anyway". If you would rather not, build it yourself: `python scripts/build_desktop.py`.
3. A window opens. Behind it the app has started its own backend on a random port on your own machine, with a one-time token, and closed itself to
   anything else on the network. Quitting the window stops the backend.
4. **What works straight away:** the bundled example designs with the *simulated* backend. Every result then carries a hatched "simulated" stamp.
   **What needs more:** real results need Yosys, SymbiYosys and OpenROAD, which the installer does not include. Set them up once with
   [eda-setup.md](eda-setup.md) (about 15 minutes), then choose the WSL backend on the New run page. A model is needed for your own specs; see the Models page.
5. Your data (runs, settings, the database) lives in the app's user data folder; the menu has "Open data folder". The log is `siliconforge.log` beside it.

## Updates

An installed copy checks this repository's latest release at start and offers the update (it reads `latest.yml`). The portable file never updates itself:
download the new one. Neither phones home for anything else.

## Checking what you downloaded

Each release page lists the files with their sizes. To compare against what the workflow built, look at the run named in the release's commit on the
Actions tab. The build runs a smoke test (`SiliconForge.exe --smoke-test`) on the unpacked app before it publishes.

## The same GUI without the app

`siliconforge serve` (after `pip install -e ".[api]"` and `pnpm build` in `services/dashboard`) serves the same cockpit at http://127.0.0.1:8000, and a
read-only copy with recorded results is at <https://pundarikakshntripathi.github.io/silicon-forge/>.
