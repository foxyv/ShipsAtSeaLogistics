"""Checks the mod's Lua scripts: luacheck, a syntax check, and the count of top-level local variables.

    python tools/check_lua.py

luacheck is looked for in $LUACHECK, then in ../.tools/luacheck/luacheck.exe beside this repository, then
on the PATH. Its settings are in .luacheckrc. A Lua file may hold only 200 local variables at its top
level and none of the checkers say so, so this counts them: a warning from 170, an error from 195.

The syntax check needs the lupa package (pip install lupa); it is skipped without it.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WARN_LOCALS, FAIL_LOCALS = 170, 195


def lua_files() -> list[Path]:
    skip = {"dist", "node_modules", ".git"}
    return sorted(p for p in ROOT.rglob("*.lua") if not skip & set(p.relative_to(ROOT).parts))


def find_luacheck() -> str | None:
    for candidate in (os.environ.get("LUACHECK"), str(ROOT.parent / ".tools" / "luacheck" / "luacheck.exe"),
                      shutil.which("luacheck")):
        if candidate and Path(candidate).exists():
            return candidate
    return None


def top_level_locals(source: str) -> int:
    """Names declared by `local` at the start of a line (the top level of the file)."""
    count = 0
    for line in source.splitlines():
        if line.startswith("local function "):
            count += 1
        elif line.startswith("local "):
            names = re.split(r"[=<]", line[len("local "):], maxsplit=1)[0]
            count += len([n for n in names.split(",") if n.strip()])
    return count


def main() -> int:
    files = lua_files()
    if not files:
        print("no Lua files found")
        return 1
    failed = False

    luacheck = find_luacheck()
    if luacheck:
        result = subprocess.run([luacheck, "--no-color", *[str(f.relative_to(ROOT)) for f in files]], cwd=ROOT)
        failed |= result.returncode != 0
    else:
        print("luacheck not found: set LUACHECK, or put luacheck.exe in ../.tools/luacheck/ "
              "(https://github.com/lunarmodules/luacheck/releases)")
        failed = True

    try:
        from lupa import LuaRuntime
        load = LuaRuntime(unpack_returned_tuples=True).eval(
            "function(s) local chunk, message = load(s); return chunk ~= nil, message end")
    except ImportError:
        load = None
        print("syntax check skipped: pip install lupa")
    for path in files:
        source = path.read_text(encoding="utf-8")
        if load is not None:
            valid, message = load(source)
            if not valid:
                print(f"{path.relative_to(ROOT)}: syntax error: {message}")
                failed = True
        count = top_level_locals(source)
        if count >= FAIL_LOCALS:
            print(f"{path.relative_to(ROOT)}: {count} top-level locals; Lua allows 200 and the script will not load past that")
            failed = True
        elif count >= WARN_LOCALS:
            print(f"{path.relative_to(ROOT)}: warning, {count} top-level locals of the 200 Lua allows")

    print("FAILED" if failed else "all checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
