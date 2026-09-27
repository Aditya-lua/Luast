print("game:", game.Name, "place", game.PlaceId)
local p = Instance.new("Part")
p.Name = "TestPart"
p.Parent = workspace
print("part parent:", p.Parent.Name, typeof(p), p:IsA("BasePart"))
print("vector:", tostring(Vector3.new(1,2,3) * 2), (Vector3.new(3,4,0)).Magnitude)
print("services:", game:GetService("Players").Name, game:GetService("TweenService").Name)
print("task:", task.wait ~= nil, typeof(task.spawn(function() end)))
print("http:", type(game.HttpGet))
