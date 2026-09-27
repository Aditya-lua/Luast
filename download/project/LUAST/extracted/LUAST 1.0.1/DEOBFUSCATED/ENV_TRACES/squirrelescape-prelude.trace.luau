-- run status: script error: Script:10180: invalid argument #1 to 'rshift' (number expected, got userdata)
--   /tmp/luau-recover-env-6aqv0zaj/harness.luau:16536
--   Script:10180
--   Script:10184
--   /tmp/luau-recover-env-6aqv0zaj/harness.luau:16541
--   
-- 2 statements recorded in 0.70s
-- non-standard globals touched: al, c2, bC

--@1 
local HttpService = game:GetService("HttpService")
--@1 
HttpService:JSONDecode("{\"m\":null,\"t\":\"buffer\",\"zbase64\":\"KLUv/SAdvQAA0sEEjhIREAABAAAAA4De+fFCMrSLMAA=\"}")
