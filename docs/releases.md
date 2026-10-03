# Release procedure

The maintained source is 2n20/creator-tools. Distribution `2n20`, import `twon20` and executable `2n20` retain their identity. Old published artifacts and tags are immutable. The portable skill and canonical Hyperliquid template ship inside both wheel and sdist. Execution dependencies belong to the template's separate requirements-runner.txt.

## Verification

Review dependencies and their licenses before changing either hash lock. Record exact registry file SHA-256 digests and upload timestamps in dependency-review.json. New third-party versions and files must be at least 14 days old. Use local built artifacts during development.

```sh
python3 -m venv .venv
.venv/bin/python -m pip --isolated install --require-hashes --only-binary=:all: -r requirements-release.txt
.venv/bin/python -m pip --isolated install --require-hashes --only-binary=:all: -r src/twon20/templates/hyperliquid-python/requirements-runner.txt
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
cd src/twon20/templates/hyperliquid-python
PYTHONPATH=../../.. ../../../../.venv/bin/python -m unittest discover -s tests -v
cd ../../../..
SOURCE_DATE_EPOCH=1790985600 .venv/bin/python scripts/build_release.py
.venv/bin/python -m pip --isolated install --no-deps dist/*.whl
.venv/bin/python scripts/check_release.py
```

Build twice with the same SOURCE_DATE_EPOCH and compare hashes. Install the sdist separately outside the checkout. Verify consent interoperability, existing setup-directory resume, failure recovery, explicit skill export, template completeness, generation overwrite refusal, deterministic paper and read-only diagnostics. Execution tests use mocks/disposable fixtures only. Inspect archive and staged Git paths for secrets, generated state/logs and unrelated material. Do not contact a real wallet or send orders/transfers as a test.

## Trusted Publishing

Configure an existing-project GitHub publisher on PyPI, without an API token:

| Setting | Exact value |
| --- | --- |
| PyPI project | `2n20` |
| GitHub owner | `2n20` |
| Repository | `creator-tools` |
| Workflow filename | `creator-cli-publish.yml` |
| Environment | `creator-cli-pypi` |

The GitHub environment accepts only release tags matching `creator-cli-v*`. Increment the package version, complete review and tests, commit, push and create a new matching immutable tag. Dispatch the workflow against that tag:

```sh
gh workflow run creator-cli-publish.yml --repo 2n20/creator-tools --ref creator-cli-v<VERSION>
```

The SHA-pinned workflow builds/tests artifacts, then gives OIDC permission only to the dedicated publishing job. Verify PyPI's actual version, downloaded file hashes and attestations. Publisher identity must name this repository, workflow, environment, release tag and tested source commit. Attach those exact verified files to the matching GitHub release.

Only then update website installation instructions to the published version and deploy through the application's established process. Retire an obsolete publisher only after the new release is verified. Do not move older tags or delete historical release evidence. TestPyPI is optional and was not used for the initial public-source transition. Record skipped checks separately from passing verification.
