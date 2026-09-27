-- run status: script error: Script:10180: invalid argument #1 to 'rshift' (number expected, got userdata)
--   /tmp/luau-recover-env-ke9zhq8e/harness.luau:16525
--   Script:10180
--   Script:10184
--   /tmp/luau-recover-env-ke9zhq8e/harness.luau:16530
--   
-- 2 statements recorded in 0.75s
-- non-standard globals touched: al, c2, bC

--@1 
local HttpService = game:GetService("HttpService")
--@1 
HttpService:JSONDecode("{\"m\":null,\"t\":\"buffer\",\"zbase64\":\"KLUv/SAdvQAA0sEEjhIREAABAAAAA4De+fFCMrSLMAA=\"}")
