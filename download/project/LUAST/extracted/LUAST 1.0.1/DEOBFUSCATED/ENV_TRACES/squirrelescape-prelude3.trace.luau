-- run status: script error: prelude:9: attempt to call a nil value
--   /tmp/luau-recover-env-_3zwdrtt/harness.luau:16537
--   prelude:9 function new
--   Script:3375
--   /tmp/luau-recover-env-_3zwdrtt/harness.luau:16542
--   
-- 2 statements recorded in 0.66s
-- non-standard globals touched: al, c2, bC

--@1 
local HttpService = game:GetService("HttpService")
--@1 
HttpService:JSONDecode("{\"m\":null,\"t\":\"buffer\",\"zbase64\":\"KLUv/SAdvQAA0sEEjhIREAABAAAAA4De+fFCMrSLMAA=\"}")
