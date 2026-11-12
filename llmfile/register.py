"""Register .llm as a file type, so double-clicking one opens a chat."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

MIME = "application/x-llm"
DESCRIPTION = "LLM model file"


def _command() -> list[str]:
    return [sys.executable, "-m", "llmfile", "chat"]


def register() -> str:
    if sys.platform == "win32":
        return _register_windows()
    if sys.platform == "darwin":
        return ("macOS needs an app bundle to own a file type, which llmfile doesn't ship.\n"
                "Open .llm files with:  llmfile chat example.llm")
    return _register_linux()


def unregister() -> str:
    if sys.platform == "win32":
        import winreg
        for key in (r"Software\Classes\LLMFile.Model\shell\open\command",
                    r"Software\Classes\LLMFile.Model\shell\open",
                    r"Software\Classes\LLMFile.Model\shell",
                    r"Software\Classes\LLMFile.Model\DefaultIcon",
                    r"Software\Classes\LLMFile.Model",
                    r"Software\Classes\.llm"):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
            except FileNotFoundError:
                pass
        _notify_windows()
        return "Removed the .llm file type."
    removed = []
    for path in _linux_paths():
        if os.path.exists(path):
            os.remove(path)
            removed.append(path)
    _refresh_linux()
    return "Removed:\n  " + "\n  ".join(removed) if removed else "Nothing to remove."


def _register_windows() -> str:
    import winreg
    exe = sys.executable
    cmd = f'"{exe}" -m llmfile chat "%1"'
    root = winreg.HKEY_CURRENT_USER
    with winreg.CreateKey(root, r"Software\Classes\.llm") as k:
        winreg.SetValue(k, "", winreg.REG_SZ, "LLMFile.Model")
        winreg.SetValueEx(k, "Content Type", 0, winreg.REG_SZ, MIME)
    with winreg.CreateKey(root, r"Software\Classes\LLMFile.Model") as k:
        winreg.SetValue(k, "", winreg.REG_SZ, DESCRIPTION)
    with winreg.CreateKey(root, r"Software\Classes\LLMFile.Model\DefaultIcon") as k:
        winreg.SetValue(k, "", winreg.REG_SZ, f'"{exe}",0')
    with winreg.CreateKey(root, r"Software\Classes\LLMFile.Model\shell\open\command") as k:
        winreg.SetValue(k, "", winreg.REG_SZ, cmd)
    _notify_windows()
    return f"Registered .llm files. Double-clicking one runs:\n  {cmd}"


def _notify_windows() -> None:
    import ctypes
    ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)  # SHCNE_ASSOCCHANGED


def _linux_paths() -> tuple[str, str]:
    data = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return (os.path.join(data, "mime", "packages", "llmfile.xml"),
            os.path.join(data, "applications", "llmfile.desktop"))


def _register_linux() -> str:
    mime_xml, desktop = _linux_paths()
    os.makedirs(os.path.dirname(mime_xml), exist_ok=True)
    os.makedirs(os.path.dirname(desktop), exist_ok=True)
    with open(mime_xml, "w") as f:
        f.write(f"""<?xml version="1.0" encoding="UTF-8"?>
<mime-info xmlns="http://www.freedesktop.org/standards/shared-mime-info">
  <mime-type type="{MIME}">
    <comment>{DESCRIPTION}</comment>
    <glob pattern="*.llm"/>
  </mime-type>
</mime-info>
""")
    exec_line = " ".join(f'"{a}"' if " " in a else a for a in _command()) + " %f"
    with open(desktop, "w") as f:
        f.write(f"""[Desktop Entry]
Type=Application
Name=LLM Chat
Comment=Chat with an .llm model file
Exec={exec_line}
Terminal=true
MimeType={MIME};
NoDisplay=true
""")
    _refresh_linux()
    if shutil.which("xdg-mime"):
        subprocess.run(["xdg-mime", "default", "llmfile.desktop", MIME], check=False)
    return (f"Registered .llm files ({MIME}). Double-clicking one opens a chat in a terminal.\n"
            f"  {mime_xml}\n  {desktop}")


def _refresh_linux() -> None:
    mime_xml, desktop = _linux_paths()
    for tool, arg in (("update-mime-database", os.path.dirname(os.path.dirname(mime_xml))),
                      ("update-desktop-database", os.path.dirname(desktop))):
        if shutil.which(tool):
            subprocess.run([tool, arg], check=False, capture_output=True)
