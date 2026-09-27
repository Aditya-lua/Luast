-- run status: finished
-- 150 statements recorded in 0.04s
-- URLs requested:
--   https://raw.githubusercontent.com/deividcomsono/Obsidian/main/Library.lua
--   https://raw.githubusercontent.com/deividcomsono/Obsidian/main/addons/ThemeManager.lua
--   https://raw.githubusercontent.com/deividcomsono/Obsidian/main/addons/SaveManager.lua

--@1 
local Players = game:GetService("Players")
--@1 
local ReplicatedStorage = game:GetService("ReplicatedStorage")
--@1 
local VirtualUser = game:GetService("VirtualUser")
--@4 
getgenv().gethui = function(arg, arg2)
	--@1 
	Players.LocalPlayer:WaitForChild("PlayerGui")
end
--@1 
local Packages = ReplicatedStorage:WaitForChild("Packages")
--@1 
local Packets = Packages:WaitForChild("Packets")
--@1 
require(Packets)
--@1 
local DataService = Packages:WaitForChild("DataService")
--@1 
local module = require(DataService)
--@1 
local Configs = ReplicatedStorage:WaitForChild("Configs")
--@1 
local GameConfig = Configs:WaitForChild("GameConfig")
--@1 
require(GameConfig)
--@1 
local UpgradeConfig = Configs:WaitForChild("UpgradeConfig")
--@1 
require(UpgradeConfig)
--@1 
local HiveLayout = Configs:WaitForChild("HiveLayout")
--@1 
require(HiveLayout)
--@1 
local RarityConfig = Configs:WaitForChild("RarityConfig")
--@1 
local module2 = require(RarityConfig)
--@1 
local Modules = ReplicatedStorage:WaitForChild("Modules")
--@1 
local ProductionMath = Modules:WaitForChild("ProductionMath")
--@1 
local module3 = require(ProductionMath)
--@1 
local ToolBuilderShared = Modules:WaitForChild("ToolBuilderShared")
--@1 
require(ToolBuilderShared)
--@1 
local BeeConfig = Configs:WaitForChild("BeeConfig")
--@1 
local module4 = require(BeeConfig)
--@1 
local MutationConfig = Configs:WaitForChild("MutationConfig")
--@1 
local module5 = require(MutationConfig)
--@1 
local response = game:HttpGet("https://raw.githubusercontent.com/deividcomsono/Obsidian/main/Library.lua")
--@1 
local result = loadstring(response)()
--@1 
local PlayerGui = Players.LocalPlayer:WaitForChild("PlayerGui")
--@1 
result.ScreenGui.Parent = PlayerGui
--@1 
local response2 = game:HttpGet("https://raw.githubusercontent.com/deividcomsono/Obsidian/main/addons/ThemeManager.lua")
--@1 
local result2 = loadstring(response2)()
--@1 
local response3 = game:HttpGet("https://raw.githubusercontent.com/deividcomsono/Obsidian/main/addons/SaveManager.lua")
--@1 
local result3 = loadstring(response3)()
--@2 
for i, v in ipairs(module2.Ladder) do
end
--@2 
for i2, v2 in ipairs(module4.Roster) do
end
--@2 
for i3, v3 in ipairs(module5.List) do
end
--@1 
local Window = result:CreateWindow({ Title = "Grow a Beehive", Footer = "Ouroboros Hub", ShowCustomCursor = false, Size = UDim2.fromOffset(900, 640) })
--@1 
result.ShowCustomCursor = false
--@1 
local Tab = Window:AddTab("Main", "flower")
--@1 
local Tab2 = Window:AddTab("Upgrades", "trending-up")
--@1 
local Tab3 = Window:AddTab("Auto Delete", "trash-2")
--@1 
local Tab4 = Window:AddTab("Settings", "settings")
--@1 
local LeftGroupbox = Tab4:AddLeftGroupbox("Discord")
--@9 
LeftGroupbox:AddButton({
	Text = "Join Discord For Dupe",
	Func = function(arg3, arg4)
		--@1 
		setclipboard("https://discord.gg/ehKVq7pf7v")
		--@1 
		result:Notify("Copied Discord invite to clipboard")
	end
})
--@1 
local LeftGroupbox2 = Tab3:AddLeftGroupbox("Discord")
--@9 
LeftGroupbox2:AddButton({
	Text = "Join Discord For Dupe",
	Func = function(arg3, arg4)
		--@1 
		setclipboard("https://discord.gg/ehKVq7pf7v")
		--@1 
		result:Notify("Copied Discord invite to clipboard")
	end
})
--@1 
local LeftGroupbox3 = Tab:AddLeftGroupbox("Discord")
--@9 
LeftGroupbox3:AddButton({
	Text = "Join Discord For Dupe",
	Func = function(arg3, arg4)
		--@1 
		setclipboard("https://discord.gg/ehKVq7pf7v")
		--@1 
		result:Notify("Copied Discord invite to clipboard")
	end
})
--@1 
local LeftGroupbox4 = Tab2:AddLeftGroupbox("Discord")
--@9 
LeftGroupbox4:AddButton({
	Text = "Join Discord For Dupe",
	Func = function(arg3, arg4)
		--@1 
		setclipboard("https://discord.gg/ehKVq7pf7v")
		--@1 
		result:Notify("Copied Discord invite to clipboard")
	end
})
--@1 
local LeftGroupbox5 = Tab:AddLeftGroupbox("Auto Roll", "dices")
--@1 
LeftGroupbox5:AddToggle("AutoRoll", { Text = "Auto Roll", Default = false })
--@1 
LeftGroupbox5:AddSlider("RollDelay", { Text = "Roll Delay", Default = 2, Max = 10, Min = 1.5, Rounding = 1 })
--@1 
LeftGroupbox5:AddToggle("RollAutoBuy", { Text = "Auto Buy Rolled Bees", Default = true })
--@6 
LeftGroupbox5:AddDropdown("RollBuyMode", {
	Text = "Buy Mode",
	Default = "Minimum Rarity",
	Multi = false,
	Values = { "Buy All", "Minimum Rarity", "Specific Rarities" }
})
--@1 
LeftGroupbox5:AddDropdown("RollMinRarity", { Text = "Minimum Rarity", Default = "Rare", Multi = false, Values = { v.Name } })
--@11 
LeftGroupbox5:AddDropdown("RollRarities", {
	Text = "Specific Rarities",
	Default = {},
	Multi = true,
	Values = { v.Name },
	Callback = function(arg5, arg6)
		--@2 
		for k, v4 in arg5 do
		end
	end
})
--@1 
LeftGroupbox5:AddInput("RollMinOdds", { Text = "Only Buy If 1 in X >=", Default = "0", Finished = true, Numeric = true })
--@1 
LeftGroupbox5:AddToggle("RollNotify", { Text = "Notify On Buy", Default = true })
--@1 
local RightGroupbox = Tab:AddRightGroupbox("Auto Honey", "droplet")
--@1 
RightGroupbox:AddToggle("AutoTakeHoney", { Text = "Auto Take Honey", Default = false })
--@1 
RightGroupbox:AddToggle("AutoSellHoney", { Text = "Auto Sell Honey", Default = false })
--@1 
RightGroupbox:AddSlider("MinHoneyToSell", { Text = "Min Honey To Sell", Default = 0, Max = 100000, Min = 0, Rounding = 0 })
--@1 
RightGroupbox:AddSlider("HoneyDelay", { Text = "Loop Delay", Default = 1, Max = 10, Min = 0.2, Rounding = 1 })
--@1 
local RightGroupbox2 = Tab:AddRightGroupbox("Auto Equip", "sparkles")
--@1 
RightGroupbox2:AddToggle("AutoEquipBest", { Text = "Auto Equip Best", Default = false })
--@1 
RightGroupbox2:AddSlider("EquipDelay", { Text = "Loop Delay", Default = 5, Max = 30, Min = 1, Rounding = 1 })
--@1 
local LeftGroupbox6 = Tab2:AddLeftGroupbox("Auto Upgrades", "trending-up")
--@1 
LeftGroupbox6:AddToggle("AutoUpgrade", { Text = "Auto Upgrade", Default = false })
--@11 
LeftGroupbox6:AddDropdown("Upgrades", {
	Text = "Upgrades To Buy",
	Default = {},
	Multi = true,
	Values = { "BeeLuck", "BeeRolls", "FlowerLevel", "BeeSpeed", "VacuumRange" },
	Callback = function(arg7, arg8)
		--@2 
		for k2, v5 in arg7 do
		end
	end
})
--@1 
LeftGroupbox6:AddToggle("AutoExpandHive", { Text = "Auto Expand Hive", Default = false })
--@1 
LeftGroupbox6:AddInput("UpgradeReserve", { Text = "Keep Cash Reserve", Default = "0", Finished = true, Numeric = true })
--@1 
LeftGroupbox6:AddSlider("UpgradeDelay", { Text = "Loop Delay", Default = 1, Max = 10, Min = 0.2, Rounding = 1 })
--@1 
local LeftGroupbox7 = Tab2:AddLeftGroupbox("Auto Upgrade Bees", "arrow-up-circle")
--@1 
LeftGroupbox7:AddToggle("AutoUpgradeBees", { Text = "Auto Upgrade Bees", Default = false })
--@1 
LeftGroupbox7:AddSlider("BeeMaxLevel", { Text = "Max Bee Level", Default = module3.MaxLevel, Max = module3.MaxLevel, Min = 1, Rounding = 0 })
--@1 
LeftGroupbox7:AddInput("BeeMaxCost", { Text = "Max Cost Per Upgrade (0 = off)", Default = "0", Finished = true, Numeric = true })
--@1 
LeftGroupbox7:AddInput("BeeUpgradeReserve", { Text = "Keep Cash Reserve", Default = "0", Finished = true, Numeric = true })
--@1 
LeftGroupbox7:AddSlider("BeeUpgradeDelay", { Text = "Loop Delay", Default = 1, Max = 10, Min = 0.2, Rounding = 1 })
--@1 
local RightGroupbox3 = Tab2:AddRightGroupbox("Auto Buy Hives", "hexagon")
--@1 
RightGroupbox3:AddToggle("AutoBuyHives", { Text = "Auto Buy Hives", Default = false })
--@1 
RightGroupbox3:AddInput("HiveReserve", { Text = "Keep Cash Reserve", Default = "0", Finished = true, Numeric = true })
--@1 
RightGroupbox3:AddSlider("HiveDelay", { Text = "Loop Delay", Default = 1, Max = 10, Min = 0.2, Rounding = 1 })
--@1 
local LeftGroupbox8 = Tab3:AddLeftGroupbox("Auto Delete Bees", "trash-2")
--@1 
LeftGroupbox8:AddToggle("AutoDelete", { Text = "Auto Delete Bees", Default = false })
--@6 
LeftGroupbox8:AddDropdown("DeleteMode", {
	Text = "Delete Filter",
	Default = "Below Minimum Rarity",
	Multi = false,
	Values = { "Below Minimum Rarity", "Selected Rarities", "Selected Bees", "Selected Mutations" }
})
--@1 
LeftGroupbox8:AddDropdown("DeleteMinRarity", { Text = "Delete Below Rarity", Default = "Uncommon", Multi = false, Values = { v.Name } })
--@11 
LeftGroupbox8:AddDropdown("DeleteRarities", {
	Text = "Rarities To Delete",
	Default = {},
	Multi = true,
	Values = { v.Name },
	Callback = function(arg9, arg10)
		--@2 
		for k3, v6 in arg9 do
		end
	end
})
--@11 
LeftGroupbox8:AddDropdown("DeleteBees", {
	Text = "Bees To Delete",
	Default = {},
	Multi = true,
	Values = { v2.Name },
	Callback = function(arg11, arg12)
		--@2 
		for k4, v7 in arg11 do
		end
	end
})
--@11 
LeftGroupbox8:AddDropdown("DeleteMutations", {
	Text = "Mutations To Delete",
	Default = {},
	Multi = true,
	Values = { tostring(v3) },
	Callback = function(arg13, arg14)
		--@2 
		for k5, v8 in arg13 do
		end
	end
})
--@1 
local RightGroupbox4 = Tab3:AddRightGroupbox("Filters & Safety", "shield")
--@1 
RightGroupbox4:AddToggle("DeleteProtectMutated", { Text = "Protect Mutated Bees", Default = true })
--@1 
RightGroupbox4:AddSlider("DeleteKeepPerType", { Text = "Keep Per Bee Type", Default = 0, Max = 50, Min = 0, Rounding = 0 })
--@1 
RightGroupbox4:AddSlider("DeleteMaxPerCycle", { Text = "Max Deletes Per Cycle", Default = 10, Max = 100, Min = 1, Rounding = 0 })
--@1 
RightGroupbox4:AddSlider("DeleteActionDelay", { Text = "Delete Delay", Default = 0.3, Max = 2, Min = 0.1, Rounding = 2 })
--@1 
RightGroupbox4:AddSlider("DeleteLoopDelay", { Text = "Loop Delay", Default = 3, Max = 30, Min = 0.5, Rounding = 1 })
--@1 
RightGroupbox4:AddToggle("DeleteNotify", { Text = "Notify On Delete", Default = false })
--@15 
RightGroupbox4:AddButton({
	Text = "Count Matching Bees",
	Func = function(arg15, arg16)
		--@1 
		local result4 = module.client:get("OwnedBees")
		--@2 
		for k6, v9 in pairs(result4) do
		end
		--@2 
		for k7, v10 in pairs(result4) do
		end
		--@1 
		result:Notify("0 bee(s) match")
	end
})
--@18 
RightGroupbox4:AddButton({
	Text = "Delete Matching Now",
	Func = function(arg17, arg18)
		--@12 
		task.spawn(function(...)
			--@1 
			local result5 = module.client:get("OwnedBees")
			--@2 
			for k8, v11 in pairs(result5) do
			end
			--@2 
			for k9, v12 in pairs(result5) do
			end
			--@1 
			result:Notify("Deleted 0 bee(s)")
		end)
	end
})
--@1 
local LeftGroupbox9 = Tab4:AddLeftGroupbox("Menu", "wrench")
--@1 
local Label = LeftGroupbox9:AddLabel("UI Keybind")
--@1 
Label:AddKeyPicker("MenuKeybind", { Text = "UI Keybind", Default = "RightShift", NoUI = true })
--@14 
LeftGroupbox9:AddToggle("AntiAfk", {
	Text = "Anti-AFK",
	Default = true,
	Callback = function(state, arg20)
		if state then
			local connection = Players.LocalPlayer.Idled:Connect(function(idleTime)
				VirtualUser:CaptureController()
				VirtualUser:ClickButton2(Vector2.new())
			end)
		else
			connection:Disconnect()
		end
	end
})
--@4 
LeftGroupbox9:AddButton("Unload", function(arg21, arg22)
	--@1 
	result:Unload()
end)
--@1 
result.ToggleKeybind = result.Options.MenuKeybind
--@4 
result:OnUnload(function(arg23, arg24)
	--@1 
	print("Grow a Beehive unloaded")
end)
--@1 
result2:SetLibrary(result)
--@1 
result2:SetFolder("OuroborosHub")
--@1 
result2:SaveDefault("Mint")
--@1 
result3:SetLibrary(result)
--@1 
result3:IgnoreThemeSettings()
--@1 
result3:SetIgnoreIndexes({ "MenuKeybind" })
--@1 
result3:SetFolder("OuroborosHub/GrowABeehive")
--@1 
result3:BuildConfigSection(Tab4)
--@1 
result2:ApplyToTab(Tab4)
--@1 
result2:LoadDefault()
--@1 
result3:LoadAutoloadConfig()
--@2 
task.spawn(function(...)
end)
--@2 
task.spawn(function(...)
end)
--@2 
task.spawn(function(...)
end)
--@2 
task.spawn(function(...)
end)
--@2 
task.spawn(function(...)
end)
--@2 
task.spawn(function(...)
end)
--@2 
task.spawn(function(...)
end)
--@1 
result:Notify("Grow a Beehive loaded")
