# Licensing

SiliconForge is licensed under **AGPL-3.0-or-later** ([LICENSE](../LICENSE)). Releases up to 0.3.0 were published under
Apache-2.0; those copies stay Apache-2.0. A commercial license for the current code is available
([COMMERCIAL.md](../COMMERCIAL.md)).

## What this means in practice

| You do this | Obligation |
| --- | --- |
| Run SiliconForge on your own or your company's designs | None. |
| Keep your RTL, constraints, programs, GDS and reports | They are yours. The AGPL covers SiliconForge, not what you feed it. |
| Modify SiliconForge and use it internally | None beyond keeping the license on what you distribute. |
| Distribute a modified copy | Provide the source of your version under AGPL-3.0-or-later. |
| Let others use a modified version over a network (hosted cockpit, API) | Offer those users the source of your version (AGPL section 13). The cockpit footer links to the source; a fork sets `VITE_SOURCE_URL` when it builds the UI. |
| Embed SiliconForge in a closed product | Needs the commercial license. |

## Generated files

- **Tiny Tapeout project files** written by `siliconforge tt scaffold` carry `SPDX-License-Identifier: Apache-2.0`, matching the
  Tiny Tapeout templates. They are your project's files.
- **RTL, documentation and SVGs generated from your `isa.yaml`** (`isa gen`) are derived from your specification and are yours.
- **The standalone ISA bundle** (`gen/tools/<name>_isa.py`) is the exception: it embeds SiliconForge's assembler and
  golden-model code, so it is AGPL-3.0-or-later software. If you ship the bundle with your project, ship it under those
  terms, or take the commercial license.
- **Verification helpers** copied into a workspace (`.siliconforge/tools/sf_*.py`) are SiliconForge code under the same license.

## Models and external tools

- Model weights (Qwen, Gemini access through your own subscription, and so on) keep their own licenses and terms. None are in the repository.
- Yosys, SymbiYosys, Verilator, OpenROAD, LibreLane, KLayout, cocotb, llama.cpp and the like run as separate programs and keep
  their own licenses. `THIRD_PARTY_NOTICES.md` lists the direct Python and JavaScript dependencies and their licenses, and
  `python scripts/notices.py --check` fails if one is not compatible with the AGPL.

## Contributing

Contributions are accepted under AGPL-3.0-or-later plus a grant that lets the maintainer also offer them commercially
([CONTRIBUTING.md](../CONTRIBUTING.md)). That grant text is a working draft; get legal review before relying on it
for a commercial offering.
