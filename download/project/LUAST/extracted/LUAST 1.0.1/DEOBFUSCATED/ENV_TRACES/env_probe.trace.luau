-- run status: finished
-- 7 statements recorded in 0.03s

--@1 
print("vector", (Vector2int16.new(0, 479) * 469).X, (Vector2int16.new(0, 479) * 469).Y, (Vector2int16.new(0, 479) * 469).Y)
--@1 
local HttpService = game:GetService("HttpService")
--@1 
local data = HttpService:JSONDecode("{\"m\":null,\"t\":\"buffer\",\"zbase64\":\"KLUv/SAdvQAA0sEEjhIREAABAAAAA4De+fFCMrSLMAA=\"}")
--@1 
print("json", true, "userdata", data)
--@1 
print("readu32", false, "invalid argument #1 to 'readu32' (buffer expected, got userdata)")
--@1 
print("readu32f", false, "invalid argument #1 to 'readu32' (buffer expected, got userdata)")
--@1 
print("frozen", true, true)
