# Security Policy

## Supported Versions

Hive is currently in **Step 1 (Python meta-package)**. Security fixes are
issued for the latest released minor version and the immediately preceding
one. Earlier versions are best-effort.

| Version | Supported           |
|---------|---------------------|
| 0.2.x   | :white_check_mark:  |
| 0.1.x   | :white_check_mark:  |
| < 0.1   | :x:                 |

## Reporting a Vulnerability

Please **do not** open a public GitHub issue for security-sensitive bugs.
Send a report to **djlougen+hive-security [at] gmail.com** with:

* a description of the vulnerability and the impact you observe,
* a minimal reproducer (transcript, command, or test case),
* the version of `hive` you are running,
* the version of the sibling packages (`busybee-cpu`, `honeycomb`) and
  Python you are running.

You can expect an acknowledgement within 72 hours. We aim to ship a fix
within 14 days for critical issues and 30 days for moderate ones. The
reporter is credited in the CHANGELOG unless they ask to remain anonymous.

## Scope

The Hive meta-package itself is a thin orchestrator. The main attack
surfaces are:

* `hive.llm` — outbound HTTP to vLLM / llama.cpp servers. Sanity-check
  endpoints and never log full message bodies.
* `hive.rust_brain` — monotonic-timestamp guard rejects replays of
  older writes. The trust score is the user-controlled input; do not
  treat high-trust nodes as authoritative in a multi-tenant setting.
* `hive.hardware` — pynvml is read-only; no attack surface.

Vulnerabilities in the sibling packages (`busybee-cpu`, `honeycomb`) are
out of scope; please report them upstream.
