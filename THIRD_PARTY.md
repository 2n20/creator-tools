# Dependency attribution

This repository's MIT license covers original 2n20 code. Third-party libraries are installed separately from their reviewed distributions; their original license files and notices remain in those distributions. No Hyperliquid SDK source, private application assets or third-party UI kit is vendored here.

The base CLI uses the pinned eth-account graph. Generated starter projects additionally use [Hyperliquid's official SDK](https://github.com/hyperliquid-dex/hyperliquid-python-sdk), its HTTP/serialization dependencies and the same compatible CLI runtime graph. Versions, registry upload dates and hashes are recorded in requirements-release.txt and the template's requirements-runner.txt/dependency-review.json.

License metadata reviewed for that runner graph:

| License | Packages |
| --- | --- |
| MIT | annotated-types, charset-normalizer, eth-account, eth-hash, eth-keyfile, eth-keys, eth-rlp, eth-typing, eth-utils, eth_abi, hexbytes, hyperliquid-python-sdk, parsimonious, pydantic, pydantic_core, rlp, typing-inspection, urllib3 |
| BSD-3-Clause | cytoolz, idna, toolz |
| PSF-2.0 | bitarray, typing_extensions |
| Apache-2.0 | ckzg, msgpack, requests, websocket-client |
| MPL-2.0 | certifi |
| Apache-2.0 and CNRI-Python | regex |
| BSD and public domain components | pycryptodome |

Build tooling is separately pinned in requirements-release.txt. Review changed dependency licenses and upstream notices with each version change. A dependency's license does not authorize copying another project's credential-handling examples or proprietary assets.
