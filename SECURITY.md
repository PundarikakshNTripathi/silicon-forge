# Security Policy

## Reporting a vulnerability
Please **do not open a public issue**. Use GitHub's private
[security advisory form](https://github.com/PundarikakshNTripathi/silicon-forge/security/advisories/new).
You can expect an acknowledgement within a few days.

## Scope notes
- SiliconForge executes EDA tools and, optionally, local model servers and provider CLIs on your machine.
  Generated RTL/Tcl is validated by guardrail allowlists, but treat generated artifacts as untrusted input.
- API keys are stored in the OS keychain (never in config files). The subscription CLI bridge is off by default
  and never reads or stores provider OAuth tokens.
- The local API binds to `127.0.0.1` with a per-launch token when run by the desktop app.
