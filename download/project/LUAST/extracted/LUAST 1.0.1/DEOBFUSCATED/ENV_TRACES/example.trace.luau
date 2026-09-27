-- run status: finished
-- 12 statements recorded in 0.03s

--@1 
print("game:", "Game", "place", 0)
--@1 
local Part = Instance.new("Part")
--@1 
Part.Name = "TestPart"
--@1 
Part.Parent = workspace
--@1 
print("part parent:", "Workspace", "Instance", true)
--@1 
print("vector:", "2, 4, 6", 5)
--@1 
local Players = game:GetService("Players")
--@1 
local TweenService = game:GetService("TweenService")
--@1 
print("services:", "Players", "TweenService")
--@2 
task.spawn(function(...)
end)
--@1 
print("task:", true, "thread")
--@1 
print("http:", "function")
