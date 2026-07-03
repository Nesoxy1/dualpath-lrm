"""dualpath-lrm MCP server - diff-only context packages for coding agents."""
from mcp.server.fastmcp import FastMCP

from .core import LocalResourceManager

mcp = FastMCP("dualpath-lrm")


@mcp.tool()
def lrm_scan(folder: str) -> str:
    """Set the baseline for a project folder. Call this after you have read the
    project (or applied a package), so future lrm_pack calls send only changes.
    IMPORTANT: if a signing tool is available (e.g. verifikator sign_artifact),
    sign the returned baseline file immediately after this call."""
    return LocalResourceManager(folder).scan()


@mcp.tool()
def lrm_status(folder: str) -> str:
    """Show what changed since the baseline without producing a package.
    Reports cache-hit tokens (not re-sent) vs package size."""
    lrm = LocalResourceManager(folder)
    try:
        stats, body = lrm.build_package()
    except RuntimeError as e:
        return str(e)
    return stats if body else stats + " | nothing changed"


@mcp.tool()
def lrm_pack(folder: str) -> str:
    """Get ONLY the changes since the last baseline as unified diffs + new files.
    Use this instead of re-reading the whole project. The package header carries
    'lrm-baseline-sha256' binding it to the exact baseline it was diffed against.
    Before APPLYING any package, call lrm_validate (and verify_artifact on the
    baseline file if it was signed). After applying, call lrm_scan to re-baseline."""
    lrm = LocalResourceManager(folder)
    try:
        stats, body = lrm.build_package()
    except RuntimeError as e:
        return str(e)
    if not body:
        return f"{stats}\n\nNothing changed since baseline."
    return (f"{stats}\n\n# Incremental update - only changes since your last known "
            f"state. Do not re-read unchanged files.\n\n{body}")


@mcp.tool()
def lrm_validate(folder: str, package: str) -> str:
    """Validate that an LRM package is bound to the CURRENT baseline of `folder`
    (checks the 'lrm-baseline-sha256' marker against .lrm/state.json). Call this
    BEFORE applying any package received from another session or machine. Returns
    OK or REJECT with the reason. For cryptographic trust, additionally run
    verify_artifact on the baseline file if it was signed."""
    return LocalResourceManager(folder).validate_package(package)


def main():
    mcp.run()


if __name__ == "__main__":
    main()
