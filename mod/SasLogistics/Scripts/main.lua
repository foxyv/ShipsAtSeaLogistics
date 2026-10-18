-- SasLogistics: read-only telemetry for the external logistics planner.
-- F8 writes snapshot.json into this mod's folder; F7 toggles writing it every AUTO_EXPORT_SECONDS,
-- which is on from the start of the game.
-- F6 toggles highlighting, in any open inventory window, the cargo the planner wants loaded here.
-- Nothing here changes the game's own state. Highlighting changes how inventory slots are drawn and
-- puts the game's in-world marker over cargo to unload; those markers are this mod's own and are
-- removed again when the cargo comes off, when highlighting is switched off, and on reload.


-- Which copy of this script is running. The log says a release name and a checksum of the file itself
-- (tools/check_lua.py --running compares it with the repository's), so there is never a doubt whether the
-- game loaded the version that was meant to be deployed.
SCRIPT_VERSION = "2026-10-18.1"
function ScriptChecksum()
    local file = io.open(debug.getinfo(1, "S").source:sub(2), "rb")
    if not file then return "unreadable", 0 end
    local text = file:read("*a")
    file:close()
    local hash = 2166136261  -- FNV-1a, 32 bits
    for i = 1, #text do hash = ((hash ~ text:byte(i)) * 16777619) & 0xFFFFFFFF end
    return string.format("%08X", hash), #text
end
do
    local checksum, bytes = ScriptChecksum()
    print("[SasLogistics] version " .. SCRIPT_VERSION .. ", file checksum " .. checksum .. " (" .. bytes .. " bytes)\n")
end

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
    if ErrorSeen and not ErrorSeen[message] and #Errors < 60 then
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
            -- A deadline is a calendar-wait objective in the mission's graph: it ends the mission when the
            -- calendar reaches its target time. Its target is a UE date time, in ticks (100 ns units).
            entry.objectives = Arr()
            Try(function()
                Each(mission.MissionObjectives, function(objective)
                    local class = ClassName(objective)
                    entry.objectives[#entry.objectives + 1] = class
                    if class and class:find("CalendarWait", 1, true) then
                        local ticks = Try(function() return objective.TargetDateTime.Ticks end, "deadline")
                        if ticks then entry.deadlineTicks = tostring(ticks) end  -- a string: too big for a float
                    end
                end)
            end, "objectives")
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

-- Every dock cargo area with its port and position, empty or not: a boat lying at a port's far dock
-- (the large-cargo docks, say) can be far from the port's own position but is beside one of these.
local function ExportDocks()
    local list = Arr()
    for _, area in ipairs(FindAllOf("CargoArea") or {}) do
        local full = FullName(area)
        if full and not full:find("Default__", 1, true) and not (ClassName(area) or ""):find("BoatCargoArea", 1, true) then
            local port = ShortName(Try(function() return area.Port end, "dock port"))
            if port then
                list[#list + 1] = { port = port, class = ClassName(area), location = ActorLocation(area) }
            end
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
-- Money. The game keeps a ledger of the company's transactions (a date, an amount and a description, such as
-- a mission paid out) and the capital. An amount is a FDecimal, a whole number of the game's smallest unit;
-- the capital is also given as the game's own conversion to a number, so that the planner can tell the unit.

local function DecimalRaw(decimal)
    return Try(function() return string.format("%d", decimal.Value) end, "decimal")
end

local function ExportFinance()
    local best, bestSize = nil, -1
    for _, comp in ipairs(FindAllOf("FinanceComponent") or {}) do
        if IsObject(comp) and not (FullName(comp) or ""):find("Default__", 1, true) then
            local size = 0
            Try(function() Each(comp.Ledger.Transactions, function() size = size + 1 end) end, "ledger size")
            local owner = ClassName(Try(function() return comp:GetOwner() end, "finance owner")) or ""
            -- the player's company: a player state or controller owns it; failing that, the longest ledger
            if owner:find("Player", 1, true) then size = size + 1000000 end
            if size > bestSize then best, bestSize = comp, size end
        end
    end
    if not best then return nil end
    local finance = {
        owner = ClassName(Try(function() return best:GetOwner() end, "finance owner")),
        historySize = Try(function() return best.Ledger.TransactionHistorySize + 0 end, "history size"),
        capitalRaw = DecimalRaw(best.Capital),
        capital = Try(function()
            local library = StaticFindObject("/Script/MiscMathUtilities.Default__DecimalMathLibrary")
            return library:Conv_DecimalToFloat(best.Capital) + 0.0
        end, "capital as a number"),
        transactions = Arr(),
    }
    Try(function()
        Each(best.Ledger.Transactions, function(tx)
            local date = tx.Date
            finance.transactions[#finance.transactions + 1] = {
                day = Try(function() return date.Day.Day + 0 end, "ledger day"),
                month = Try(function() return date.Month + 0 end, "ledger month"),  -- 0 for January
                year = Try(function() return date.Year.Year + 0 end, "ledger year"),
                raw = DecimalRaw(tx.Value),
                description = Try(function() return tx.Description:ToString() end, "ledger description"),
            }
        end)
    end, "ledger")
    return finance
end

---------------------------------------------------------------------------------------------------

local function Export(reason)
    Errors, ErrorSeen, Footprints = Arr(), {}, {}
    local snapshot = { version = 1, time = os.date("%Y-%m-%dT%H:%M:%S"), reason = reason, footprints = Footprints }
    -- The game's calendar: now in the same ticks as a mission's deadline, and how many game seconds pass
    -- in a real second, for turning sailing time into game time.
    snapshot.calendar = Try(function()
        local calendar = FindFirstOf("CalendarSubsystem")
        return {
            nowTicks = tostring(calendar:GetUniversalTime().Ticks),
            gameSpeed = calendar:GetGameSpeed() + 0.0,
        }
    end, "calendar")
    snapshot.cellSize = Try(function()
        return StaticFindObject("/Script/Sas.Default__SasInventorySettings").GridInventoryCellSize + 0.0
    end, "cell size")
    for name, section in pairs({
        missions = ExportMissions, containers = ExportContainers, ports = ExportPorts,
        boats = ExportBoats, player = ExportPlayer, docks = ExportDocks, finance = ExportFinance,
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
local AutoExport = true  -- the telemetry is on from the start; F7 turns it off and on
local ExportRequested = false

RegisterKeyBind(Key.F8, function()
    ExportRequested = true
end)

-- On-screen notices, shown with the game's own toast messages. A key press only queues the text;
-- it is sent from the game thread a moment later.
local TOAST_CHANNEL = "Channel.Toast.Simple.Info"
local Notices = {}

local function Notify(text)
    Notices[#Notices + 1] = text
end

local function SendNotices()
    if #Notices == 0 then return end
    local texts = Notices
    Notices = {}
    local library = StaticFindObject("/Script/SasGameplayMessageSystem.Default__SasToastFunctionLibrary")
    local controller = FindFirstOf("PlayerController")
    if not IsObject(library) or not IsObject(controller) then return end
    for _, text in ipairs(texts) do
        local ok, err = pcall(function()
            library:SendSimpleToast(controller, { TagName = FName(TOAST_CHANNEL) }, FText(text), controller)
        end)
        if not ok then print("[SasLogistics] notice failed: " .. tostring(err):gsub("\n.*", "") .. "\n") end
    end
end

RegisterKeyBind(Key.F7, function()
    AutoExport = not AutoExport
    print("[SasLogistics] auto export " .. (AutoExport and ("on, every " .. AUTO_EXPORT_SECONDS .. " s") or "off") .. "\n")
    Notify("Logistics telemetry " .. (AutoExport and "ON" or "OFF"))
end)

---------------------------------------------------------------------------------------------------
-- Highlighting. The planner writes the cargo to handle at this port into highlight.txt, one item
-- per line: its GUID, optionally followed by "U" for cargo to unload from the boat. In an inventory
-- window showing any of them, cargo to load or take out is tinted green, cargo to unload is tinted
-- orange, and the rest is left as it is. That covers the
-- warehouse window and the cargo list beside the camera feed when operating a crane.

local TICK_MS = 1000
local HighlightPath = ScriptDir .. "\\..\\highlight.txt"
local HighlightOn = true
local Touched = false  -- has any slot's look been changed since everything was last put back
local LastReport = ""
local Wanted = {}      -- the last highlight list read, kept for the slot hooks
local Pending = {}     -- slots the game has just refilled, to be tinted again a moment later

local GREEN = { R = 0.25, G = 1.0, B = 0.25, A = 1.0 }
local ORANGE = { R = 1.0, G = 0.55, B = 0.1, A = 1.0 }
local WHITE = { R = 1.0, G = 1.0, B = 1.0, A = 1.0 }

local function ReadHighlights()
    local wanted, count = {}, 0
    local file = io.open(HighlightPath, "r")
    if not file then return wanted, 0 end
    for line in file:lines() do
        local guid, kind = line:match("^(%x+)%s*(U?)%s*$")  -- only "U" means unload; any other letter is not a line we know
        if guid and #guid == 32 then
            wanted[guid:upper()] = (kind == "U") and ORANGE or GREEN
            count = count + 1
        end
    end
    file:close()
    return wanted, count
end

local function SlotGuid(slot)
    local ok, guid = pcall(function()
        local model = slot.ViewModelData
        if model == nil or not model:IsValid() then model = slot.SlotData end  -- crane cargo lists set this one
        local item = model.Item
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
    Wanted = wanted
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
                if guid and wanted[guid] then
                    Paint(slot, wanted[guid], 1.0)
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

-- Taking an item out makes the game refill its inventory slots, which puts them back to their normal
-- look. Waiting for the next pass would leave them untinted for up to a second, so each slot is
-- tinted again as the game fills it, and once more a moment later in case the game's own setup of
-- the slot ran after the hook did.
local function TintSlot(slot, guid)
    if not IsObject(slot) then return end
    guid = guid or SlotGuid(slot)
    local colour = guid and Wanted[guid]
    if colour then
        Paint(slot, colour, 1.0)
        Touched = true
    elseif Touched then
        Paint(slot, WHITE, 1.0)  -- a reused slot may still carry the tint of the item it showed before
    end
end

-- Only the slot itself is touched here. The function's own parameter is left alone: what it points
-- at is not certain to be a live object, and reading a dead one crashes the game outside pcall's reach.
local function OnSlotFilled(context)
    pcall(function()
        if next(Wanted) == nil and not Touched then return end
        local slot = context:get()
        TintSlot(slot, nil)
        Pending[#Pending + 1] = slot
    end)
end

local SLOT_CLASS = "/Game/ShipsAtSea/UI/Inventories/Items/WBP_ItemInventorySlot.WBP_ItemInventorySlot_C"
local SlotHooks = { [SLOT_CLASS .. ":OnListItemObjectSet"] = false, [SLOT_CLASS .. ":SetSlotData"] = false }

-- The slot's blueprint is only loaded once an inventory window has been opened, so keep trying.
local function HookSlots()
    for path, done in pairs(SlotHooks) do
        if not done and pcall(RegisterHook, path, OnSlotFilled, OnSlotFilled) then
            SlotHooks[path] = true
            print("[SasLogistics] highlight: following " .. path:match("[^:]+$") .. "\n")
        end
    end
end

local function TintPending()
    pcall(SendNotices)
    if #Pending > 0 then
        local slots = Pending
        Pending = {}
        for _, slot in ipairs(slots) do
            pcall(TintSlot, slot, nil)
        end
    end
    return false
end

RegisterKeyBind(Key.F6, function()
    HighlightOn = not HighlightOn
    print("[SasLogistics] highlighting " .. (HighlightOn and "on" or "off") .. "\n")
    Notify("Logistics highlighting " .. (HighlightOn and "ON" or "OFF"))
end)

---------------------------------------------------------------------------------------------------
-- Unload markers. The game marks the dock cargo of the one mission being tracked with a floating
-- icon. This puts the same kind of marker over every piece of cargo in the boat's hold that the plan
-- says comes off at this port, whichever contract it belongs to, so it can be picked out from the
-- crane. They are the game's own marker actors, created here and owned by nobody else; each carries
-- the player controller as its instigator, which is how this mod recognises its own to remove them.

local MARKER_TITLE = "UNLOAD"
local MARKER_ICON = "/Game/ShipsAtSea/UI/WorldMap/Materials/Circle/MI_CargoMapIcon_Green.MI_CargoMapIcon_Green"
local MARKER_TAG = "UIMapMarker.Type.Marker.Mission.Cargo.M"
local MARKER_RAISE_CM = 100
local Markers = {}            -- item guid -> marker actor
local MarkerClass = nil
local StrayMarkersCleared = false
local MarkerReport = ""

local function IsOurMarker(marker)
    local ok, ours = pcall(function()
        local instigator = marker.MarkerData.Instigator
        return instigator:IsValid() and instigator:GetClass():GetFName():ToString():find("PlayerController", 1, true) ~= nil
    end)
    return ok and ours
end

-- Markers left by an earlier load of this mod are still in the world but no longer tracked.
local function RemoveStrayMarkers()
    for _, marker in ipairs(FindAllOf("MissionMarker") or {}) do
        if IsOurMarker(marker) then pcall(function() marker:K2_DestroyActor() end) end
    end
end

local function FindMarkerClass()
    if IsObject(MarkerClass) then return MarkerClass end
    for _, component in ipairs(FindAllOf("SasEmployeeMissionComponent") or {}) do
        local ok, class = pcall(function() return component.MissionMarkerClass end)
        if ok and IsObject(class) then
            MarkerClass = class
            return class
        end
    end
    return nil
end

-- One of the game's own cargo markers, if any is showing, to copy the look from.
local function TemplateMarker()
    for _, marker in ipairs(FindAllOf("MissionMarker") or {}) do
        local ok, usable = pcall(function()
            return not IsOurMarker(marker) and marker.MarkerData.Icon.ResourceObject:IsValid()
                and marker.MarkerData.MissionMarkerTag.TagName:ToString() == MARKER_TAG
        end)
        if ok and usable then return marker end
    end
    return nil
end

local function SpawnMarker(cargo, controller, statics)
    local class = FindMarkerClass()
    if not class then return nil end
    local at = cargo:K2_GetActorLocation()
    local transform = { Rotation = { X = 0.0, Y = 0.0, Z = 0.0, W = 1.0 },
                        Translation = { X = at.X, Y = at.Y, Z = at.Z + MARKER_RAISE_CM },
                        Scale3D = { X = 1.0, Y = 1.0, Z = 1.0 } }
    local marker = statics:BeginDeferredActorSpawnFromClass(controller, class, transform, 1, nil, 0)
    if not IsObject(marker) then return nil end
    statics:FinishSpawningActor(marker, transform, 0)

    local template = TemplateMarker()
    if template then
        pcall(function() marker:Init(template.MarkerData) end)
    else
        pcall(function()
            local icon = StaticFindObject(MARKER_ICON)
            if not IsObject(icon) then icon = LoadAsset(MARKER_ICON) end
            marker.MarkerData.Icon.ResourceObject = icon
            marker.MarkerData.Icon.ImageSize = { X = 86.0, Y = 86.0 }
        end)
        pcall(function() marker.MarkerData.MissionMarkerTag.TagName = FName(MARKER_TAG) end)
    end
    local data = marker.MarkerData
    pcall(function() data.WidgetTitle = FText(MARKER_TITLE) end)
    pcall(function() data.Instigator = controller end)
    pcall(function() data.AttachedToActor = cargo end)
    pcall(function() data.Location = { X = at.X, Y = at.Y, Z = at.Z } end)
    pcall(function() marker:Update(marker.MarkerData) end)
    pcall(function() marker:K2_AttachToActor(cargo, FName("None"), 1, 1, 1, false) end)  -- keep its place, follow the cargo
    pcall(function() marker:SetVisibility(true) end)
    return marker
end

-- `unload` is the set of item guids to mark. A marker is kept only while its cargo is in a boat's
-- hold, so it goes as soon as the crane lifts the cargo out, without waiting for the next snapshot.
local function UpdateMarkers(unload)
    if not StrayMarkersCleared then
        StrayMarkersCleared = true
        RemoveStrayMarkers()
    end
    local wanted = next(unload) ~= nil
    if not wanted and next(Markers) == nil then return end

    local inHold, made = {}, 0
    if wanted then
        local controller = FindFirstOf("PlayerController")
        local statics = StaticFindObject("/Script/Engine.Default__GameplayStatics")
        if IsObject(controller) and IsObject(statics) then
            for _, cargo in ipairs(FindAllOf("Cargo") or {}) do
                pcall(function()
                    if not cargo:IsValid() then return end
                    local area = cargo.CargoArea
                    if not area:IsValid() or not area:GetClass():GetFName():ToString():find("BoatCargoArea", 1, true) then return end
                    local guid = GuidStr(cargo:GetRepresentedInventoryItem().Guid)
                    if not unload[guid] then return end
                    inHold[guid] = true
                    if not IsObject(Markers[guid]) then
                        Markers[guid] = SpawnMarker(cargo, controller, statics)
                        if Markers[guid] then made = made + 1 end
                    end
                end)
            end
        end
    end
    local shown = 0
    for guid, marker in pairs(Markers) do
        if inHold[guid] then
            shown = shown + 1
        else
            pcall(function() marker:K2_DestroyActor() end)
            Markers[guid] = nil
        end
    end
    local report = string.format("%d cargo to unload marked in the hold", shown)
    if report ~= MarkerReport then
        MarkerReport = report
        print("[SasLogistics] markers: " .. report .. "\n")
    end
end

local function UnloadWanted()
    local unload = {}
    if not HighlightOn then return unload end
    local wanted = ReadHighlights()
    for guid, colour in pairs(wanted) do
        if colour == ORANGE then unload[guid] = true end
    end
    return unload
end

---------------------------------------------------------------------------------------------------
-- Depth grid. The planner works out sea routes between ports, and for that it needs to know where
-- the water is. The first time a save is loaded this samples the game's water depth every 50 m
-- across the area the ports cover and writes depthgrid.txt; after that the file is left alone.
-- Delete the file to have it sampled again.
--
-- File: a header line "sasdepth 1 <origin x> <origin y> <cell cm> <columns> <rows> <min boat depth cm>",
-- then one line per row (south-going y), depths in whole metres west to east; negative is land.

local DEPTH_CELL_CM = 5000
local DEPTH_PADDING_CM = 200000  -- sampled this far beyond the outermost ports
local DEPTH_ROWS_PER_STEP = 3    -- about 10 ms of work per step, so the game does not stutter
local DEPTH_STEP_MS = 100
local DepthPath = ScriptDir .. "\\..\\depthgrid.txt"
local Depth = nil            -- sampling state while a grid is being written
local DepthSettled = false   -- the grid exists, or is being sampled, or sampling has failed too often
local DepthFailures = 0

local function DepthStep()
    local job = Depth
    if not job then return true end
    local ok, err = pcall(function()
        for _ = 1, DEPTH_ROWS_PER_STEP do
            if job.row >= job.rows then break end
            local y = job.minY + job.row * DEPTH_CELL_CM
            local cells = {}
            for col = 0, job.cols - 1 do
                local metres = job.env:GetWaterDepth({ X = job.minX + col * DEPTH_CELL_CM, Y = y, Z = 0 }) / 100
                -- Outside the mapped area the game reports an enormous land height.
                if metres ~= metres or metres < -99 then metres = -99 elseif metres > 999 then metres = 999 end
                cells[col + 1] = string.format("%.0f", metres)
            end
            job.file:write(table.concat(cells, " "), "\n")
            job.row = job.row + 1
        end
    end)
    if ok and job.row < job.rows then return false end

    job.file:close()
    Depth = nil
    if ok then
        os.remove(DepthPath)
        os.rename(DepthPath .. ".tmp", DepthPath)
        print(string.format("[SasLogistics] depth grid written: %.0f x %.0f cells\n", job.cols, job.rows))
    else
        os.remove(DepthPath .. ".tmp")
        DepthFailures = DepthFailures + 1
        DepthSettled = DepthFailures >= 3  -- otherwise try again from the top on a later tick
        print("[SasLogistics] depth grid sampling stopped: " .. tostring(err):gsub("\n.*", "") .. "\n")
    end
    return true
end

local function StartDepthGrid()
    local existing = io.open(DepthPath, "r")
    if existing then
        existing:close()
        DepthSettled = true
        return
    end
    local env, portSubsystem = FindFirstOf("EnvironmentSubsystem"), FindFirstOf("PortSubsystem")
    if not IsObject(env) or not IsObject(portSubsystem) then return end

    local minX, maxX, minY, maxY, count = math.huge, -math.huge, math.huge, -math.huge, 0
    for _, port in ipairs(FindAllOf("Port") or {}) do
        pcall(function()
            if port:GetFullName():find("Default__", 1, true) then return end
            local at = portSubsystem:GetPortLocation(port)
            if at.X == 0 and at.Y == 0 then return end  -- placeholder ports sit at the origin
            minX, maxX = math.min(minX, at.X), math.max(maxX, at.X)
            minY, maxY = math.min(minY, at.Y), math.max(maxY, at.Y)
            count = count + 1
        end)
    end
    if count < 5 then return end  -- not in a loaded save yet

    minX, minY = minX - DEPTH_PADDING_CM, minY - DEPTH_PADDING_CM
    local cols = math.floor((maxX + DEPTH_PADDING_CM - minX) / DEPTH_CELL_CM) + 1
    local rows = math.floor((maxY + DEPTH_PADDING_CM - minY) / DEPTH_CELL_CM) + 1
    local file = io.open(DepthPath .. ".tmp", "w")
    if not file then return end
    local minDepth = 500
    pcall(function() minDepth = env:GetMinWaterDepthForBoats() end)
    file:write(string.format("sasdepth 1 %.0f %.0f %.0f %.0f %.0f %.0f\n", minX, minY, DEPTH_CELL_CM, cols, rows, minDepth))
    Depth = { env = env, file = file, minX = minX, minY = minY, cols = cols, rows = rows, row = 0 }
    DepthSettled = true
    print(string.format("[SasLogistics] sampling the depth grid: %.0f x %.0f cells\n", cols, rows))
    -- Its own timer, also on the game thread, so the rows go by quickly without one long pause.
    if LoopInGameThreadWithDelay then
        LoopInGameThreadWithDelay(DEPTH_STEP_MS, DepthStep)
    end
end

---------------------------------------------------------------------------------------------------
-- The one timer. It runs on the game thread, which is the only place game objects may be touched.
-- An earlier version used LoopAsync plus ExecuteInGameThread from two background timers; those raced
-- on the Lua state and brought the game down.

local RequestPath = ScriptDir .. "\\..\\request.txt"
local Ticks = 0

local function Tick()
    Ticks = Ticks + 1
    if not DepthSettled and Ticks % 5 == 0 then
        pcall(StartDepthGrid)
    elseif Depth and not LoopInGameThreadWithDelay then
        pcall(DepthStep)  -- older UE4SS builds: sample on this timer instead, more slowly
    end
    -- The planner asks for a snapshot by leaving a request file here.
    local request = io.open(RequestPath, "r")
    if request then
        request:close()
        os.remove(RequestPath)
        ExportRequested = true
    end
    if ExportRequested then
        ExportRequested = false
        pcall(Export, "manual")
    elseif AutoExport and Ticks % AUTO_EXPORT_SECONDS == 0 then
        pcall(Export, "auto")
    end
    pcall(UpdateHighlights)
    pcall(HookSlots)
    if Ticks % 2 == 0 then
        pcall(function() UpdateMarkers(UnloadWanted()) end)
    end
    return false
end

if LoopInGameThreadWithDelay then
    LoopInGameThreadWithDelay(30, TintPending)
    LoopInGameThreadWithDelay(TICK_MS, Tick)
else
    -- Older UE4SS builds: a single background timer that hands each tick to the game thread.
    LoopAsync(TICK_MS, function()
        ExecuteInGameThread(Tick)
        return false
    end)
end

print("[SasLogistics] loaded: telemetry is on (a snapshot every " .. AUTO_EXPORT_SECONDS .. " s); F8 = write snapshot now, F7 = toggle auto export, F6 = toggle highlighting and unload markers\n")

-- For the tests (app/tests/lua_harness.py): when a test sets SAS_TEST before loading this script, the local
-- functions and values the tests need are handed out in SasTest. In the game SAS_TEST is never set.
if SAS_TEST then
    SasTest = { ReadHighlights = ReadHighlights, UnloadWanted = UnloadWanted, ORANGE = ORANGE, GREEN = GREEN,
                ExportFinance = ExportFinance }
end
