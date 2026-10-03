# Contributing

Open an issue describing the behavior and the public evidence involved. Do not include private keys, approval links, credentials or real setup directories. Report vulnerabilities privately using SECURITY.md.

Use Python 3.10 or later and install requirements-release.txt with `--require-hashes --only-binary=:all:` in an isolated environment. Run the CLI tests with `PYTHONPATH=src python -m unittest discover -s tests -v`. The starter's README contains its separately locked runner installation and behavioral tests. Use fixtures for transactions and execution.

Change the canonical bundled template in src/twon20/templates/hyperliquid-python. Do not add a second generated project to this repository. Keep its tests, command examples and agent guide aligned. Generated user projects retain their recorded template version; updates require review of the user's changes.

Dependency changes require exact versions, registry file digests, license review and upload-date evidence in the relevant dependency-review.json. Apply a 14-day minimum age to new third-party versions and files. Avoid broad dependency upgrades or arbitrary remote installers.

Before a pull request, run focused tests, build both archives and inspect their contents. Explain the resulting behavior, validation and material limitations. Include regression cases for recovery, ambiguous network responses and unavailable evidence when affected. Keep secret material out of stdout, exceptions, logs and Git.
