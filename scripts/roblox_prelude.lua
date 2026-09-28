-- Pure-Lua Roblox shims for running LUAST output under the luau CLI
-- (semantics mirror documented Roblox behaviour; enum values from Roblox API refs)

local function enumitem(etype, name, value)
    return setmetatable({}, {
        __index = function(t, k)
            if k == "Name" then return name
            elseif k == "Value" then return value
            elseif k == "EnumType" then return etype end
        end,
        __tostring = function() return "Enum." .. etype .. "." .. name end,
    })
end

local function enumtype(name, items)
    local t = {}
    for k, v in pairs(items) do
        t[k] = enumitem(name, k, v)
    end
    return setmetatable(t, {
        __index = function() return nil end,
        __tostring = function() return "Enum." .. name end,
    })
end

Enum = enumtype("_root", {})
local EnumMt = getmetatable(Enum)
EnumMt.__index = function(t, k)
    if k == "FontWeight" then
        return enumtype("FontWeight", {
            Thin = 100, ExtraLight = 200, Light = 300, Regular = 400,
            Medium = 500, SemiBold = 600, Bold = 700, ExtraBold = 800,
            Black = 900, Heavy = 1000,
        })
    elseif k == "FontStyle" then
        return enumtype("FontStyle", { Normal = 0, Italic = 1 })
    elseif k == "Material" then
        return enumtype("Material", {
            Plastic = 256, SmoothPlastic = 272, Neon = 288, Wood = 512,
            Grass = 1280, Brick = 848, Metal = 1088, DiamondPlate = 1056,
            Slate = 800, Concrete = 816, Ice = 1536, Glass = 1568,
            ForceField = 1584, WoodPlanks = 528, Pebble = 864,
            Sand = 1296, Fabric = 1312, Granite = 832, Marble = 784,
        })
    end
    return nil
end

local V2mt
V2mt = {
    __mul = function(a, b)
        if type(a) == "number" then
            return setmetatable({ X = a * b.X, Y = a * b.Y }, V2mt)
        elseif type(b) == "number" then
            return setmetatable({ X = a.X * b, Y = a.Y * b }, V2mt)
        end
        return setmetatable({ X = a.X * b.X, Y = a.Y * b.Y }, V2mt)
    end,
    __add = function(a, b)
        if type(b) == "number" then
            return setmetatable({ X = a.X + b, Y = a.Y + b }, V2mt)
        end
        return setmetatable({ X = a.X + b.X, Y = a.Y + b.Y }, V2mt)
    end,
    __index = function(t, k)
        if k == "X" or k == "Y" then return rawget(t, k) end
        return nil
    end,
    __tostring = function(t) return t.X .. ", " .. t.Y end,
}
Vector2int16 = {
    new = function(x, y)
        return setmetatable({ X = x, Y = y }, V2mt)
    end,
}

local function fontobj(family, weight, style)
    local f = {}
    local mt = {
        __index = function(t, k)
            if k == "Family" then return family
            elseif k == "Weight" then return weight
            elseif k == "Style" then return style
            elseif k == "Bold" then return weight.Name == "Bold" end
        end,
        __tostring = function() return "Font" end,
    }
    return setmetatable(f, mt)
end

Font = {
    fromName = function(name, weight, style)
        return fontobj(name, weight or Enum.FontWeight.Regular,
                       style or Enum.FontStyle.Normal)
    end,
    new = function(family, weight, style)
        return fontobj(family, weight or Enum.FontWeight.Regular,
                       style or Enum.FontStyle.Normal)
    end,
}

local HttpService = {}
function HttpService.JSONDecode(self, s)
    -- minimal decode of the fixed-shape JSON used by the pool
    local t = {}
    t.m = nil
    t.t = string.match(s, '"t":"(.-)"')
    t.zbase64 = string.match(s, '"zbase64":"(.-)"')
    return t
end

game = {}
function game.GetService(self, name)
    if name == "HttpService" then return HttpService end
    return {}
end
function game.FindService(self, name)
    return nil
end

workspace = {
    CurrentCamera = {},
    Raycast = function() return nil end,
}
