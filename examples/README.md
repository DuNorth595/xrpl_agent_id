# Examples

Three runnable scripts. Each one prints what it did and what you can do next.

| Script | What it shows | Needs a funded wallet? |
|---|---|---|
| `01_create_did.py` | Derive a `did:xrpl` and resolve it | No |
| `02_publish_did_set.py` | Publish a DIDSet, then resolve the DID | Yes |
| `03_authorization_decision.py` | Allow/deny decisions with reason codes | No |

## Run them

```bash
# not on PyPI yet -- install from GitHub
pip install "xrpl-agent-id @ git+https://github.com/DuNorth595/xrpl_agent_id.git@v0.5.1"
python examples/01_create_did.py
```

`01` and `03` run immediately against XRPL testnet with no setup at all.

`02` submits a real transaction, so it needs a funded testnet wallet:

```bash
# Option A: fund a generated address at
#   https://testnet.xrpl.org/fund_genesis_account/
#   then export the seed:
export XRPL_EXAMPLE_SEED=yourTestnetSeed...

# Option B: let the script pick a wallet and fund it yourself
python examples/02_publish_did_set.py
```

## The shape of the library

```python
from xrpl_agent_id import did_from_account, resolve_did, TrustRegistry, TrustPolicy

did  = did_from_account(address, network="testnet")   # derive
doc  = resolve_did(did, network="testnet")            # read from the ledger

registry = TrustRegistry(network="testnet")
policy   = TrustPolicy(issuers={...}, credential_type=b"https://...")
result   = registry.check(agent_did=did, policy=policy)  # allow / deny + reason
```

## Notes

- Everything here runs on **testnet**. Mainnet is the same code with
  `network="mainnet"` and a funded wallet -- see `docs/MAINNET_DECISION.md`
  for the validated mainnet results.
- The DID Document in example 2 is fetched from the ledger by anyone.
  There is no server of ours in the path.
- Authorization decisions return a **reason code**, not just a boolean.
  That string is what an audit log records.
