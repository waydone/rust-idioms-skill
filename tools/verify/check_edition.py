"""Fail if a skill ships a manifest example that is not edition 2024.

usage: python3 -I check_edition.py <skill_dir> [<Cargo.toml> ...]

Checks (a) every ```toml block that declares a [package] or [workspace.package] table and
(b) every real Cargo.toml passed on the command line: each must say edition = "2024".
An `edition = "<other>"` line anywhere in a toml block is also an error, unless the same
line carries the marker `# existing project` (examples about matching an old project).
"""
import pathlib
import re
import sys

EDITION = re.compile(r'^\s*edition\s*=\s*"(\d{4})"(.*)$', re.M)
BLOCK = re.compile(r"```toml\n(.*?)```", re.S)
errors, checked = [], 0


def check(text: str, where: str, must_declare: bool) -> None:
    global checked
    found = EDITION.findall(text)
    for year, rest in found:
        if year != "2024" and "# existing project" not in rest:
            errors.append(f'{where}: edition = "{year}" (new manifests must be 2024)')
    if must_declare:
        checked += 1
        if not found:
            errors.append(f"{where}: [package] without an edition (defaults to 2015)")


for md in sorted(pathlib.Path(sys.argv[1]).rglob("*.md")):
    text = md.read_text(encoding="utf-8")
    for m in BLOCK.finditer(text):
        line = text.count("\n", 0, m.start()) + 1
        body = m.group(1)
        declares = re.search(r"^\[(workspace\.)?package\]", body, re.M) is not None
        inherits = re.search(r"^\s*edition\.workspace\s*=\s*true", body, re.M) is not None
        check(body, f"{md.name}:{line}", declares and not inherits)
for manifest in sys.argv[2:]:
    check(pathlib.Path(manifest).read_text(encoding="utf-8"), manifest, True)

print(f"edition check: {checked} manifests, {len(errors)} problems")
for e in errors:
    print("  " + e)
sys.exit(1 if errors else 0)
