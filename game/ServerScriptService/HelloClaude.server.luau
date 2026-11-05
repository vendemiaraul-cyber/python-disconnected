-- Test script from Claude. Press Play: if a glowing purple cube spins above
-- the spawn point, Claude Sync is working. Ask Claude to delete it any time.
local part = Instance.new("Part")
part.Name = "HelloFromClaude"
part.Size = Vector3.new(6, 6, 6)
part.Position = Vector3.new(0, 12, 0)
part.Anchored = true
part.Material = Enum.Material.Neon
part.Color = Color3.fromRGB(170, 85, 255)
part.Parent = workspace

print("Hello from Claude! Sync is working.")

while true do
	part.CFrame *= CFrame.Angles(0, math.rad(2), 0)
	task.wait()
end
