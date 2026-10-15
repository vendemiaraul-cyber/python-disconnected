---
name: pyd
description: Write a new Python Disconnected (.pyd) program from a plain description, then check and run it. Use when the user asks for a pyd, a Python Disconnected program, or a Yellow/Blue program.
argument-hint: [what the program should do]
---

Write a Python Disconnected program that does this: $ARGUMENTS

Follow the language rules in AGENTS.md exactly:
- `:: yellow ::` section first: imports, APIs, files and all text managing; hand data over with `give("name", value)`; the very last line is `blue()`.
- `:: blue ::` section last: basic programming only, no imports, no file access, no text methods.

Steps:
1. Pick a short snake_case file name that describes the program and write it as `<name>.pyd` in the project root.
2. Run `python3 pyd.py check <name>.pyd` and fix anything it reports.
3. Run `python3 pyd.py run <name>.pyd`.
4. Reply with the file name, one line on how Yellow and Blue split the work, and the output.

If the description is empty, ask the user what the program should do.
