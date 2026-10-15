#!/usr/bin/env python3
"""
Python Disconnected (pyd)
=========================

One language, split in two:

  BLUE PYTHON   - basic programming only: math, variables, if/else, loops,
                  functions, lists/dicts, print. No imports, no files, no
                  network, no text managing (split, replace, strip, ...).

  YELLOW PYTHON - full Python: APIs, files, text managing, imports, anything
                  hard. It hands values to Blue with  give("name", value)
                  and MUST end by running Blue with  blue()  as its very last
                  line. If it doesn't, nothing it gave is registered and
                  Blue never runs.

A .pyd file has two sections, Yellow first, Blue last:

    :: yellow ::
    import json
    data = json.loads('{"a": 2, "b": 3}')
    give("a", data["a"])
    give("b", data["b"])
    blue()

    :: blue ::
    print(a + b)

A .blue file is a Blue-only program (nothing is given to it).

Usage:
    python3 pyd.py run   program.pyd     run a program
    python3 pyd.py check program.pyd     check the rules without running
    python3 pyd.py new   name            create name.pyd from a template
"""

import ast
import re
import sys
import os

VERSION = "1.0"

# ----------------------------------------------------------------- colours ---
_TTY = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
def _c(code, s): return f"\033[{code}m{s}\033[0m" if _TTY else s
BLUE_TAG   = _c("1;34", "[blue]")
YELLOW_TAG = _c("1;33", "[yellow]")
PYD_TAG    = _c("1;35", "[pyd]")


class PydError(Exception):
    """A Python Disconnected rule was broken."""
    def __init__(self, side, msg, line=None, src_name=None):
        self.side, self.msg, self.line, self.src_name = side, msg, line, src_name
        super().__init__(msg)

    def __str__(self):
        tag = {"blue": BLUE_TAG, "yellow": YELLOW_TAG}.get(self.side, PYD_TAG)
        where = ""
        if self.src_name:
            where = f" {self.src_name}" + (f":{self.line}" if self.line else "")
        return f"{tag}{where}  {self.msg}"


# =============================================================== BLUE RULES ===

# Builtins Blue is allowed to use: basic programming only.
BLUE_BUILTINS = {
    name: getattr(__builtins__, name) if not isinstance(__builtins__, dict)
    else __builtins__[name]
    for name in [
        "print", "range", "len", "int", "float", "str", "bool", "list",
        "dict", "set", "tuple", "abs", "min", "max", "sum", "round",
        "sorted", "reversed", "enumerate", "zip", "map", "filter", "any",
        "all", "isinstance", "divmod", "pow", "True", "False", "None",
        "Exception", "ValueError", "TypeError", "KeyError", "IndexError",
        "ZeroDivisionError", "StopIteration", "__build_class__",
    ]
}

# Names Blue may never touch (they reach outside the program).
BLUE_FORBIDDEN_NAMES = {
    "open", "input", "eval", "exec", "compile", "__import__", "globals",
    "locals", "vars", "getattr", "setattr", "delattr", "breakpoint", "help",
    "memoryview", "bytes", "bytearray", "chr", "ord", "format", "repr",
    "ascii", "hash", "id", "type", "object", "super", "exit", "quit",
}

# Text-managing methods: that's Yellow's job.
BLUE_TEXT_METHODS = {
    "split", "rsplit", "splitlines", "join", "replace", "strip", "lstrip",
    "rstrip", "lower", "upper", "title", "capitalize", "casefold",
    "swapcase", "find", "rfind", "startswith", "endswith",
    "format", "format_map", "encode", "decode", "center", "ljust", "rjust",
    "zfill", "partition", "rpartition", "translate", "maketrans",
    "expandtabs", "isalpha", "isdigit", "isnumeric", "isalnum", "isspace",
    "isupper", "islower", "istitle", "removeprefix", "removesuffix",
}


class BlueChecker(ast.NodeVisitor):
    def __init__(self, src_name):
        self.src_name = src_name

    def fail(self, node, msg):
        raise PydError("blue", msg, getattr(node, "lineno", None), self.src_name)

    def visit_Import(self, node):
        self.fail(node, "Blue can't import anything. Do it in Yellow and give() the result.")
    visit_ImportFrom = visit_Import

    def visit_Name(self, node):
        if node.id in BLUE_FORBIDDEN_NAMES:
            self.fail(node, f"'{node.id}' is not basic programming. Move it to Yellow.")
        if node.id in ("give", "blue"):
            self.fail(node, f"'{node.id}()' only exists in Yellow.")
        self.generic_visit(node)

    def visit_Attribute(self, node):
        if node.attr.startswith("_"):
            self.fail(node, f"Blue can't use hidden attributes ('{node.attr}').")
        if node.attr in BLUE_TEXT_METHODS:
            self.fail(node, f"'.{node.attr}()' is text managing - that's Yellow's job. "
                            f"Do it in Yellow and give() the result.")
        self.generic_visit(node)

    def visit_With(self, node):
        self.fail(node, "'with' blocks are for files/resources - use Yellow.")
    visit_AsyncWith = visit_With

    def visit_AsyncFunctionDef(self, node):
        self.fail(node, "async code belongs in Yellow.")
    visit_Await = visit_AsyncFunctionDef


# Types that may cross from Yellow to Blue.
def _blue_safe(value, depth=0):
    if depth > 50:
        return False
    if value is None or isinstance(value, (bool, int, float, str)):
        return True
    if isinstance(value, (list, tuple, set, frozenset)):
        return all(_blue_safe(v, depth + 1) for v in value)
    if isinstance(value, dict):
        return all(_blue_safe(k, depth + 1) and _blue_safe(v, depth + 1)
                   for k, v in value.items())
    return False


# ============================================================= YELLOW RULES ===

def check_yellow(tree, src_name):
    """Yellow must END with blue(), and call blue() nowhere else."""
    body = tree.body
    last = body[-1] if body else None
    ends_with_blue = (
        isinstance(last, ast.Expr) and isinstance(last.value, ast.Call)
        and isinstance(last.value.func, ast.Name) and last.value.func.id == "blue"
    )
    if not ends_with_blue:
        line = getattr(last, "end_lineno", None)
        raise PydError("yellow",
            "Yellow must end by executing Blue: put  blue()  as the last line. "
            "Without it, nothing you give() is registered.", line, src_name)
    if last.value.args or last.value.keywords:
        raise PydError("yellow", "blue() takes no arguments.", last.lineno, src_name)

    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "blue" and node is not last.value):
            raise PydError("yellow",
                "blue() can only be called once, as the very last line of Yellow.",
                node.lineno, src_name)


# ================================================================= PARSING ===

SECTION_RE = re.compile(r"^\s*::\s*(yellow|blue)\s*::\s*$", re.IGNORECASE)


def split_sections(text, src_name):
    """Return {'yellow': (code, first_line), 'blue': (code, first_line)}."""
    sections, order, current, buf, start = {}, [], None, [], 1
    for i, line in enumerate(text.splitlines(), 1):
        m = SECTION_RE.match(line)
        if m:
            if current:
                sections[current] = ("\n".join(buf), start)
            current = m.group(1).lower()
            if current in sections:
                raise PydError("pyd", f"Section ':: {current} ::' appears twice.", i, src_name)
            order.append(current)
            buf, start = [], i + 1
        elif current is None:
            if line.strip() and not line.strip().startswith("#"):
                raise PydError("pyd", "Code must be inside a ':: yellow ::' or ':: blue ::' section.",
                               i, src_name)
        else:
            buf.append(line)
    if current:
        sections[current] = ("\n".join(buf), start)

    if "blue" not in sections:
        raise PydError("pyd", "Missing ':: blue ::' section.", None, src_name)
    if order and order[-1] != "blue":
        raise PydError("pyd", "':: blue ::' must be the last section - Blue always runs at the end.",
                       None, src_name)
    return sections


def _parse(code, first_line, side, src_name):
    try:
        tree = ast.parse(code, filename=src_name)
    except SyntaxError as e:
        line = (e.lineno or 1) + first_line - 1
        raise PydError(side, f"Syntax error: {e.msg}", line, src_name)
    ast.increment_lineno(tree, first_line - 1)
    return tree


def _compile(tree, src_name):
    return compile(tree, src_name, "exec")


# ================================================================= RUNNING ===

def _run_blue(blue_tree, src_name, given):
    BlueChecker(src_name).visit(blue_tree)
    env = {"__builtins__": dict(BLUE_BUILTINS), "__name__": "__blue__"}
    env.update(given)
    try:
        exec(_compile(blue_tree, src_name), env)
    except PydError:
        raise
    except Exception as e:
        raise PydError("blue", f"{type(e).__name__}: {e}", _err_line(e, src_name), src_name)


def _err_line(exc, src_name):
    tb, line = exc.__traceback__, None
    while tb:
        if tb.tb_frame.f_code.co_filename == src_name:
            line = tb.tb_lineno
        tb = tb.tb_next
    return line


def load(path):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    name = os.path.basename(path)

    if path.endswith(".blue"):
        blue_tree = _parse(text, 1, "blue", name)
        BlueChecker(name).visit(blue_tree)
        return name, None, blue_tree

    sections = split_sections(text, name)
    yellow_tree = None
    if "yellow" in sections:
        code, start = sections["yellow"]
        yellow_tree = _parse(code, start, "yellow", name)
        check_yellow(yellow_tree, name)
    code, start = sections["blue"]
    blue_tree = _parse(code, start, "blue", name)
    BlueChecker(name).visit(blue_tree)
    return name, yellow_tree, blue_tree


def run(path):
    name, yellow_tree, blue_tree = load(path)

    if yellow_tree is None:
        _run_blue(blue_tree, name, {})
        return

    pending = {}          # values queued by give(); only registered by blue()
    state = {"ran": False}

    def give(var_name, value):
        if not isinstance(var_name, str) or not var_name.isidentifier():
            raise PydError("yellow", f"give() needs a valid variable name, got {var_name!r}.")
        if var_name in BLUE_BUILTINS or var_name in BLUE_FORBIDDEN_NAMES:
            raise PydError("yellow", f"'{var_name}' is a reserved name in Blue.")
        if not _blue_safe(value):
            raise PydError("yellow",
                f"Can't give '{var_name}' to Blue: a {type(value).__name__} isn't basic data. "
                "Give numbers, text, True/False/None, or lists/dicts of those.")
        pending[var_name] = value

    def blue():
        state["ran"] = True
        registered = dict(pending)  # this is the moment gifts are registered
        _run_blue(blue_tree, name, registered)

    env = {"__name__": "__yellow__", "give": give, "blue": blue}
    yellow_dir = os.path.dirname(os.path.abspath(path))
    old_cwd = os.getcwd()
    os.chdir(yellow_dir)  # Yellow reads files relative to the program
    try:
        exec(_compile(yellow_tree, name), env)
    except PydError as e:
        if e.src_name is None:
            e.src_name, e.line = name, _err_line(e, name)
        raise
    except SystemExit as e:
        if not state["ran"]:
            raise PydError("yellow", "Yellow exited before executing Blue - nothing was registered.",
                           _err_line(e, name), name)
    except Exception as e:
        raise PydError("yellow", f"{type(e).__name__}: {e}", _err_line(e, name), name)
    finally:
        os.chdir(old_cwd)

    if not state["ran"]:  # e.g. Yellow returned early via sys.exit
        raise PydError("yellow", "Yellow finished without executing Blue - nothing was registered.",
                       None, name)


# ===================================================================== CLI ===

TEMPLATE = '''\
# {name}.pyd - a Python Disconnected program

:: yellow ::
# Full Python lives here: APIs, files, text managing, imports.
# Hand basic values to Blue with give("name", value).
text = "hello disconnected world"
words = text.split()

give("words", words)
give("count", len(words))
blue()   # always the last line of Yellow

:: blue ::
# Basic programming only. Variables given by Yellow are ready to use.
print("Blue received", count, "words:")
for i, w in enumerate(words):
    print(i + 1, w)
'''


def main(argv):
    if len(argv) < 2 or argv[1] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    if argv[1] in ("-v", "--version"):
        print(f"Python Disconnected {VERSION}")
        return 0

    cmd = argv[1]
    if cmd == "new":
        if len(argv) != 3:
            print(f"{PYD_TAG} usage: pyd new <name>"); return 2
        target = argv[2] if argv[2].endswith(".pyd") else argv[2] + ".pyd"
        if os.path.exists(target):
            print(f"{PYD_TAG} {target} already exists"); return 1
        with open(target, "w", encoding="utf-8") as f:
            f.write(TEMPLATE.format(name=os.path.splitext(os.path.basename(target))[0]))
        print(f"{PYD_TAG} created {target}")
        return 0

    if cmd in ("run", "check") and len(argv) == 3:
        path = argv[2]
    elif cmd.endswith((".pyd", ".blue")) and len(argv) == 2:
        cmd, path = "run", cmd
    else:
        print(f"{PYD_TAG} usage: pyd run|check <file.pyd|file.blue>"); return 2

    if not os.path.isfile(path):
        print(f"{PYD_TAG} file not found: {path}"); return 1
    try:
        if cmd == "check":
            load(path)
            print(f"{PYD_TAG} {path}: OK - Yellow and Blue follow the rules")
        else:
            run(path)
    except PydError as e:
        print(e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
