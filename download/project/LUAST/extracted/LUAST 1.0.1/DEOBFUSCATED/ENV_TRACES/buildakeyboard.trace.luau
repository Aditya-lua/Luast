-- run status: script error: Script:2: invalid argument #2 to 'bxor' (number expected, got userdata)
--   /tmp/luau-recover-env-g2kjv_we/harness.luau:5337
--   Script:2
--   /tmp/luau-recover-env-g2kjv_we/harness.luau:5342
--   
-- 18 statements recorded in 0.05s

--@1 
local Players = game:GetService("Players")
--@1 
local ReplicatedStorage = game:GetService("ReplicatedStorage")
--@1 
local ReplicatedFirst = game:GetService("ReplicatedFirst")
--@1 
local VirtualUser = game:GetService("VirtualUser")
--@4 
getgenv().gethui = function(arg, arg2)
	--@1 
	Players.LocalPlayer:WaitForChild("PlayerGui")
end
--@1 
local Shared = ReplicatedStorage:WaitForChild("Shared")
--@1 
local Remotes = Shared:WaitForChild("Remotes")
--@1 
local NetworkShared = Remotes:WaitForChild("NetworkShared")
--@1 
require(NetworkShared)
--@1 
local Configurations = ReplicatedStorage:WaitForChild("Configurations")
--@1 
local Gameplay = Configurations:WaitForChild("Gameplay")
--@1 
local UpgradesConfiguration = Gameplay:WaitForChild("UpgradesConfiguration")
--@1 
require(UpgradesConfiguration)
--@1 
local WorkerConfiguration = Gameplay:WaitForChild("WorkerConfiguration")
--@1 
require(WorkerConfiguration)
--@1 
local RebirthConfiguration = Gameplay:WaitForChild("RebirthConfiguration")
--@1 
require(RebirthConfiguration)
