#!/usr/bin/env bash
# Re-verify rust-idioms on the active toolchain (rustc --version).
#   1. regenerate src/blocks/ from every ```rust block of skills/rust-idioms
#      (fragments / proc-macro listed in harness.json are skipped)
#   2. cargo check --all-targets — must report 0 errors, 0 warnings (no deprecated API)
#   3. cargo test --test new_on_stable — the "new on stable" APIs SKILL.md recommends
# usage: tools/verify/check.sh            (from anywhere)
set -uo pipefail
here="$(cd -- "$(dirname -- "$0")" && pwd)"
skill="$here/../../skills/rust-idioms"
manifest="$here/Cargo.toml"

echo "toolchain: $(rustc --version)"
python3 -I "$here/check_edition.py" "$skill" "$manifest" "$here/hello-derive/Cargo.toml" || exit 1
python3 -I "$here/gen_harness.py" "$skill" "$here/src" "$here/harness.json" || exit 1
python3 -I "$here/gen_derive.py" "$skill" "$here/hello-derive/src/lib.rs" || exit 1

log="$here/target/check.log"
mkdir -p "$here/target"
cargo check --manifest-path "$manifest" --all-targets --message-format=short 2> "$log" > /dev/null
status=$?
errors=$(grep -c -E '^(src|tests)/.*: error' "$log")
warnings=$(grep -c -E '^(src|tests)/.*: warning' "$log")
echo "cargo check --all-targets: exit=$status errors=$errors warnings=$warnings"
if [ "$errors" -gt 0 ] || [ "$warnings" -gt 0 ]; then
  grep -E '^(src|tests)/.*: (error|warning)' "$log" | head -40
  exit 1
fi

cargo test --manifest-path "$manifest" --test new_on_stable --test derive 2>&1 | grep -E '^test |test result' || exit 1
