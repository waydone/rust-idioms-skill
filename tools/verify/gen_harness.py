"""Turn every ```rust block of a skill into a module of a compilable harness crate.

usage: python3 -I gen_harness.py <skill_dir> <crate_src_dir> <harness.json>

Each block becomes `src/blocks/bNNN.rs` (numbered in SKILL.md, then references/*.md order):
- top-level items stay at module level; loose statements go into `async fn __stmts`
  (returns Result so `?` works; gets req/depot/res/ctrl params when it uses them);
- `/* ... */`-style placeholders become `todo!()` (statement form when alone on a line,
  `..Default::default()` inside a struct literal);
- consecutive bare expressions get `;` inserted before any trailing comment;
- a block ending in a lone attribute gets a target fn appended;
- rustdoc hidden lines (`# ...`) are un-hidden;
- `value_placeholders` names the snippet uses but never binds get a `let` in `__stmts`.

harness.json keys (all optional):
  preamble                         lines added to every module after `use ...common::*;`
  skip_if_contains                 skip a block whose text contains any of these strings
  skip_if_starts_with              skip a block whose first code line starts with any of these
  merge_into_previous_if_contains  append a block to the previous one (docs that continue a block)
  literal_subst                    exact text replacements applied before parsing
  value_placeholders               {name: rust expression}
"""
import json
import pathlib
import re
import sys

skill_root = pathlib.Path(sys.argv[1])
crate_src = pathlib.Path(sys.argv[2])
cfg = json.loads(pathlib.Path(sys.argv[3]).read_text(encoding="utf-8"))

PREAMBLE = cfg.get("preamble", [])
SKIP_CONTAINS = cfg.get("skip_if_contains", [])
SKIP_STARTS = tuple(cfg.get("skip_if_starts_with", []))
MERGE_CONTAINS = cfg.get("merge_into_previous_if_contains", [])
LITERAL_SUBST = cfg.get("literal_subst", {})
VALUE_PH = cfg.get("value_placeholders", {})

ITEM_START = re.compile(
    r"^(use |pub |fn |async fn |#\[|#!\[|struct |enum |impl|const |static |mod |type |trait |extern |macro_rules!"
    r"|[a-z_][a-z0-9_]*!\s*\{)"  # brace-form macro at column 0 = item position (proptest! { .. }, thread_local! { .. })
)
PH = r"/\*\s*(\.\.\.|routes|api routes|real routes|)\s*\*/"
PH_STRUCT = re.compile(r"(?<!-> )\b([A-Z]\w*)\s*\{\s*" + PH + r"\s*\}")
PH_ALONE = re.compile(r"^(\s*)" + PH + r"\s*$")
PH_ANY = re.compile(PH)
OPEN_ENDINGS = (";", "{", ",", "(", "[", "=", ".", "|", "&&", "||", "+", "=>", "->")
CTX_PARAMS = {"req": "&mut Request", "depot": "&mut Depot", "res": "&mut Response", "ctrl": "&mut FlowCtrl"}


def code_part(line: str) -> str:
    """Line without its trailing // comment (string-literal aware)."""
    in_str, i = False, 0
    while i < len(line):
        c = line[i]
        if c == "\\" and in_str:
            i += 2
            continue
        if c == '"':
            in_str = not in_str
        elif not in_str and line.startswith("//", i):
            return line[:i].rstrip()
        i += 1
    return line.rstrip()


def terminate(prev_lines: list) -> None:
    """Append `;` to the last code line of the previous statement if it is a bare expression."""
    for idx in range(len(prev_lines) - 1, -1, -1):
        raw = prev_lines[idx]
        code = code_part(raw)
        if code.strip() == "" or code.strip().startswith("//"):
            continue
        if not code.endswith(OPEN_ENDINGS):
            prev_lines[idx] = code + ";" + raw[len(code):]
        return


def first_code_line(body: list) -> str:
    return next((l.strip() for l in body if l.strip() and not l.strip().startswith("//")), "")


# 1. collect blocks
files = [skill_root / "SKILL.md", *sorted((skill_root / "references").glob("*.md"))]
blocks = []
for f in files:
    if not f.exists():
        continue
    inside, start, buf = False, 0, []
    for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
        s = line.strip()
        if not inside and s.startswith("```rust"):
            inside, start, buf = True, i, []
        elif inside and s == "```":
            inside = False
            blocks.append([f.relative_to(skill_root), start, buf, "keep"])
        elif inside:
            buf.append(line)

# 2. classify: skip / merge
for n, blk in enumerate(blocks):
    text = "\n".join(blk[2])
    for k, v in LITERAL_SUBST.items():  # classify on the substituted text
        text = text.replace(k, v)
    if any(s in text for s in SKIP_CONTAINS) or first_code_line(blk[2]).startswith(SKIP_STARTS):
        blk[3] = "skip"
    elif n > 0 and any(s in text for s in MERGE_CONTAINS):
        prev = blocks[n - 1]
        dup = lambda l: "// (dup import) " + l if l.startswith("use serde::") else l  # noqa: E731
        prev[2] = prev[2] + ["", f"// --- merged from {blk[0]}:{blk[1]}"] + [dup(l) for l in blk[2]]
        blk[3] = "merged"

# 3. emit modules
out_dir = crate_src / "blocks"
out_dir.mkdir(parents=True, exist_ok=True)
for old in out_dir.glob("b*.rs"):
    old.unlink()
mods, counts = [], {"keep": 0, "skip": 0, "merged": 0}
for n, (rel, start, body, state) in enumerate(blocks, 1):
    name = f"b{n:03}"
    mods.append(name)
    counts[state] += 1
    header = f"// {rel}:{start}\n"
    if state != "keep":
        (out_dir / f"{name}.rs").write_text(header + f"// {state.upper()}\n")
        continue
    items, stmts, mode, pending = [], [], "item", []
    for raw in body:
        if raw.startswith("# ") or raw == "#":  # rustdoc hidden line
            raw = raw[2:]
        for k, v in LITERAL_SUBST.items():
            raw = raw.replace(k, v)
        line = PH_STRUCT.sub(r"\1 { ..Default::default() }", raw)
        line = PH_ALONE.sub(r"\1todo!();", line)
        line = PH_ANY.sub("todo!()", line)
        if line.strip() == "":
            (items if mode == "item" else stmts).append(line)
            continue
        col0 = not line[0].isspace()
        if col0 and line.startswith("//"):
            pending.append(line)
            continue
        if col0 and not line.startswith(("}", ")", "]", ".", "?")):
            new_mode = "item" if ITEM_START.match(line) else "stmt"
            if new_mode == "stmt" and mode == "stmt":
                terminate(stmts)
            mode = new_mode
        target = items if mode == "item" else stmts
        target.extend(pending)
        pending = []
        target.append(line)
    items.extend(pending)
    last = [code_part(l) for l in items if code_part(l).strip() and not l.strip().startswith("//")]
    if last and last[-1].strip().endswith(")]"):
        items.append("async fn __attr_target() -> Json<User> { todo!() }")

    text = [header, "use super::super::common::*;", *PREAMBLE, ""]
    text.extend(items)
    stmt_src = "\n".join(stmts)
    if stmt_src.strip():
        words = set(re.findall(r"\b[a-z_][a-z0-9_]*\b", stmt_src))
        defined = set(re.findall(r"\blet\s+(?:mut\s+)?([a-z_][a-z0-9_]*)", stmt_src))
        params = [f"{p}: {t}" for p, t in CTX_PARAMS.items() if p in words and p not in defined]
        text.append("")
        text.append(f"pub async fn __stmts({', '.join(params)}) -> Result<(), Box<dyn std::error::Error>> {{")
        for v, expr in VALUE_PH.items():
            if v in words and v not in defined and not re.search(rf"\b{v}\s*\(", stmt_src):
                text.append(f"    let {v} = {expr};")
        text.extend("    " + s for s in stmts)
        text.append("    ;")
        text.append("    Ok(())")
        text.append("}")
    (out_dir / f"{name}.rs").write_text("\n".join(text) + "\n")

(crate_src / "blocks.rs").write_text(
    "// generated by gen_harness.py — do not edit\n"
    + "".join(cfg.get("blocks_rs_prelude", []))
    + "".join(f"#[path = \"blocks/{m}.rs\"] pub mod {m};\n" for m in mods)
)
print(f"{len(blocks)} rust blocks: {counts['keep']} compiled, {counts['skip']} skipped, {counts['merged']} merged into the previous block")
