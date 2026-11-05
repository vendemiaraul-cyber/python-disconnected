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

**Claude knows when Studio is open.** While Studio runs, the plugin sends a tiny
"I'm open" signal to a private [ntfy.sh](https://ntfy.sh) channel once a minute, plus any
red errors from Output. When you start a Claude session, Claude checks it automatically.
If Studio is open, Claude gets ready without being asked: it loads your latest game code
and reads your errors, so you never have to screenshot them.

A test script (`HelloClaude`) comes included. After your first Pull, press Play and you should see a
spinning purple cube. Ask Claude to remove it whenever you like.

## Less lag

- The PC setup turns off Windows animations and transparency, and picks the high-performance power plan.
- The plugin's **Low-lag mode** is on by default. It draws Studio's 3D view at the lowest quality, so the
  remote picture is lighter. Tap it in the panel to switch it off when you want things to look nice.
- The biggest wins are free: connect the PC to the router with a cable, put the iPhone on 5 GHz Wi-Fi,
  and use a lower PC screen resolution (for example 1280x720). Close games and video on the PC while you work.

Using regular Claude chat instead of the Code tab? See [CLAUDE-APP-INSTRUCTIONS.md](CLAUDE-APP-INSTRUCTIONS.md)
for a block of text to paste into a Claude Project.

> This repo is public, so anyone can see the game code and the status channel name, which means anyone
> could see whether Studio is open. Don't put passwords or keys in it.
> Already ran the setup before this update? Run `START-HERE.bat` again to get the new plugin.
