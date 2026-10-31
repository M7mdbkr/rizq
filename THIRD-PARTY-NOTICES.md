# Third-party notices

The MIT licence in `LICENSE` covers the code in this repository that was written for
Rizq. Components below are redistributed under their own terms.

| Component | Where | Licence |
|---|---|---|
| React, React DOM | compiled into `rizq-pos/assets/index-*.js` | MIT |
| sql.js (SQLite compiled to WebAssembly) | `rizq-pos/sql-wasm.wasm`, bundle | MIT |
| SQLite | inside sql.js | Public domain |
| Tailwind CSS | compiled into `rizq-pos/assets/index-*.css` | MIT |
| SheetJS / xlsx (spreadsheet export) | compiled into the bundle | Apache-2.0 |
| Tajawal font | `rizq-pos/assets/fonts/*.ttf` | SIL Open Font License 1.1 (`OFL.txt`) |

The agent itself (`agent/runtime/`) uses only the Python standard library.

## Models

No model weights are distributed here. The agent calls whatever local model you have
installed through [Ollama](https://ollama.com). Models used while developing:

| Model | Used for | Upstream licence |
|---|---|---|
| `qwen3:8b` | conversation fallback | Apache-2.0 as declared upstream |
| `qwen2.5-coder:7b` | code proposals | Apache-2.0 as declared upstream |

Anthropic's Claude is supported as an optional alternative and requires your own API key.
Verify any model's licence yourself before commercial use.

## Notes

- The "Made in KSA" wordmark in the cashier UI is a custom mark. It is **not** an official
  Saudi Made certification and does not imply one.
- Rizq produces ZATCA-*style* VAT receipts. It is **not** connected to ZATCA FATOORA and
  submits nothing to the authority. See the limitations section of the README.
