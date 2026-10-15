# Python Disconnected — instructions for AI coding agents

This folder is a small language called **Python Disconnected**. When the user asks
for a program, a script, or "a pyd", write it as a Python Disconnected file
(`.pyd`) unless they ask for something else.

## The language

A `.pyd` file has two sections, **Yellow first, Blue last**:

```
:: yellow ::
<Yellow Python code>
blue()

:: blue ::
<Blue Python code>
```

### Yellow Python (runs first)
- Full Python 3: `import`, files, web APIs (`urllib`, `json`…), string methods, regex, anything hard.
- Hands values to Blue with `give("name", value)`. The name becomes a variable in Blue.
- Only basic data may be given: `int`, `float`, `bool`, `None`, `str`, and `list` / `tuple` / `set` / `dict` of those. Never modules, files, responses or custom objects.
- **Its last line must be exactly `blue()`**, at top level, no arguments, used nowhere else. That line registers everything given and runs Blue. Without it nothing is registered.
- Relative file paths are resolved from the `.pyd` file's own folder.

### Blue Python (runs last)
- Basic programming only: numbers, variables, `if`/`elif`/`else`, `for`/`while`, functions, `lambda`, classes, lists, dicts, sets, tuples, `print`, f-strings.
- Allowed builtins: `print range len int float str bool list dict set tuple abs min max sum round sorted reversed enumerate zip map filter any all isinstance divmod pow` and common exception types.
- **Forbidden in Blue:** `import`, `open`, `input`, `eval`, `exec`, `getattr`, `type`, `format`, `repr`, `chr`, `ord`, `with` blocks, `async`, any `_hidden` / `__dunder__` attribute, and **all text methods**: `split rsplit splitlines join replace strip lstrip rstrip lower upper title capitalize casefold swapcase find rfind startswith endswith format format_map encode decode center ljust rjust zfill partition rpartition translate maketrans expandtabs isalpha isdigit isnumeric isalnum isspace isupper islower istitle removeprefix removesuffix`.
- So: do all text cleaning, splitting, joining and parsing in Yellow, and give Blue ready-made values. Blue may still `print` strings and use f-strings.

A `.blue` file is a Blue-only program (no sections, nothing given).

## How to split work
1. Put every outside-world or text task in Yellow: fetching APIs, reading files, parsing, string formatting that needs methods.
2. `give()` Blue only the plain data it needs, with clear names.
3. Put calculations, decisions, loops and output in Blue.
4. If an API might be unreachable, wrap it in `try/except` in Yellow and fall back to sample data.

## Workflow (always do this)
1. Write the program to a new `.pyd` file (in `examples/` only if the user asks; otherwise the project root).
2. Run `python3 pyd.py check <file>` and fix every error it reports.
3. Run `python3 pyd.py run <file>` and confirm the output makes sense.
4. Tell the user the file name and show the output. Don't modify `pyd.py` unless asked.

## Example

```
:: yellow ::
import json
data = json.loads('{"scores": [12, 7, 19]}')
give("scores", data["scores"])
give("player", "Ada")
blue()

:: blue ::
best = max(scores)
print(f"{player}'s best score: {best}")
if best > 15:
    print("High score!")
```
