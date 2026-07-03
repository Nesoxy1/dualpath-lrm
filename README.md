# dualpath-lrm

MCP server that gives coding agents **diff-only context**: instead of re-reading a whole
project, the agent gets only what changed since its last known baseline.

Inspired by the DualPath paper's KV-cache reuse philosophy, applied client-side:
*hit tokens* = unchanged files (never re-sent), *miss tokens* = unified diffs (the only
thing that crosses the wire).

## Tools

| Tool | Purpose |
|---|---|
| `lrm_scan(folder)` | Set baseline = "agent has seen this state". Run after reading the project or applying a package. |
| `lrm_status(folder)` | Report what changed and how many tokens a package would cost. Sends nothing. |
| `lrm_pack(folder)` | Return only unified diffs + new files since baseline. Header carries `lrm-baseline-sha256` binding the package to its exact baseline. |
| `lrm_validate(folder, package)` | Check a package's baseline binding against the local `.lrm/state.json` before applying. Rejects tampered baselines and stale/unbound packages. |

Baseline lives in `.lrm/` inside the tracked folder — add it to `.gitignore`.

## Install

```bash
pip install .
```

## Use with Claude Code

```bash
claude mcp add dualpath-lrm -- dualpath-lrm
```

Or in `.mcp.json`:

```json
{
  "mcpServers": {
    "dualpath-lrm": { "command": "dualpath-lrm" }
  }
}
```

Then: ask the agent to `lrm_scan` the project once; on later sessions ask it to
`lrm_pack` instead of re-reading files.

## Standalone (no MCP)

```python
from dualpath_lrm import LocalResourceManager
lrm = LocalResourceManager("path/to/project")
lrm.scan()
stats, package = lrm.build_package()
```

## Works great with: verifikator (https://github.com/Nesoxy1/verifikator-mcp)

Diff-only context is only as trustworthy as its baseline. If `.lrm/state.json` is
tampered with, the agent applies diffs against a state it never saw. Two layers:

1. **Built in:** every package carries `lrm-baseline-sha256`; `lrm_validate`
   rejects packages whose binding doesn't match the local baseline.
2. **Cryptographic (verifikator):** after `lrm_scan`, sign `.lrm/state.json`
   (`sign_artifact`); before applying any package, verify it (`verify_artifact`).
   Layer 1 proves package↔baseline consistency; layer 2 proves the baseline
   itself is the one you signed.

## Limits

- Text files only, ≤1 MB each; `node_modules`, `.git`, build dirs etc. are ignored.
- This reduces **new input tokens**. It cannot bypass server-side prefill of
  conversation history — session hygiene still matters.

MIT license.
