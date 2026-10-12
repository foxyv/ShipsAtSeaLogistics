-- SasLogistics: read-only telemetry for the external logistics planner.
-- F8 writes snapshot.json into this mod's folder; F7 toggles writing it every AUTO_EXPORT_SECONDS.
-- F6 toggles highlighting, in any open inventory window, the cargo the planner wants loaded here.
-- Nothing here changes game state: highlighting only changes how inventory slots are drawn.

local AUTO_EXPORT_SECONDS = 20

local ScriptDir = debug.getinfo(1, "S").source:sub(2):match("^(.*)[/\\][^/\\]*$") or "."
local SnapshotPath = ScriptDir .. "\\..\\snapshot.json"

local MISSION_STATUS = {
    [0] = "NotStarted", "WaitingForDependencies", "InProgress", "Succeeded", "Failed", "Cancelled", "Suspended",
}
local VALUE_TYPES = {
    [0] = "Unknown", "Float", "Int", "Bool", "String", "Vector", "Object", "SoftObject", "SoftObjectArray",
    "Guid", "DateTime", "FloatArray", "IntArray", "StringArray", "VectorArray", "ObjectArray", "GuidArray",
    "DateTimeArray", "TagContainer",
}

---------------------------------------------------------------------------------------------------
-- JSON

local ArrayMeta = {}
local function Arr(t) return setmetatable(t or {}, ArrayMeta) end

local function EncodeString(s)
    return '"' .. s:gsub('[%c"\\]', function(c)
        if c == '"' then return '\\"' elseif c == "\\" then return "\\\\" elseif c == "\n" then return "\\n" end
        return string.format("\\u%04x", c:byte())
    end) .. '"'
end

local function Encode(v, buf)
    local t = type(v)
    if t == "boolean" then
        buf[#buf + 1] = tostring(v)
    elseif t == "number" then
        if v ~= v or v == math.huge or v == -math.huge then buf[#buf + 1] = "null"
        elseif math.type(v) == "integer" then buf[#buf + 1] = string.format("%d", v)
        else buf[#buf + 1] = string.format("%.2f", v) end
    elseif t == "string" then
        buf[#buf + 1] = EncodeString(v)
    elseif t == "table" then
        if getmetatable(v) == ArrayMeta then
            buf[#buf + 1] = "["
            for i, item in ipairs(v) do
                if i > 1 then buf[#buf + 1] = "," end
                Encode(item, buf)
            end
            buf[#buf + 1] = "]"
        else
            local keys = {}
            for k in pairs(v) do keys[#keys + 1] = tostring(k) end
            table.sort(keys)
            buf[#buf + 1] = "{"
            for i, k in ipairs(keys) do
                if i > 1 then buf[#buf + 1] = "," end
                buf[#buf + 1] = EncodeString(k) .. ":"
                Encode(v[k], buf)
            end
            buf[#buf + 1] = "}"
        end
    else
        buf[#buf + 1] = "null"
    end
end

---------------------------------------------------------------------------------------------------
-- Safe accessors: every game read goes through Try so one bad field never aborts the export.

local Errors, ErrorSeen

local function Try(fn, where)
    local ok, result = pcall(fn)
    if ok then return result end
    local message = (where or "?") .. ": " .. tostring(result):gsub("\n.*", ""):gsub("^.*Scripts\\", "")
    if not ErrorSeen[message] and #Errors < 60 then
        ErrorSeen[message] = true
        Errors[#Errors + 1] = message
    end
    return nil
end

local function IsObject(obj)
    return obj ~= nil and type(obj) == "userdata" and pcall(function() return obj:IsValid() end) and obj:IsValid()
end

local function FullName(obj)
    if not IsObject(obj) then return nil end
    return Try(function() return obj:GetFullName() end, "FullName")
end

-- "Port /Game/ShipsAtSea/Ports/Port_Fenes.Port_Fenes" -> "Port_Fenes"
local function ShortName(obj)
    local full = FullName(obj)
    return full and full:match("([^%.:/ ]+)$") or nil
end

local function ClassName(obj)
    if not IsObject(obj) then return nil end
    return Try(function() return obj:GetClass():GetFName():ToString() end, "ClassName")
end

local function GuidStr(g)
    return string.format("%08X%08X%08X%08X", g.A & 0xFFFFFFFF, g.B & 0xFFFFFFFF, g.C & 0xFFFFFFFF, g.D & 0xFFFFFFFF)
end

local function Vec(v)
    if v == nil then return nil end
    return Try(function() return Arr({ v.X + 0.0, v.Y + 0.0, v.Z + 0.0 }) end, "Vec")
end

local function ActorLocation(actor)
    if not IsObject(actor) then return nil end
    return Vec(Try(function() return actor:K2_GetActorLocation() end, "ActorLocation"))
end

local function Unwrap(element)
    if type(element) == "userdata" then
        local ok, inner = pcall(function() return element:get() end)
        if ok and inner ~= nil then return inner end
    end
    return element
end

-- Iterates a TArray wrapper or a plain Lua table the same way.
local function Each(array, fn)
    if type(array) == "table" then
        for _, element in ipairs(array) do fn(Unwrap(element)) end
    elseif array ~= nil then
        array:ForEach(function(_, element) fn(element:get()) end)
    end
end

---------------------------------------------------------------------------------------------------
-- Missions

local function ContextValue(value)
    local kind = VALUE_TYPES[value.Type]
    if kind == "Float" then return value.FloatValue + 0.0
    elseif kind == "Int" then return value.IntValue
    elseif kind == "Bool" then return value.BoolValue
    elseif kind == "String" then return value.StringValue:ToString()
    elseif kind == "Vector" then return Vec(value.VectorValue)
    elseif kind == "Object" then return ShortName(value.ObjectValue)
    elseif kind == "Guid" then return GuidStr(value.GuidValue)
    elseif kind == "ObjectArray" then
        local list = Arr()
        Each(value.ObjectArray, function(o) list[#list + 1] = ShortName(o) end)
        return list
    elseif kind == "GuidArray" then
        local list = Arr()
        Each(value.GuidArray, function(g) list[#list + 1] = GuidStr(g) end)
        return list
    elseif kind == "IntArray" then
        local list = Arr()
        Each(value.IntArray, function(i) list[#list + 1] = i end)
        return list
    end
    return nil
end

local function ExportContext(context, into, where)
    Try(function()
        Each(context.Data, function(pair)
            local tag = pair.Tag.TagName:ToString()
            local value = Try(function() return ContextValue(pair.Value) end, where .. " " .. tag)
            if value ~= nil then into[tag] = value end
        end)
    end, where)
end

local function ExportRewards(descriptor)
    local list = Arr()
    Try(function()
        local out = {}
        descriptor:GetPotentialRewards(out)
        Each(out.OutRewards or out, function(reward)
            list[#list + 1] = {
                stage = reward.RewardsDescription:ToString(),
                type = ClassName(reward.RewardType),
                amount = reward.amount,
            }
        end)
    end, "GetPotentialRewards")
    return list
end

local function ExportMissions()
    local list = Arr()
    for _, mission in ipairs(FindAllOf("SasMission") or {}) do
        local full = FullName(mission)
        if full and not full:find("Default__", 1, true) then
            local descriptor = Try(function() return mission.MissionDescriptor end, "MissionDescriptor")
            local entry = {
                path = full,
                class = ClassName(mission),
                status = MISSION_STATUS[Try(function() return mission.CurrentMissionStatus end, "status")],
                context = {},
                rewards = Arr(),
            }
            ExportContext(mission.MissionDataContext, entry.context, "missionContext")
            if IsObject(descriptor) then
                entry.title = Try(function() return descriptor:GetMissionTitle():ToString() end, "title")
                entry.asset = ShortName(Try(function() return descriptor:GetMissionDataAsset() end, "asset"))
                ExportContext(descriptor.MissionDescriptorDataContext, entry.context, "descriptorContext")
                entry.rewards = ExportRewards(descriptor)
            end
            list[#list + 1] = entry
        end
    end
    return list
end

---------------------------------------------------------------------------------------------------
-- Cargo containers: port warehouses, dock cargo areas and boat cargo areas

local Footprints  -- item definition name -> grid cells it occupies, filled during an export

local function IntVec(v)
    return Arr({ v.X, v.Y, v.Z })
end

local function RecordFootprint(definition)
    local name = ShortName(definition)
    if not name or Footprints[name] then return end
    Footprints[name] = Arr()
    Try(function()
        Each(definition.Fragments, function(fragment)
            local cells = Try(function() return fragment.CellPositions end)
            if cells ~= nil and #Footprints[name] == 0 then
                Try(function() Each(cells, function(cell) table.insert(Footprints[name], IntVec(cell)) end) end, "footprint cells")
            end
        end)
    end, "footprint")
end

local function ExportRuleList(rules, into)
    Each(rules, function(rule)
        local test = Try(function() return rule.Test end)
        local row = { test = ClassName(test), filter = ClassName(Try(function() return rule.Filter end)),
                      expected = Try(function() return rule.ExpectedTestResult end) }
        row.maxWeight = Try(function() return test.MaxWeightSource.Value + 0.0 end)
        row.maxWeightSource = ClassName(Try(function() return test.MaxWeightSource end))
        row.maxCapacity = Try(function() return test.MaxCapacitySource.Value + 0.0 end)
        into[#into + 1] = row
    end)
end

local function ExportRules(rulesData)
    local list = Arr()
    if not IsObject(rulesData) then return list end
    local function entries(array)
        Each(array, function(ruleEntry)
            Try(function() ExportRuleList(ruleEntry.Rules, list) end, "rules")
            Try(function() Each(ruleEntry.RuleSets, function(set) ExportRuleList(set.Rules, list) end) end, "rule sets")
        end)
    end
    Try(function() entries(rulesData.Entries) end, "rule entries")
    Try(function() Each(rulesData.RulesEntrySets, function(set) entries(set.Entries) end) end, "rule entry sets")
    return list
end

-- Grid layout of a boat hold: which cells are taken, where each item sits, and the limits that apply.
local function ExportGridDetail(component, inventory, entry)
    entry.weight = Try(function() return inventory.InventoryWeight + 0.0 end, "inventory weight")
    entry.sizes = Arr()
    Try(function()
        inventory.SupportedCargoSizes:ForEach(function(key, value)
            if value:get() then table.insert(entry.sizes, key:get()) end
        end)
    end, "supported sizes")
    entry.occupied = Arr()
    Try(function()
        inventory.CellsOccupancyMap:ForEach(function(key) table.insert(entry.occupied, IntVec(key:get())) end)
    end, "occupancy")
    entry.placements = Arr()
    Try(function()
        Each(inventory.GridInventoryItems.Entries, function(placed)
            table.insert(entry.placements, {
                guid = GuidStr(placed.InventoryItem.Guid),
                position = IntVec(placed.GridTransform.position),
                yaw = placed.GridTransform.Rotation.Yaw + 0.0,
                pitch = placed.GridTransform.Rotation.Pitch + 0.0,
                roll = placed.GridTransform.Rotation.Roll + 0.0,
            })
        end)
    end, "placements")
    entry.rules = ExportRules(Try(function() return inventory.RulesData end))
    entry.componentRules = ExportRules(Try(function() return component.RulesData end))
end

local function ExportItems(component, entry, detailed)
    entry.items = Arr()
    Try(function()
        component.Inventories:ForEach(function(_, value)
            local inventory = value:get()
            local grid = Try(function()
                local size = inventory.GridSize
                return Arr({ size.X, size.Y, size.Z })
            end)
            if grid then entry.gridSize = grid end
            if detailed and grid then ExportGridDetail(component, inventory, entry) end
            inventory.RegisteredInventoryItems:ForEach(function(key)
                local item = key:get()
                local size = Try(function() return item.ItemDefinition.CargoSize end)
                Try(function() RecordFootprint(item.ItemDefinition) end, "RecordFootprint")
                entry.items[#entry.items + 1] = {
                    guid = Try(function() return GuidStr(item.Guid) end, "item guid"),
                    name = Try(function() return item:GetDisplayName():ToString() end, "item name"),
                    def = ShortName(Try(function() return item.ItemDefinition end, "item def")),
                    weight = Try(function() return item:GetItemWeight() + 0.0 end, "item weight"),
                    count = Try(function() return item.ItemCount end, "item count"),
                    size = type(size) == "number" and size or nil,
                }
            end)
        end)
    end, "ExportItems")
end

local function ExportContainers()
    local list = Arr()
    for _, proxy in ipairs(FindAllOf("WarehouseProxy") or {}) do
        local full = FullName(proxy)
        if full and not full:find("Default__", 1, true) then
            local entry = { kind = "warehouse", path = full, port = ShortName(Try(function() return proxy.Port end, "proxy port")) }
            ExportItems(proxy.BasicInventoryComponent, entry)
            list[#list + 1] = entry
        end
    end
    for _, area in ipairs(FindAllOf("CargoArea") or {}) do
        local full = FullName(area)
        if full and not full:find("Default__", 1, true) then
            local onBoat = (ClassName(area) or ""):find("BoatCargoArea", 1, true) ~= nil
            local entry = {
                kind = onBoat and "boatCargoArea" or "cargoArea",
                class = ClassName(area),
                path = full,
                port = ShortName(Try(function() return area.Port end, "area port")),
                location = ActorLocation(area),
            }
            ExportItems(area.BasicInventoryComponent, entry, onBoat)
            if onBoat or #entry.items > 0 then list[#list + 1] = entry end
        end
    end
    return list
end

---------------------------------------------------------------------------------------------------
-- Ports, boats, player

local function ExportPorts()
    local list = Arr()
    local subsystem = FindFirstOf("PortSubsystem")
    for _, port in ipairs(FindAllOf("Port") or {}) do
        local full = FullName(port)
        if full and not full:find("Default__", 1, true) then
            list[#list + 1] = {
                id = ShortName(port),
                name = Try(function() return port.PortName:ToString() end, "port name"),
                location = IsObject(subsystem) and Vec(Try(function() return subsystem:GetPortLocation(port) end, "GetPortLocation")) or nil,
            }
        end
    end
    return list
end

local function ExportBoats()
    local owned = {}
    for _, component in ipairs(FindAllOf("CompanyOwnedBoatsComponent") or {}) do
        Try(function()
            Each(component.ActiveBoats, function(boat)
                local full = FullName(boat)
                if full then owned[full] = true end
            end)
        end, "ActiveBoats")
    end
    local list = Arr()
    for _, boat in ipairs(FindAllOf("Boat") or {}) do
        local full = FullName(boat)
        if full and not full:find("Default__", 1, true) then
            local info = Try(function() return boat:GetBoatInfo() end, "GetBoatInfo")
            local entry = { path = full, class = ClassName(boat), owned = owned[full] == true, location = ActorLocation(boat) }
            if IsObject(info) then
                entry.info = ShortName(info)
                entry.boatName = Try(function() return info.BoatName:ToString() end, "boat name")
                entry.payload = Try(function() return info.Payload + 0.0 end, "payload")
                entry.maxSpeed = Try(function() return info.MaxSpeed + 0.0 end, "max speed")
                entry.grossTonnage = Try(function() return info.GrossTonnage + 0.0 end, "tonnage")
                entry.storageType = Try(function() return info.StorageType:ToString() end, "storage type")
                entry.tier = ShortName(Try(function() return info.Tier end, "tier"))
            end
            list[#list + 1] = entry
        end
    end
    return list
end

local function ExportPlayer()
    local controller = FindFirstOf("PlayerController")
    if not IsObject(controller) then return nil end
    local pawn = Try(function() return controller.Pawn end, "player pawn")
    return { pawn = FullName(pawn), location = ActorLocation(pawn) }
end

---------------------------------------------------------------------------------------------------

local function Export(reason)
    Errors, ErrorSeen, Footprints = Arr(), {}, {}
    local snapshot = { version = 1, time = os.date("%Y-%m-%dT%H:%M:%S"), reason = reason, footprints = Footprints }
    snapshot.cellSize = Try(function()
        return StaticFindObject("/Script/Sas.Default__SasInventorySettings").GridInventoryCellSize + 0.0
    end, "cell size")
    for name, section in pairs({
        missions = ExportMissions, containers = ExportContainers, ports = ExportPorts,
        boats = ExportBoats, player = ExportPlayer,
    }) do
        snapshot[name] = Try(section, name)
    end
    -- Outside a loaded save there is nothing to report; keep the last good snapshot.
    if not snapshot.missions or not snapshot.ports or #snapshot.ports == 0 then return false end
    snapshot.errors = Errors

    local buf = {}
    Encode(snapshot, buf)
    local tmp = SnapshotPath .. ".tmp"
    local file, err = io.open(tmp, "w")
    if not file then
        print("[SasLogistics] could not write " .. tmp .. ": " .. tostring(err) .. "\n")
        return false
    end
    file:write(table.concat(buf))
    file:close()
    os.remove(SnapshotPath)
    os.rename(tmp, SnapshotPath)
    if reason == "manual" then
        print(string.format("[SasLogistics] snapshot: %d missions, %d containers, %d ports, %d read errors\n",
            #snapshot.missions, #(snapshot.containers or {}), #snapshot.ports, #Errors))
    end
    return true
end

-- Key presses only set flags. All the work happens in one timer on the game thread (see the end of
-- this file), so the mod's Lua never runs on two threads at once.
local AutoExport = false
local ExportRequested = false

RegisterKeyBind(Key.F8, function()
    ExportRequested = true
end)

RegisterKeyBind(Key.F7, function()
    AutoExport = not AutoExport
    print("[SasLogistics] auto export " .. (AutoExport and ("on, every " .. AUTO_EXPORT_SECONDS .. " s") or "off") .. "\n")
end)

---------------------------------------------------------------------------------------------------
-- Highlighting. The planner writes the GUIDs of the cargo to load at this port into highlight.txt,
-- one per line. In an inventory window showing any of them, those slots are tinted green and the
-- rest are dimmed. Windows showing none of them are left alone.

local TICK_MS = 1000
local HighlightPath = ScriptDir .. "\\..\\highlight.txt"
local HighlightOn = true
local Touched = false  -- has any slot's look been changed since everything was last put back
local LastReport = ""

local GREEN = { R = 0.25, G = 1.0, B = 0.25, A = 1.0 }
local WHITE = { R = 1.0, G = 1.0, B = 1.0, A = 1.0 }

local function ReadHighlights()
    local wanted, count = {}, 0
    local file = io.open(HighlightPath, "r")
    if not file then return wanted, 0 end
    for line in file:lines() do
        local guid = line:match("^(%x+)%s*$")
        if guid and #guid == 32 then
            wanted[guid:upper()] = true
            count = count + 1
        end
    end
    file:close()
    return wanted, count
end

local function SlotGuid(slot)
    local ok, guid = pcall(function()
        local item = slot.ViewModelData.Item
        if not item:IsValid() then return nil end
        return GuidStr(item.Guid)
    end)
    return ok and guid or nil
end

local PaintErrors = {}  -- each way of restyling a slot reports its first failure to the UE4SS log, once

local function Attempt(label, fn)
    local ok, err = pcall(fn)
    if not ok and not PaintErrors[label] then
        PaintErrors[label] = true
        print("[SasLogistics] highlight step '" .. label .. "' failed: " .. tostring(err):gsub("\n.*", "") .. "\n")
    end
    return ok
end

-- Several independent ways of marking a slot, so that one the game's UI ignores does not hide the rest.
local function Paint(slot, colour, opacity)
    Attempt("widget tint", function() slot:SetColorAndOpacity(colour) end)
    Attempt("icon tint", function() slot.Icon:SetColorAndOpacity(colour) end)
    Attempt("opacity", function() slot:SetRenderOpacity(opacity) end)
end

local function UpdateHighlights()
    local wanted, count = {}, 0
    if HighlightOn then wanted, count = ReadHighlights() end
    if count == 0 and not Touched then return end
    local wasTouched = Touched

    local windows, shown = {}, 0  -- slots grouped by the inventory window they belong to
    for _, slot in ipairs(FindAllOf("WBP_ItemInventorySlot_C") or {}) do
        local name = IsObject(slot) and slot:GetFullName() or nil
        if name and not name:find("Default__", 1, true) then
            local owner = "?"
            pcall(function() owner = slot.WBP_ItemInventory:GetFullName() end)
            local guid = SlotGuid(slot)
            local window = windows[owner] or { slots = {}, hit = false }
            windows[owner] = window
            window.slots[#window.slots + 1] = { slot, guid }
            if guid and wanted[guid] then
                window.hit = true
                shown = shown + 1
            end
        end
    end
    -- Slots are only restyled while something wanted is on screen, plus once more to put them back.
    if shown > 0 or wasTouched then
        for _, window in pairs(windows) do
            for _, pair in ipairs(window.slots) do
                local slot, guid = pair[1], pair[2]
                if window.hit and guid and wanted[guid] then
                    Paint(slot, GREEN, 1.0)
                elseif window.hit and guid then
                    Paint(slot, WHITE, 0.3)
                else
                    Paint(slot, WHITE, 1.0)
                end
            end
        end
    end
    Touched = shown > 0
    local report = string.format("%d wanted, %d visible in open inventories", count, shown)
    if report ~= LastReport then
        LastReport = report
        print("[SasLogistics] highlight: " .. report .. "\n")
    end
end

RegisterKeyBind(Key.F6, function()
    HighlightOn = not HighlightOn
    print("[SasLogistics] highlighting " .. (HighlightOn and "on" or "off") .. "\n")
end)

---------------------------------------------------------------------------------------------------
-- The one timer. It runs on the game thread, which is the only place game objects may be touched.
-- An earlier version used LoopAsync plus ExecuteInGameThread from two background timers; those raced
-- on the Lua state and brought the game down.

local Ticks = 0

local function Tick()
    Ticks = Ticks + 1
    if ExportRequested then
        ExportRequested = false
        pcall(Export, "manual")
    elseif AutoExport and Ticks % AUTO_EXPORT_SECONDS == 0 then
        pcall(Export, "auto")
    end
    pcall(UpdateHighlights)
    return false
end

if LoopInGameThreadWithDelay then
    LoopInGameThreadWithDelay(TICK_MS, Tick)
else
    -- Older UE4SS builds: a single background timer that hands each tick to the game thread.
    LoopAsync(TICK_MS, function()
        ExecuteInGameThread(Tick)
        return false
    end)
end

print("[SasLogistics] loaded: F8 = write snapshot, F7 = toggle auto export, F6 = toggle highlighting\n")

