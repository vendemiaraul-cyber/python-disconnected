# Who you're working with

The user vibe codes Roblox games: they describe what they want and you write
the code. They are **on an iPhone**, controlling Roblox Studio on their Windows
PC through Chrome Remote Desktop (see README.md). Typing and copy-pasting on
the phone is slow and awkward, so the code reaches Studio through the
**Claude Sync** plugin (`plugin/ClaudeSync.lua`). They tap **Pull** in Studio
and your scripts show up.

Assume this setup in every session in this repo, unless the user says they're on the PC directly.

# How to deliver game code

1. **Never ask them to copy-paste code.** Write the code as files under `game/`:
   - The first folder is the Roblox service. Deeper folders become `Folder` instances.
   - `Name.server.luau` becomes a `Script`, `Name.client.luau` a `LocalScript`, and `Name.luau` a `ModuleScript`.
   - Examples: `game/ServerScriptService/Coins.server.luau`,
     `game/StarterPlayer/StarterPlayerScripts/Hud.client.luau`,
     `game/ReplicatedStorage/Shared/Config.luau`.
   - Deleting a file here deletes that script in Studio on the next Pull.
2. **Only scripts sync, not parts, models, or GUIs built in the editor.** So create
   those from code (parts in a server script, ScreenGuis in a LocalScript,
   RemoteEvents in ReplicatedStorage created by the server), instead of asking
   the user to build them by hand. If something really has to be done by hand
   in Studio, give short tap-by-tap steps.
3. **Publish to the `studio` branch**, which the plugin pulls from:
   - Before you edit `game/`, run `git fetch origin studio` and `git merge origin/studio`
     so you build on the current game. Skip this if the branch doesn't exist yet.
   - Commit, push your own branch, then run `git push origin HEAD:studio`.
     If that is rejected, fetch and merge again, then retry. Never force-push `studio`.
4. End every reply with the next step for the user, for example:
   **"In Studio, tap Claude → Pull, then press Play."** Say what they should see.

# Style

- Short replies. They're reading on a phone screen. Use plain language, no jargon.
  Explain *what* the game does now, not how the code works, unless they ask.
- Write modern Luau: `task.wait` and `task.spawn`, `game:GetService`, typed where helpful.
  Server-authoritative logic goes in ServerScriptService, never trusting the client.
- You can't run Studio. Re-read your code for typos and nil errors before pushing.
  If they report a bug, ask them to open **View → Output** and read or screenshot the red text.
- The repo is **public**. Never commit API keys, cookies, or personal info.
