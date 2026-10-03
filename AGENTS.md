# Working on creator tools

This repository is the sole maintained source of the `2n20` distribution (`twon20` import), its portable `2n20-setup` skill and Hyperliquid Python starter. Read README.md, docs/releases.md and the affected protocol code before changing behavior.

Keep discovery, cryptography, key persistence and recovery inside the CLI. The skill is a guide to that interface. The canonical starter source is src/twon20/templates/hyperliquid-python; do not maintain another template copy. Keep CLI commands, template documentation and skill instructions consistent.

Never inspect or print real private keys, wallet seeds, `.env` files, `config.json`, `notes.txt` or credential files. The intended CLI/runtime may perform local cryptography internally. Use disposable fixtures in tests. Ignore generated keys, state and logs before their creation. Never commit them or include them in distributions. Sanitize errors and structured output.

Setup, status, configuration, template generation and skill export must submit no orders, collateral transfers or wallet transactions. Paper is the default. A live execution plan needs explicit separate authorization and concrete limits. Do not enable a service, restart existing trading software, choose a budget or inspect unrelated strategies. Do not claim access/readiness when authoritative evidence is unavailable.

Preserve supported setup-directory formats and package identity. Existing keys must survive retries and interruptions. Never change a published artifact or move a released tag. Use exactly pinned reviewed dependencies and hash locks. Newly selected third-party versions and registry files must be at least 14 days old before installation. Verify official documentation and actual selected SDK APIs. Keep execution dependencies out of the base CLI dependency graph.

Run focused behavioral tests and the installed-artifact checks in docs/releases.md. Mock execution and use public or disposable fixtures; never place a real order as a test. Keep public examples generic. Do not publish private application assets, history, operator runbooks or infrastructure details.
