# Security reporting

Use this repository's **Security → Report a vulnerability** private advisory form to report a suspected vulnerability. Include the affected version, a minimal reproduction with synthetic data and the expected boundary. Do not disclose wallet keys, seeds, credentials, real approval links or funds-bearing setup directories.

The current release receives fixes. Older releases remain immutable; upgrade using reviewed release instructions while retaining your setup directory. Key recovery must never overwrite an existing key.

The CLI verifies public deployment/account evidence and retains a local trading key. The approval link is a request locator and requires human wallet authorization. The starter is an educational execution example. Tests and client order identifiers do not establish exactly-once execution, profitability or complete isolation from another process using the same account/key.
