-- SasBoatFixes: handling fixes for Ships At Sea, plus the recorder used to find them.
-- F4 toggles the list fix, which is on by default: crew and passengers no longer press down on the
--    deck they stand on. This is a change to how the game behaves. See "List fix" below.
-- F9 starts and stops a physics recording of the boat being driven, written to boatlog.txt in this
--    mod's folder. The recorder only reads.

local TICK_MS = 250
local TICKS_PER_SECOND = 4

local ScriptDir = debug.getinfo(1, "S").source:sub(2):match("^(.*)[/\\][^/\\]*$") or "."
local BoatLogPath = ScriptDir .. "\\..\\boatlog.txt"

---------------------------------------------------------------------------------------------------
-- Helpers

local function IsObject(obj)
    return obj ~= nil and type(obj) == "userdata" and pcall(function() return obj:IsValid() end) and obj:IsValid()
end

-- Reads a value for the log; a read that fails becomes a short error note instead of stopping the row.
local function Get(fn)
    local ok, value = pcall(fn)
    if not ok then return "<err " .. tostring(value):gsub("\n.*", ""):sub(-60) .. ">" end
    if type(value) == "number" then return string.format("%.4f", value) end
    return tostring(value)
end

local function V3(v)
    local ok, text = pcall(function() return string.format("%.2f %.2f %.2f", v.X, v.Y, v.Z) end)
    return ok and text or "?"
end

-- Iterates a TArray wrapper or a plain Lua table the same way.
local function Each(array, fn)
    if type(array) == "table" then
        for _, element in ipairs(array) do
            local ok, inner = pcall(function() return element:get() end)
            fn(ok and inner ~= nil and inner or element)
        end
    elseif array ~= nil then
        array:ForEach(function(_, element) fn(element:get()) end)
    end
end

-- The owned boat nearest the player, which is the one being driven.
local function DrivenBoat()
    local here = nil
    pcall(function() here = FindFirstOf("PlayerController").Pawn:K2_GetActorLocation() end)
    local best, bestDistance = nil, nil
    for _, component in ipairs(FindAllOf("CompanyOwnedBoatsComponent") or {}) do
        pcall(function()
            Each(component.ActiveBoats, function(boat)
                if not IsObject(boat) then return end
                local distance = 0
                if here then
                    local at = boat:K2_GetActorLocation()
                    distance = (at.X - here.X) ^ 2 + (at.Y - here.Y) ^ 2
                end
                if not bestDistance or distance < bestDistance then best, bestDistance = boat, distance end
            end)
        end)
    end
    return best
end

-- A world-space point expressed along the boat's own forward / right / up axes, in cm.
local function InBoatAxes(boat, point)
    local origin = boat:K2_GetActorLocation()
    local dx, dy, dz = point.X - origin.X, point.Y - origin.Y, point.Z - origin.Z
    local function along(axis) return dx * axis.X + dy * axis.Y + dz * axis.Z end
    return string.format("fwd %.1f right %.1f up %.1f",
        along(boat:GetActorForwardVector()), along(boat:GetActorRightVector()), along(boat:GetActorUpVector()))
end

-- Body queries take a bone name; a boat hull is a single body, so that is always "None".
local function WholeBody()
    return FName("None")
end

local function CentreOfMass(boat)
    return InBoatAxes(boat, boat.Mesh:GetCenterOfMass(WholeBody()))
end

---------------------------------------------------------------------------------------------------
-- People aboard. A character standing on a physics body pushes down on it where they stand, with a
-- force of their mass times gravity times StandingDownwardForceScale. That push is not part of the
-- boat's mass or centre of mass, so it has to be looked at separately.

local ABOARD_RANGE_CM = 3000

local function PeopleAboard(boat)
    local people = {}
    local origin = boat:K2_GetActorLocation()
    local right, forward, up = boat:GetActorRightVector(), boat:GetActorForwardVector(), boat:GetActorUpVector()
    for _, character in ipairs(FindAllOf("Character") or {}) do
        pcall(function()
            if not character:IsValid() or character:GetFullName():find("Default__", 1, true) then return end
            local at = character:K2_GetActorLocation()
            local dx, dy, dz = at.X - origin.X, at.Y - origin.Y, at.Z - origin.Z
            if dx * dx + dy * dy + dz * dz > ABOARD_RANGE_CM * ABOARD_RANGE_CM then return end
            local movement = character.CharacterMovement
            people[#people + 1] = {
                class = character:GetClass():GetFName():ToString(),
                right = dx * right.X + dy * right.Y + dz * right.Z,
                forward = dx * forward.X + dy * forward.Y + dz * forward.Z,
                up = dx * up.X + dy * up.Y + dz * up.Z,
                mass = movement.Mass,
                push = movement.StandingDownwardForceScale,
                interacts = movement.bEnablePhysicsInteraction,
            }
        end)
    end
    return people
end

-- kg-metres of sideways turning effect from people pressing on the deck; positive leans to starboard.
local function PeopleMoment(people)
    local moment, count = 0.0, 0
    for _, person in ipairs(people) do
        if person.interacts and person.push and person.push > 0 then
            moment = moment + person.mass * person.push * person.right / 100.0
            count = count + 1
        end
    end
    return moment, count
end

---------------------------------------------------------------------------------------------------
-- List fix. Boats could heel over in a turn and stay heeled for as long as they kept their speed.
-- Recordings of the class 3 service vessel showed its mass and centre of mass never moving while it
-- held 17 degrees of list at 26 knots; the list went within five seconds of switching off the
-- downward push of the six crew and passengers aboard. The game already has that push switched off
-- for the player's own character, so this gives every other character the same treatment.
--
-- The hull's weak righting force at speed is the underlying problem and is not touched here.

local ListFixOn = true
local ListFixToggle = false
local SavedPush = {}  -- character full name -> its original StandingDownwardForceScale

local function ApplyListFix()
    if ListFixToggle then
        ListFixToggle = false
        ListFixOn = not ListFixOn
        print("[SasBoatFixes] list fix " .. (ListFixOn and "on: crew and passengers do not press on the deck" or "off: game default") .. "\n")
        if not ListFixOn then
            for _, character in ipairs(FindAllOf("SasNpcCharacter") or {}) do
                pcall(function()
                    local saved = SavedPush[character:GetFullName()]
                    if saved then character.CharacterMovement.StandingDownwardForceScale = saved end
                end)
            end
            SavedPush = {}
        end
    end
    if not ListFixOn then return end
    for _, character in ipairs(FindAllOf("SasNpcCharacter") or {}) do
        pcall(function()
            local movement = character.CharacterMovement
            local push = movement.StandingDownwardForceScale
            if push > 0 then
                SavedPush[character:GetFullName()] = push
                movement.StandingDownwardForceScale = 0.0
            end
        end)
    end
end

---------------------------------------------------------------------------------------------------
-- Boat physics recorder. It writes what the boat is made of (mass, centre of mass, buoyancy settings,
-- what is attached, who is aboard) and then a row four times a second with its attitude.

local BoatLog = nil  -- open file while recording
local BoatLogToggle = false
local BoatLogTicks = 0

local BUOYANCY_FIELDS = {
    "WaterDensity", "bEnableHydrostaticsForce", "BuoyancyCoefficient", "LateralBuoyantForcePercentage",
    "bEnableLinearDampingImpulse", "LinearDampingCoefficientLong", "LinearDampingCoefficientLat", "LinearDampingCoefficientVert",
    "bEnableAngularDampingImpulse", "AngularDampingCoefficientRoll", "AngularDampingCoefficientPitch", "AngularDampingCoefficientYaw",
    "bEnableViscousDrag", "bEnablePressureSuctionDrag", "PressureDragPower", "LinearPressureDragCoefficient",
    "QuadraticPressureDragCoefficient", "SuctionDragPower", "LinearSuctionDragCoefficient", "QuadraticSuctionDragCoefficient",
    "DragReferenceVelocity", "DragMultiplierLong", "DragMultiplierLat", "DragMultiplierVert", "bEnableSlammingForce",
    "VerticalWaterDisplacementForceCoefficient", "LateralWaterDisplacementForceCoefficient",
}

local function WriteBoatHeader(file, boat)
    local function line(label, fn) file:write(label, ": ", Get(fn), "\n") end
    local body = boat.Mesh
    local movement = boat.BoatMovementComponent
    file:write("# SasBoatFixes boat recording ", os.date("%Y-%m-%d %H:%M:%S"), "\n")
    line("boat", function() return boat:GetFullName() end)
    line("info", function() return boat:GetBoatInfo():GetFullName() end)
    line("mass kg", function() return body:GetMass() end)
    line("centre of mass", function() return CentreOfMass(boat) end)
    line("inertia tensor", function() return V3(body:GetInertiaTensor(WholeBody())) end)
    line("linear damping", function() return body:GetLinearDamping() end)
    line("angular damping", function() return body:GetAngularDamping() end)
    line("body COM nudge", function() return V3(body.BodyInstance.COMNudge) end)
    line("body mass override", function() return tostring(body.BodyInstance.bOverrideMass) .. " " .. tostring(body.BodyInstance.MassInKgOverride) end)
    line("buoyancy values asset", function() return movement.BuoyancyValues:GetFullName() end)
    for _, field in ipairs(BUOYANCY_FIELDS) do
        line("  " .. field, function() return movement.BuoyancyValues.BuoyancyValues[field] end)
    end
    line("health", function() return boat.DamageComponent.CurrentHealth end)
    pcall(function()
        boat.BoatInventoryComponent.Inventories:ForEach(function(key, value)
            line("  inventory " .. key:get().TagName:ToString() .. " weight", function() return value:get().InventoryWeight end)
        end)
    end)
    pcall(function()
        local attached = {}
        boat:GetAttachedActors(attached, true, true)
        Each(attached.OutActors or attached, function(actor)
            file:write("  attached: ", Get(function() return actor:GetFullName():match("([^%.:]+)$") end),
                " | class ", Get(function() return actor:GetClass():GetFName():ToString() end),
                " | at ", Get(function() return InBoatAxes(boat, actor:K2_GetActorLocation()) end), "\n")
        end)
    end)
    pcall(function()
        for _, person in ipairs(PeopleAboard(boat)) do
            file:write(string.format("  aboard: %s | right %.0f fwd %.0f up %.0f cm | mass %s push %s interacts %s\n",
                person.class, person.right, person.forward, person.up,
                tostring(person.mass), tostring(person.push), tostring(person.interacts)))
        end
    end)
    file:write("# t_s roll_deg pitch_deg yaw_deg speed_kn mass_kg | centre of mass | people pushing, their moment kg.m (+ = starboard), offsets right cm | list fix\n")
end

local function RecordBoat()
    if BoatLogToggle then
        BoatLogToggle = false
        if BoatLog then
            BoatLog:close()
            BoatLog = nil
            print("[SasBoatFixes] boat recording stopped\n")
        else
            local boat = DrivenBoat()
            if not IsObject(boat) then
                print("[SasBoatFixes] boat recording: no owned boat found\n")
                return
            end
            BoatLog = io.open(BoatLogPath, "w")
            BoatLogTicks = 0
            pcall(WriteBoatHeader, BoatLog, boat)
            print("[SasBoatFixes] boat recording started: " .. BoatLogPath .. "\n")
        end
    end
    if not BoatLog then return end
    local boat = DrivenBoat()
    if not IsObject(boat) then return end
    BoatLogTicks = BoatLogTicks + 1
    -- Each value is read on its own, so one that cannot be read still leaves a usable row.
    BoatLog:write(string.format("%.2f", BoatLogTicks / TICKS_PER_SECOND), " ",
        Get(function() return boat:K2_GetActorRotation().Roll end), " ",
        Get(function() return boat:K2_GetActorRotation().Pitch end), " ",
        Get(function() return boat:K2_GetActorRotation().Yaw end), " ",
        Get(function() return boat.BoatMovementComponent:GetSpeedKnots() end), " ",
        Get(function() return boat.Mesh:GetMass() end), " | ",
        Get(function() return CentreOfMass(boat) end), " | ",
        Get(function()
            local people = PeopleAboard(boat)
            local moment, count = PeopleMoment(people)
            local offsets = {}
            for _, person in ipairs(people) do offsets[#offsets + 1] = string.format("%.0f", person.right) end
            return string.format("%d %.1f [%s]", count, moment, table.concat(offsets, " "))
        end), " | ", ListFixOn and "list-fix-on" or "list-fix-off", "\n")
    BoatLog:flush()
end

---------------------------------------------------------------------------------------------------
-- Key presses only set flags. All the work happens in one timer on the game thread, which is the
-- only place game objects may be touched, so this mod's Lua never runs on two threads at once.

RegisterKeyBind(Key.F4, function()
    ListFixToggle = true
end)

RegisterKeyBind(Key.F9, function()
    BoatLogToggle = true
end)

local Ticks = 0

local function Tick()
    Ticks = Ticks + 1
    pcall(RecordBoat)
    if ListFixToggle or Ticks % (2 * TICKS_PER_SECOND) == 0 then  -- new crew and passengers appear over time
        pcall(ApplyListFix)
    end
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

print("[SasBoatFixes] loaded: list fix on (F4 toggles), F9 = record boat physics\n")
