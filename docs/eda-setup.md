# EDA toolchain setup

SiliconForge runs real verification and implementation through one of four backends (`--backend` /
`eda_mode` in `configs/flow_default.yaml`):

| Backend | Where tools run | Supports |
| :--- | :--- | :--- |
| `simulated` | nowhere (surrogate models) | everything, **labelled simulated**; for demos/CI only |
| `native` | on your `PATH` (Linux/macOS) | formal, LEC, lint, P&R |
| `wsl` | inside a WSL2 distro (Windows) | formal, LEC, lint, P&R |
| `docker` | in a container | formal, LEC, lint (P&R: use `librelane --dockerized`; see below) |

Check what is available at any time:

```bash
siliconforge doctor
python scripts/dev.py doctor
```

A missing tool is a hard error (`ToolMissingError`), never a silent fallback to simulated results.

## Windows 11: WSL2 (no root / sudo needed)

This is the path the project was developed and verified on (Ubuntu 24.04 in WSL2, RTX 50-series laptop).

```bash
# 1) OSS CAD Suite: yosys, sby (SymbiYosys), verilator, bitwuzla, z3 ... (~0.8 GB download)
mkdir -p ~/eda && cd ~/eda
curl -fsSL -o oss-cad-suite.tgz \
  https://github.com/YosysHQ/oss-cad-suite-build/releases/download/2026-11-20/oss-cad-suite-linux-x64-20261120.tgz
tar xzf oss-cad-suite.tgz && rm oss-cad-suite.tgz
source ~/eda/oss-cad-suite/environment && yosys -V && sby --help | head -1

# 2) LibreLane (OpenROAD flow) from its self-contained devshell AppImage: no Nix install, no root
mkdir -p ~/eda/librelane && cd ~/eda/librelane
curl -C - -fsSL -o librelane.AppImage \
  https://github.com/librelane/librelane/releases/download/3.0.17/librelane-devshell-x86_64.AppImage
chmod +x librelane.AppImage && ./librelane.AppImage --appimage-extract   # ~5 GB extracted
./squashfs-root/AppRun librelane --smoke-test                            # downloads the Sky130 PDK (~2 GB) on first use
```

Notes:

- Do **not** `source` the OSS CAD Suite environment in the same shell as LibreLane: it exports `VERILATOR_ROOT`, which
  breaks LibreLane's own Verilator. SiliconForge runs LibreLane isolated.
- Newer releases: adjust the URLs (`gh release list -R YosysHQ/oss-cad-suite-build`,
  `gh release list -R librelane/librelane`).
- Run P&R on the distro's own ext4 filesystem for speed; SiliconForge copies each episode to `~/siliconforge-work`
  and back automatically (`eda.wsl.scratch`).
- Paths are configurable under `eda.wsl` in the flow config (`distro`, `env_scripts`, `librelane_cmd`).

## Linux / macOS (native)

Install OSS CAD Suite and LibreLane (Nix or AppImage per the
[LibreLane docs](https://librelane.readthedocs.io/en/latest/installation/index.html)), put the tools on `PATH`, and
use `--backend native`.

## Docker (formal / LEC / lint)

```bash
docker build -f docker/Dockerfile.eda -t siliconforge-eda:latest .
siliconforge run --spec hardware/examples/uart_tx/spec.md --backend docker
```

Place-and-route in Docker uses LibreLane's own image (`pip install librelane` then `librelane --dockerized ...`);
wiring that into the `docker` backend is on the roadmap. The Docker backend is covered by unit tests of its wiring but
has not been exercised end-to-end on the project's reference machine, so treat it as experimental.

## What was verified

`python scripts/verify_examples.py --backend wsl --lec` runs every bundled example through real SymbiYosys (BMC +
PDR proof + cover) and real Yosys LEC: the correct RTL must pass and the injected bug must be caught with a
counterexample trace. `pytest -m real_eda` runs the same checks (auto-skipped when tools are absent).
