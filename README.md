# Roblox Studio on your iPhone

Roblox Studio can't run on iOS. Instead, it runs on your Windows PC and your
iPhone controls that PC remotely. This uses Chrome Remote Desktop, which is
free, works over Wi-Fi or mobile data, and needs no router setup.

## On the PC (one time, about 5 minutes)

1. Download this repo (green **Code** button, then **Download ZIP**) and unzip it.
2. Double-click **`START-HERE.bat`**.
   - It downloads and installs **Roblox Studio** (official installer from roblox.com).
   - It downloads and installs **Chrome Remote Desktop** (official installer from Google). Click **Yes** on the permission prompt.
   - It installs the **Claude Sync** plugin into Studio. More on that below.
   - It sets the PC to **never sleep while plugged in**, so you can always reach it.
   - It opens `remotedesktop.google.com/access` in your browser.
3. On that page, sign in with Google and click **Turn on** under *Set up remote access*. Give the PC a name and choose a PIN.

If Windows SmartScreen warns about the file, click **More info**, then **Run anyway**.
The script is plain text, so you can open `setup-pc.ps1` in Notepad and read it first.

## On the iPhone

1. Install **Chrome Remote Desktop** from the App Store.
2. Sign in with the **same Google account**.
3. Tap your PC, enter the PIN, then open **Roblox Studio**.

## Controlling Studio from a touchscreen

| Action | Touch gesture |
|---|---|
| Left click / select | Tap |
| Right click | Two-finger tap |
| Drag / move a part | Tap and hold, then drag |
| Scroll / zoom camera | Two-finger swipe up or down |
| Keyboard (WASD, Ctrl+Z, ...) | Tap the keyboard icon in the app menu |
| Switch touch to trackpad mode | App menu, then **Trackpad mode** |

Tips:
- Turn the iPhone **sideways** and use Studio's **View** tab to hide panels you don't need.
- A **Bluetooth mouse and keyboard** paired to the iPhone make it feel like a real PC (right-drag to look around, WASD to fly).
- On the PC, a lower screen resolution (for example 1280x720) makes the interface larger on the phone.

## Vibe coding with Claude

The repo includes a `CLAUDE.md` file. Claude reads it at the start of every
session in this repo, so it already knows you're on an iPhone using Studio
through remote desktop. You never have to explain the setup again.

How it works:

1. In the Claude iPhone app, open the **Code** tab, pick this repo, and describe what you want, for example
   *"make a coin that spins and gives +1 point when touched"*.
2. Claude writes the Luau scripts into the `game/` folder and pushes them to the `studio` branch.
3. In Studio, open the **Plugins** tab and tap **Claude → Pull**, or the big button in the floating *Claude Sync* panel.
   Your scripts appear in the right places (ServerScriptService and so on). Press **Play** to test.

The first time you pull, Studio asks whether Claude Sync may reach `api.github.com` and
`raw.githubusercontent.com`. Tap **Allow**. Turn on **Auto-pull** in the panel and new
code comes in by itself about every 90 seconds, with no tapping.

A test script (`HelloClaude`) comes included. After your first Pull, press Play and you should see a
spinning purple cube. Ask Claude to remove it whenever you like.

Using regular Claude chat instead of the Code tab? See [CLAUDE-APP-INSTRUCTIONS.md](CLAUDE-APP-INSTRUCTIONS.md)
for a block of text to paste into a Claude Project.

> This repo is public, so anyone can see the game code. Don't put passwords or keys in it.
