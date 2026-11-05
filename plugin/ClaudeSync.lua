-- Claude Sync: pulls the Luau code Claude writes (the repo's /game folder) into the open place.
-- setup-pc.ps1 installs this file into %LOCALAPPDATA%\Roblox\Plugins.
--
-- Folder layout it understands (first folder = Roblox service, extra folders become Folders):
--   game/ServerScriptService/Coins.server.luau          -> Script
--   game/StarterPlayer/StarterPlayerScripts/Hud.client.luau -> LocalScript
--   game/ReplicatedStorage/Config.luau                   -> ModuleScript

local RunService = game:GetService("RunService")
local HttpService = game:GetService("HttpService")
local LogService = game:GetService("LogService")

---------------------------------------------------------------- Status for Claude
-- Claude reads this ntfy.sh channel at session start (.claude/hooks/session-start.sh)
-- to know Studio is open, what's synced, and which errors showed up in Output.
local STATUS_TOPIC = "pocketstudio-1cfc43541489275a"
local HEARTBEAT_INTERVAL = 60

local function send(event, data)
	data = data or {}
	data.event = event
	data.place = game.Name
	task.spawn(function() -- never block Studio on the network
		pcall(HttpService.PostAsync, HttpService, "https://ntfy.sh/",
			HttpService:JSONEncode({ topic = STATUS_TOPIC, message = HttpService:JSONEncode(data) }),
			Enum.HttpContentType.ApplicationJson)
	end)
end

-- Forward red Output errors (edit mode and playtests), batched and de-duplicated.
local mode = RunService:IsEdit() and "edit" or (RunService:IsClient() and "play-client" or "play-server")
local pendingErrors, seenErrors = {}, {}
LogService.MessageOut:Connect(function(text, kind)
	if kind ~= Enum.MessageType.MessageError or seenErrors[text] or #pendingErrors >= 10 then return end
	seenErrors[text] = true
	table.insert(pendingErrors, text:sub(1, 300))
	if #pendingErrors == 1 then
		task.delay(5, function()
			local batch = pendingErrors
			pendingErrors = {}
			send("errors", { mode = mode, lines = batch })
		end)
	end
end)

if not RunService:IsEdit() then return end -- below: edit mode only, never touch a running playtest
local ChangeHistoryService = game:GetService("ChangeHistoryService")
local CollectionService = game:GetService("CollectionService")
local ScriptEditorService = game:GetService("ScriptEditorService")

local OWNER = "vendemiaraul-cyber"
local REPO = "python-disconnected"
local ROOT = "game/"
local TAG = "ClaudeSync"
local PATH_ATTR = "ClaudeSyncPath"
local AUTO_INTERVAL = 90 -- seconds; GitHub allows 60 anonymous API calls per hour

local branch = plugin:GetSetting("branch") or "studio"
local auto = plugin:GetSetting("auto") == true
local lowLag = plugin:GetSetting("lowLag") ~= false -- on unless turned off
local lastSha = nil
local busy = false

---------------------------------------------------------------- UI
local toolbar = plugin:CreateToolbar("Claude")
local pullButton = toolbar:CreateButton("ClaudePull", "Pull Claude's latest code into this place", "", "Pull")
local panelButton = toolbar:CreateButton("ClaudePanel", "Show or hide the Claude Sync panel", "", "Panel")
pullButton.ClickableWhenViewportHidden = true
panelButton.ClickableWhenViewportHidden = true

local widget = plugin:CreateDockWidgetPluginGui("ClaudeSyncPanel",
	DockWidgetPluginGuiInfo.new(Enum.InitialDockState.Float, true, false, 280, 280, 220, 240))
widget.Title = "Claude Sync"

local theme = settings().Studio.Theme
local C = Enum.StudioStyleGuideColor

local root = Instance.new("Frame")
root.Size = UDim2.fromScale(1, 1)
root.BackgroundColor3 = theme:GetColor(C.MainBackground)
root.BorderSizePixel = 0
root.Parent = widget
local pad = Instance.new("UIPadding")
for _, side in { "PaddingTop", "PaddingBottom", "PaddingLeft", "PaddingRight" } do
	pad[side] = UDim.new(0, 8)
end
pad.Parent = root
local list = Instance.new("UIListLayout")
list.Padding = UDim.new(0, 6)
list.SortOrder = Enum.SortOrder.LayoutOrder
list.Parent = root

local order = 0
local function add(className, text, height, bg, fg)
	local obj = Instance.new(className)
	order += 1
	obj.LayoutOrder = order
	obj.Size = UDim2.new(1, 0, 0, height or 32)
	obj.Font = Enum.Font.SourceSans
	obj.TextSize = 18
	obj.Text = text or ""
	obj.TextColor3 = theme:GetColor(fg or C.MainText)
	obj.BackgroundColor3 = theme:GetColor(bg or C.InputFieldBackground)
	obj.BorderSizePixel = 0
	obj.Parent = root
	return obj
end

local branchLabel = add("TextLabel", "Branch Claude pushes to:", 20)
branchLabel.BackgroundTransparency = 1
branchLabel.TextXAlignment = Enum.TextXAlignment.Left
local branchBox = add("TextBox", branch)
branchBox.ClearTextOnFocus = false
branchBox.PlaceholderText = "studio"
local bigPull = add("TextButton", "Pull Claude's code", 48, C.DialogMainButton, C.DialogMainButtonText)
local autoButton = add("TextButton", "", 32, C.Button, C.ButtonText)
local lagButton = add("TextButton", "", 32, C.Button, C.ButtonText)
local status = add("TextLabel", "Tap Pull after Claude says it pushed.", 70)
status.BackgroundTransparency = 1
status.TextWrapped = true
status.TextXAlignment = Enum.TextXAlignment.Left
status.TextYAlignment = Enum.TextYAlignment.Top

local function setStatus(msg)
	status.Text = os.date("%H:%M ") .. msg
	print("[Claude Sync] " .. msg)
end

local function refreshAuto()
	autoButton.Text = auto and ("Auto-pull: ON (every %ds)"):format(AUTO_INTERVAL) or "Auto-pull: OFF"
end
refreshAuto()

-- Low-lag mode: render the edit viewport at the lowest quality, so the PC
-- encodes a lighter picture for remote desktop. Turning it off restores your setting.
local function applyLowLag()
	lagButton.Text = lowLag and "Low-lag mode: ON" or "Low-lag mode: OFF"
	pcall(function()
		local rendering = settings().Rendering
		local saved = plugin:GetSetting("savedQuality")
		if lowLag then
			if saved == nil then plugin:SetSetting("savedQuality", rendering.EditQualityLevel.Name) end
			rendering.EditQualityLevel = Enum.QualityLevel.Level01
		elseif saved ~= nil then
			rendering.EditQualityLevel = Enum.QualityLevel[saved]
			plugin:SetSetting("savedQuality", nil)
		end
	end)
end
applyLowLag()

---------------------------------------------------------------- Sync
local function get(url)
	return HttpService:GetAsync(url, true)
end

local function encodePath(p)
	local parts = string.split(p, "/")
	for i, s in parts do
		parts[i] = HttpService:UrlEncode(s)
	end
	return table.concat(parts, "/")
end

local function parseFile(fileName)
	local base = (fileName:gsub("%.luau?$", ""))
	if base:match("%.server$") then return base:sub(1, -8), "Script" end
	if base:match("%.client$") then return base:sub(1, -8), "LocalScript" end
	return base, "ModuleScript"
end

local function resolveParent(segments)
	local ok, node = pcall(game.GetService, game, segments[1])
	if not ok or not node then node = game:FindFirstChild(segments[1]) end
	if not node then error("Unknown service '" .. segments[1] .. "'", 0) end
	for i = 2, #segments do
		local child = node:FindFirstChild(segments[i])
		if not child then
			child = Instance.new("Folder")
			child.Name = segments[i]
			child.Parent = node
		end
		node = child
	end
	return node
end

local function setSource(obj, src)
	if obj.Source == src then return false end
	local ok = pcall(function()
		ScriptEditorService:UpdateSourceAsync(obj, function() return src end)
	end)
	if not ok then obj.Source = src end
	return true
end

local function friendly(err)
	err = tostring(err)
	if err:find("404") then return ("Branch '%s' not found yet. Ask Claude to push to it."):format(branch) end
	if err:find("403") or err:find("429") then return "GitHub rate limit hit. Wait a few minutes and try again." end
	if err:lower():find("permission") or err:lower():find("not allowed") then
		return "Allow HTTP access: Plugins > Manage Plugins > Claude Sync > allow github domains."
	end
	return "Error: " .. err
end

-- Download everything first, so a network error never leaves the place half-synced.
local function fetch(manual)
	local commit = HttpService:JSONDecode(get(("https://api.github.com/repos/%s/%s/commits/%s")
		:format(OWNER, REPO, HttpService:UrlEncode(branch))))
	local sha = commit.sha
	if sha == lastSha and not manual then return nil end
	local tree = HttpService:JSONDecode(get(("https://api.github.com/repos/%s/%s/git/trees/%s?recursive=1")
		:format(OWNER, REPO, sha)))
	local files = {}
	for _, item in tree.tree do
		local p = item.path
		if item.type == "blob" and p:sub(1, #ROOT) == ROOT and p:match("%.luau?$") then
			files[p] = get(("https://raw.githubusercontent.com/%s/%s/%s/%s"):format(OWNER, REPO, sha, encodePath(p)))
		end
	end
	return sha, files
end

local function apply(files)
	local existing = {}
	for _, obj in CollectionService:GetTagged(TAG) do
		local p = obj:GetAttribute(PATH_ATTR)
		if p and obj:IsA("LuaSourceContainer") then existing[p] = obj end
	end
	local added, updated, removed, skipped = 0, 0, 0, 0
	for path, src in files do
		local segments = string.split(path:sub(#ROOT + 1), "/")
		local fileName = table.remove(segments)
		if #segments == 0 then
			skipped += 1 -- file sits directly in game/, no service folder
			continue
		end
		local name, className = parseFile(fileName)
		local parent = resolveParent(segments)
		local obj = existing[path]
		existing[path] = nil
		if obj and obj.ClassName ~= className then
			obj:Destroy()
			obj = nil
		end
		if not obj then
			local found = parent:FindFirstChild(name)
			if found and found.ClassName == className then obj = found end
		end
		if not obj then
			obj = Instance.new(className)
			obj.Name = name
			obj.Source = src
			obj.Parent = parent
			added += 1
		elseif setSource(obj, src) then
			updated += 1
		end
		obj:SetAttribute(PATH_ATTR, path)
		CollectionService:AddTag(obj, TAG)
	end
	for _, obj in existing do -- Claude deleted these files
		obj:Destroy()
		removed += 1
	end
	return added, updated, removed, skipped
end

local function pull(manual)
	if busy then return end
	busy = true
	if manual then setStatus("Pulling from '" .. branch .. "'...") end
	local ok, sha, files = pcall(fetch, manual)
	if not ok then
		setStatus(friendly(sha))
	elseif sha then
		local recording = ChangeHistoryService:TryBeginRecording("Claude Sync")
		local applied, a, u, r, s = pcall(apply, files)
		if recording then
			ChangeHistoryService:FinishRecording(recording, Enum.FinishRecordingOperation.Commit)
		end
		if applied then
			lastSha = sha
			local msg = ("Synced %s: %d new, %d updated, %d removed."):format(sha:sub(1, 7), a, u, r)
			if s > 0 then msg ..= (" %d skipped (not in a service folder)."):format(s) end
			if a + u + r == 0 then msg = "Already up to date (" .. sha:sub(1, 7) .. ")." end
			setStatus(msg)
			if a + u + r > 0 then send("synced", { branch = branch, sha = sha:sub(1, 7) }) end
		else
			setStatus(friendly(a))
		end
	end
	busy = false
end

---------------------------------------------------------------- Wiring
pullButton.Click:Connect(function()
	pullButton:SetActive(false)
	pull(true)
end)
bigPull.Activated:Connect(function() pull(true) end)
panelButton.Click:Connect(function()
	panelButton:SetActive(false)
	widget.Enabled = not widget.Enabled
end)
autoButton.Activated:Connect(function()
	auto = not auto
	plugin:SetSetting("auto", auto)
	refreshAuto()
	if auto then task.spawn(pull, false) end
end)
lagButton.Activated:Connect(function()
	lowLag = not lowLag
	plugin:SetSetting("lowLag", lowLag)
	applyLowLag()
end)
branchBox.FocusLost:Connect(function()
	local text = branchBox.Text:match("^%s*(.-)%s*$")
	branch = text ~= "" and text or "studio"
	branchBox.Text = branch
	plugin:SetSetting("branch", branch)
	lastSha = nil
end)

task.spawn(function()
	while true do
		task.wait(AUTO_INTERVAL)
		if auto then pull(false) end
	end
end)

-- Heartbeat: lets Claude know Studio is open (it treats 3+ minutes of silence as closed).
local function heartbeat(event)
	send(event, { placeId = game.PlaceId, branch = branch, synced = lastSha and lastSha:sub(1, 7), auto = auto })
end
heartbeat("opened")
task.spawn(function()
	while true do
		task.wait(HEARTBEAT_INTERVAL)
		heartbeat("heartbeat")
	end
end)
plugin.Unloading:Connect(function() heartbeat("closed") end)
