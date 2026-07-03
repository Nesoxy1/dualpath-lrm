"""LocalResourceManager core - track a folder baseline, emit diff-only packages.

DualPath analogy:
  hit_tokens  = unchanged files the agent already saw -> NOT re-sent (cache hit)
  miss_tokens = changed lines only -> the only thing that crosses the wire
"""
import difflib
import hashlib
import json
import os
import re
import shutil

BASELINE_MARKER = "lrm-baseline-sha256:"

IGNORE_DIRS = {".git", ".lrm", "node_modules", "__pycache__", ".venv", "venv",
               "dist", "build", ".next", "target", ".idea", ".vscode"}
IGNORE_FILES = {"lrm_package.md"}
MAX_FILE_BYTES = 1_000_000


def est_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _is_text(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            return b"\0" not in f.read(8192)
    except OSError:
        return False


def _walk_files(root: str):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS]
        for name in filenames:
            if name in IGNORE_FILES:
                continue
            full = os.path.join(dirpath, name)
            try:
                if os.path.getsize(full) > MAX_FILE_BYTES or not _is_text(full):
                    continue
            except OSError:
                continue
            yield os.path.relpath(full, root).replace(os.sep, "/")


def _file_hash(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _read(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


class LocalResourceManager:
    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        self.lrm_dir = os.path.join(self.root, ".lrm")
        self.state_file = os.path.join(self.lrm_dir, "state.json")
        self.base_dir = os.path.join(self.lrm_dir, "base")

    def _load_state(self) -> dict:
        if os.path.exists(self.state_file):
            with open(self.state_file, encoding="utf-8") as f:
                return json.load(f)
        return {}

    def scan(self) -> str:
        """Snapshot current state as the baseline the agent has seen."""
        if os.path.isdir(self.base_dir):
            shutil.rmtree(self.base_dir)
        state = {}
        for rel in _walk_files(self.root):
            full = os.path.join(self.root, rel)
            state[rel] = _file_hash(full)
            dst = os.path.join(self.base_dir, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(full, dst)
        os.makedirs(self.lrm_dir, exist_ok=True)
        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=1, sort_keys=True)  # deterministic -> stable signature
        return (f"Baseline set: {len(state)} files tracked in {self.root}. "
                f"Baseline file: {self.state_file} (sha256={self.state_hash()[:16]}...). "
                f"If a signing tool is available (e.g. verifikator sign_artifact), "
                f"sign the baseline file NOW so future packages can be trusted.")

    def state_hash(self) -> str:
        """SHA-256 of the baseline file. Packages are bound to this value."""
        return _file_hash(self.state_file)

    def diff(self):
        """Classify files vs baseline -> (unchanged, modified, new, deleted)."""
        old = self._load_state()
        current = {rel: _file_hash(os.path.join(self.root, rel))
                   for rel in _walk_files(self.root)}
        unchanged = [r for r in current if old.get(r) == current[r]]
        modified = [r for r in current if r in old and old[r] != current[r]]
        new = [r for r in current if r not in old]
        deleted = [r for r in old if r not in current]
        return unchanged, modified, new, deleted

    def build_package(self):
        """Return (stats_line, package_body). Empty body = nothing changed."""
        if not self._load_state():
            raise RuntimeError("No baseline - run scan first")
        unchanged, modified, new, deleted = self.diff()

        hit_tok = sum(est_tokens(_read(os.path.join(self.root, r))) for r in unchanged)
        parts = []
        for rel in modified:
            old_lines = _read(os.path.join(self.base_dir, rel)).splitlines(keepends=True)
            new_lines = _read(os.path.join(self.root, rel)).splitlines(keepends=True)
            d = "".join(difflib.unified_diff(old_lines, new_lines,
                                             fromfile=f"a/{rel}", tofile=f"b/{rel}", n=3))
            parts.append(f"### Modified: `{rel}` (apply this diff)\n```diff\n{d}```\n")
        for rel in new:
            parts.append(f"### New file: `{rel}`\n```\n{_read(os.path.join(self.root, rel))}```\n")
        if deleted:
            parts.append("### Deleted files\n" + "\n".join(f"- `{r}`" for r in deleted) + "\n")

        body = "\n".join(parts)
        if parts:
            # Bind the package to the exact baseline it was diffed against.
            body = f"{BASELINE_MARKER} {self.state_hash()}\n\n{body}"
        miss_tok = est_tokens(body) if parts else 0
        total = hit_tok + miss_tok
        stats = (f"unchanged: {len(unchanged)} files (~{hit_tok:,} tok cache-hit, not sent) | "
                 f"modified: {len(modified)}, new: {len(new)}, deleted: {len(deleted)} | "
                 f"package: ~{miss_tok:,} tok "
                 f"({100 * miss_tok // total if total else 0}% of full re-send)")
        return stats, body

    def validate_package(self, package_text: str) -> str:
        """Check that a package is bound to the CURRENT baseline of this folder."""
        m = re.search(rf"{re.escape(BASELINE_MARKER)}\s*([0-9a-f]{{64}})", package_text)
        if not m:
            return ("REJECT: package has no baseline binding "
                    f"('{BASELINE_MARKER} <sha256>' marker missing). "
                    "Do not apply - it may have been produced by an untrusted tool.")
        if not os.path.exists(self.state_file):
            return "REJECT: no local baseline (.lrm/state.json) - run scan first."
        declared, actual = m.group(1), self.state_hash()
        if declared != actual:
            return (f"REJECT: baseline mismatch. Package was diffed against "
                    f"sha256={declared[:16]}..., local baseline is "
                    f"sha256={actual[:16]}.... Either the baseline was tampered with "
                    "or the package is stale. Do not apply.")
        return (f"OK: package is bound to the current baseline "
                f"(sha256={actual[:16]}...). For cryptographic trust, also run "
                "verify_artifact on the baseline file if it was signed.")
