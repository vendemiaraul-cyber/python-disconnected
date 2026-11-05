# Roblox Studio on your iPhone

Roblox Studio can't run on iOS. Instead, it runs on your Windows PC and your
iPhone controls that PC remotely. This uses Chrome Remote Desktop, which is
free, works over Wi-Fi or mobile data, and needs no router setup.

## On the PC (one time, about 5 minutes)

1. Download this repo (green **Code** button, then **Download ZIP**) and unzip it.
2. Double-click **`START-HERE.bat`**.
   - It downloads and installs **Roblox Studio** (official installer from roblox.com).
   - It downloads and installs **Chrome Remote Desktop** (official installer from Google). Click **Yes** on the permission prompt.
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
