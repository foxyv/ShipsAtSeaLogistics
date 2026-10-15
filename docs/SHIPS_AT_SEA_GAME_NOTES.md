# Ships At Sea Game Notes

What we learned about Ships At Sea (Steam app 1266540, Unreal Engine 5.7.4) and about modding it
with UE4SS Lua, while building three mods: [Ships At Sea Logistics](https://github.com/foxyv/ShipsAtSeaLogistics),
[SasBoatFixes](https://github.com/foxyv/SasBoatFixes) and [SasCraneOperator](https://github.com/foxyv/SasCraneOperator).
Collected 2026-10-13.

Sources are noted per section: **dump** = the UE4SS header dump (`ue4ss\CXXHeaderDump\*.hpp`) and
object dump (`ue4ss\UE4SS_ObjectDump.txt`); **probe** = a recording made in game by one of our probe
mods; **playtest** = seen in game; **ours** = how our own mods do it, which works but is our choice,
not the game's. Anything not verified is marked.

## Setting up

Source: playtest.

- **Install:** `D:\SteamLibrary\steamapps\common\Ships At Sea`. The game binary is
  `SAS\Binaries\Win64\Sas-Win64-Shipping.exe`, launched through `Sas.exe`.
- **UE4SS:** only the **experimental** build runs on UE 5.7 (we use `UE4SS_v3.0.1-1164-g5e627997`);
  stable 3.0.1 does not. It goes into `SAS\Binaries\Win64` as `dwmapi.dll` plus a `ue4ss` folder.
- **Settings we use** (`ue4ss\UE4SS-settings.ini`): GUI console on, `EnableHotReloadSystem = 1`, so
  **Ctrl+R** reloads every Lua mod without restarting the game.
- **Mods load** from `ue4ss\Mods`: those listed in `mods.txt` first, then any folder holding an
  `enabled.txt`, in no defined order. Removing `enabled.txt` switches a mod off.
- **Bundled mods that matter:**
  - `CheatManagerEnablerMod` builds a cheat manager when the player controller restarts, which
    happens when a save is loaded (see Cheats).
  - `ConsoleEnablerMod` opens the game console on **F10**.
  - `LineTraceMod` (F3, off in `mods.txt`).
- **Dumps:** the header dump and object dump come from the UE4SS GUI. The object dump lists every
  live object with its properties and function parameters, which is how parameter names and
  offsets were found.
- **Saves:** `%LOCALAPPDATA%\SAS\Saved\SaveGames` (`SaveGame00.sav` …, `Autosave00.sav` …). Steam
  Cloud syncs the folder (`steam_autocloud.vdf`), so keep a local copy before risky tests.
- **Logs:** `ue4ss\UE4SS.log` gets every Lua `print` and every script load error, with timestamps.

## UE4SS Lua: rules that cost us something

Source: playtest, unless noted.

- **Only touch the game from the game thread.** Two timers built from `LoopAsync` +
  `ExecuteInGameThread` raced on the Lua state and crashed the game (`0xc0000409`, "Ref was not
  function"). Use `LoopInGameThreadWithDelay(ms, fn)` (return `false` to keep it running). Key
  presses and hooks should only set a flag or queue work for that timer.
- **Several timers are fine** as long as all are `LoopInGameThreadWithDelay`; the crane mod runs one
  at 500 ms for decisions and one at 100 ms for steering.
- **Never name a local `Key`.** `local function Key(...)` shadows UE4SS's global key table and the
  next `RegisterKeyBind(Key.F5, …)` fails with "attempt to index a function value", which stops the
  whole script from loading. Check `UE4SS.log` for "Error executing script" after every reload.
- **Key binds:** `RegisterKeyBind(Key.F5, fn)` or `RegisterKeyBind(Key.O, { ModifierKey.CONTROL,
  ModifierKey.ALT }, fn)`. Presses only arrive while the game window has focus, not the UE4SS
  console. Holding a key repeats it.
- **Keys already taken:**

  | Key | Taken by |
  | --- | --- |
  | F3 | `LineTraceMod` and our probes |
  | F4, F9 | SasBoatFixes |
  | F5 | SasCraneOperator |
  | F6, F7, F8 | SasLogistics |
  | F10 | game console (`ConsoleEnablerMod`) |
  | Ctrl+Alt+S | something in the game: a short freeze, then the player is moved to the edge of the boat. Not identified. |

- **Structs are Lua tables:** `{ X = 0, Y = 0, Z = 0 }`, `{ Pitch = 0.0, Yaw = 0.0, Roll = 0.0 }`,
  a gameplay tag is `{ TagName = FName("Channel.Toast.Simple.Info") }`. `FName("...")` and
  `FText("...")` build names and texts.
- **Out parameters** come back in the table you pass: `local out = {}; obj:Fn(..., out)`, then the
  value is either `out.<ParamName>` or `out` itself (`out.OutRewards or out`). `GetActorBounds` fills
  `origin.Origin` / `extent.BoxExtent` the same way.
- **Arrays and maps:**
  - `TArray:ForEach(function(i, v) v:get() end)`; `TMap:ForEach(function(k, v) k:get(), v:get() end)`.
  - An array property can be assigned a whole Lua table: `comp.CrewGroups = { group }`.
  - `TMap:Add(key, value)` and `TMap:Empty()` work.
- **Making objects:** `StaticConstructObject(class, outer)`; spawning actors with
  `GameplayStatics:BeginDeferredActorSpawnFromClass(controller, class, transform, 1, nil, 0)` then
  `FinishSpawningActor(actor, transform, 0)`.
- **Don't list an actor's components through the engine.** Going through the result of
  `actor:K2_GetComponentsByClass(class)` crashed the game twice, inside UE4SS's object check
  (`HasAnyInternalFlags`, reading address 0x18), a few entries in. Read components by their property
  name (`crane.HookJoint`) or a skeletal mesh's bones (`GetNumBones`, `GetBoneName`,
  `GetSocketLocation`) instead.
- **A crash outside Lua can't be caught.** `pcall` only guards Lua errors; touching a dead object
  through UE4SS takes the game down. Keep hook callbacks to the object running the function, and
  don't read a hook's parameters unless you know they are live objects.
- **Object identity:** compare with `a:GetAddress() == b:GetAddress()`. `FindAllOf` also returns the
  class default object (`Default__…`); filter it out by full name.
- **Clearing an object reference:** we try `obj.Prop = nil` and fall back to
  `CreateInvalidObject()`; which one this build accepts was not pinned down. (ours)
- **Hooks:** `RegisterHook("/Game/…/WBP_X.WBP_X_C:Fn", pre, post)` fails until that blueprint class
  is loaded, so retry it from the timer. UE4SS unregisters a mod's hooks on Ctrl+R.
- **What survives Ctrl+R:** objects a mod created in the world (crew groups, markers) stay, while the
  mod's Lua state is gone. Recognise your own objects by their data (a group name, a marker title),
  not by a Lua table, and clean up strays on load.
- **A chunk may have at most 200 locals.** A long script that passes it fails to load ("too many
  local variables (limit is 200) in main function"). Keep settings in a table or as globals of the
  mod's own Lua state.
- **Line traces work:** `Default__KismetSystemLibrary:LineTraceSingle(worldContext, start, end, 0,
  false, { actorsToIgnore }, 0, hit, true, colour, colour, 0.0)` returns whether it hit, with the
  hit in `hit.OutHit or hit` (`ImpactPoint`, `Location`). The bundled `LineTraceMod` shows the
  same call.
- **Errors inside `pcall` vanish.** Log the error of any call that matters; a missing toast went
  unexplained until we did.
- **Tooling on this machine:** there is no Lua compiler to syntax-check, so load errors only show in
  game. In Git Bash, heredocs halve backslashes (`\\n` becomes `\n`); write patch scripts with a
  file-writing tool and raw strings. `python` in PowerShell is the broken Store alias; use `py`.

## World, units and the map

Source: dump, probe.

- **Units:** centimetres. World **X points north, Y east**, Z up.
- **Ports:** `Port` objects; `PortSubsystem:GetPortLocation(port)` gives each one's position.
  Neighbouring ports can be as little as 0.5 km apart, and a port's far docks can be further from its
  own position than that: Røst's large-cargo dock is 348 m from Røst and 289 m from Kveldsbeita.
  Each `CargoArea` names its `Port`, so the dock areas are the better guide to which port a boat is at.
- **Water depth:** `EnvironmentSubsystem:GetWaterDepth({X, Y, Z})` returns centimetres, positive over
  water and negative on land, in about 5 µs a call. `GetMinWaterDepthForBoats()` is **500 cm**.
- **The map in numbers** (ours): a depth grid at 50 m covering every port plus 2 km is 665 × 547
  cells (33.25 × 27.35 km) from (−1,762,880, −1,723,170). Sampling it all takes about 20 s when spread
  over the game thread in 10 ms slices.

## Contracts (missions)

Source: dump, probe, playtest.

- **Objects:** `SasMission`, each with `MissionDescriptor` (`GetMissionTitle()`,
  `GetMissionDataAsset()`, `GetPotentialRewards(out)`) and `CurrentMissionStatus`: 0 NotStarted,
  1 WaitingForDependencies, 2 InProgress, 3 Succeeded, 4 Failed, 5 Cancelled, 6 Suspended.
- **Data contexts:** `mission.MissionDataContext.Data` and
  `descriptor.MissionDescriptorDataContext.Data` hold tagged values (object arrays, GUID arrays, int
  arrays). The tags that matter:

  | Tag | Holds |
  | --- | --- |
  | `Mission.Descriptor.Start` / `.End` | start and destination port |
  | `Mission.Descriptor.MissionID` | the contract's GUID |
  | `Mission.Data.Spawned` | the GUIDs of the contract's cargo |
  | `Mission.Rewards.Delivery.BoardedReward` | cargo already put aboard |
  | `Mission.Rewards.Delivery.DeliverededReward` | cargo already delivered (the game's spelling) |

- **Contract number:** the game's `#DL-XXXXXXXX` is the CRC-32 of the GUID string encoded as
  UTF-32-LE (Unreal's `StrCrc32`), as eight upper-case hex digits.
- **Delivery and completion** (playtest):
  - Cargo counts as delivered when it reaches the destination, including when it is put in the
    destination's warehouse or set down on its dock by the crane.
  - Delivered cargo stays where it was put. The contract only completes when the cargo is **taken
    out of the destination warehouse**, so a part-delivered contract sitting in a warehouse needs
    that last step once the rest arrives.

## Cargo, warehouses and cargo grids

Source: dump, probe.

- **Warehouses:** `WarehouseProxy` per port (`Port`, `BasicInventoryComponent.Inventories`).
- **Cargo areas** (`CargoArea`, each with `Port`, `OriginComponent`, `GridBoundsComponent`,
  `BasicInventoryComponent`, `CargoRepresentationComponent`, `GetInventory()`):

  | Class | What it is | Grid seen |
  | --- | --- | --- |
  | `BP_BoatCargoArea_C` | a boat's hold, attached to the boat | 12 × 12 × 4 on the class 2 cargo boat |
  | `BP_CargoArea_C` | a port's own cargo area | |
  | `BP_CargoSpawnArea_C` | dock area beside the crane; the dock cargo our first crane test picked up stood in one (what else it is for is not known) | |
  | `BP_CargoMissionSpawnArea_Heavy_C` | dock area for heavy contract cargo | 20 × 20 × 10, 25 × 25 × 12 |
  | `BP_CargoMissionSpawnArea_Light_C` | dock area for light cargo | 8 × 7 × 4 |
  | `BP_FishUnloadingCargoArea_C`, `BP_CargoHoldingArea01_C` | fish unloading, holding | |

- **Cells** are 25 cm (`Default__SasInventorySettings.GridInventoryCellSize`).
- **Grid to world:** the grid's corner is `OriginComponent`'s location. Grid X runs along its yaw,
  grid Y along yaw + 90°, Z counts cell layers up from 0 at the floor. A cargo actor's location is
  the centre of its footprint (a 3 × 3 piece at cell 0,0 sits 37.5 cm in on each axis).
- **What fills which cell:** `inventory.CellsOccupancyMap` (cell → item) and
  `inventory.GridInventoryItems.Entries` (item, `GridTransform.position`, `GridTransform.Rotation`).
  An item's footprint is in `ItemDefinition.Fragments[].CellPositions`.
- **Finding room:** `inventory:FindPlaceForInventoryItemInGrid(item, out, { DesiredGridTransform =
  zero, PositionRule = 1, RotationRule = 1 }, {})` returns whether there is a place and fills `out`
  with a grid transform. Rule 1 is `Fit` (0 is `Preserve`). It does stack pieces on others when it
  can (pipes on pipes), but does not rearrange anything.
- **Cargo actors:** `Cargo` → `HeavyCargo` (crane cargo, with a `CargoHook` component at its lifting
  point) and `LightCargo` (carried by hand). `cargo.CargoArea` is the area it stands in;
  `cargo:GetRepresentedInventoryItem()` its item, with `Guid`, `GetDisplayName()`, `GetItemWeight()`.
- **A piece on a crane hook has no item:** `GetRepresentedInventoryItem()` fails while it hangs, so
  read the GUID before lifting.
- **Green placement cells:** while a crane carries a piece over an area, the area's
  `CargoRepresentationComponent.HighlightedCellsMap` holds green `CargoCellHighlightComponent`s where
  it would land. Letting go at the controls clears them; setting a piece down with
  `ServerDetachCargo` does not. Hiding them and calling `HighlightedCellsMap:Empty()` works.

## Cranes

Source: dump, probe, playtest.

- **Classes:** `ACrane` (a pawn) → `ACargoCrane` → `BP_CargoCrane_Base_C` →
  `BP_StationaryCrane_T01__C` (the dock crane). Cranes need the skill `Skills.Deckhand.CraneOperator`
  level 1 (`CraneSkill`, `RequiredCraneLevel`). `IsOccupied()`, `IsPowered()`, `IsEnabled()`.
- **Moving cargo without anyone at the controls works:** `crane:ServerAttachCargo(heavyCargo)`
  hangs the piece on the hook, wherever the hook is. `crane:ServerDetachCargo(area, position,
  rotation)` puts it in that area at that grid place, and the game's records follow (cargo moved
  dock → hold this way is in the hold; unloaded cargo counts as delivered). `AttachedCargo` and
  `TargetCargoArea` say what it holds and where.
- **The seat:** `crane.DriverComponent:SetDriver(pawn)` seats any pawn, a crew member too: they are
  attached at `DriverLocation`, their AI controller lets go of them, and the crane counts as
  occupied. `RemoveCurrentDriver()` puts them out at `GetDriverExitLocation()` and the AI takes them
  back.
- **Joints** of the dock crane, each a `UCraneJointDriverComponent` that eases towards
  `SetTargetValue(v)` (lerp factor 4) whether or not anyone is in the seat:

  | Joint | Moves | Range | Unit | Input speed |
  | --- | --- | --- | --- | --- |
  | `Joint_SwivelPlatform` | turns the crane | −180 … 180, wraps | degrees | 25 |
  | `Joint_BoomHoist` | raises the boom | 0 … 50 | degrees | 15 |
  | `Joint_TelescopicBoom` | extends the boom | 0 … 230 | 2 cm a unit | 60 |
  | `Joint_CableDrum` | pays out cable | 10 … 1000 | cm | 60 |
  | `Joint_HookRotator` | turns the hook | −180 … 180 | degrees | 90 |

- **Geometry of `BP_StationaryCrane_T01__C`** (measured by moving each joint):
  - The boom points along the crane's yaw plus the swivel value.
  - The boom is 445 cm long, pivoting 25 cm behind and 335 cm above the crane's origin, and lies 2.1°
    below level at hoist 0. At hoist 0 and telescope 0 the tip is 420 cm out and 319 cm up.
  - Telescope adds 2 cm of boom per unit, so the reach at hoist 0 is 420 to 880 cm; fully hoisted
    with no telescope it is about 274 cm.
  - The hook collider hangs 53 cm plus the paid-out cable below the tip, and stops on the ground.
- **Components worth knowing:** `CableStart` (the boom tip), `HookCollider`, `HookJoint`,
  `CraneHookVisual`, `DriverLocation`, `BoomDirrectionArrow` (the game's spelling).

### The class 3 cargo ship's deck cranes

Source: probe (two surveys on the Norland), playtest.

- **The ship** is `BP_Cargo_Boat_3_C` (`DA_CargoBoat3`). It comes with one or two deck cranes, and a
  towing rig option that changes its cargo space. With two cranes and no rig, its hold is one
  `BP_BoatCargoArea_C` of 40 × 25 × 12 cells, and it has a native "Engineering" crew group with three
  slots.
- **Its cranes** are `BP_StationaryCrane_T02_WithCable__C`, attached to the boat about 13 m apart and
  facing opposite ways. Each has a seat (`DriverComponent`) like the dock crane.
- **Large containers need the ship's crane:** the docks where they are unloaded have no crane of
  their own. One crane lifts a large container; there is no two-crane lift. Medium and large cargo
  go to separate dock areas (`UCargoGridInventory.SupportedCargoSizes`, `IsSupportedCargoSize`).
- **Every heavy piece has one `CargoHook`**, its lifting point, 0.8 to 1.35 m above its centre.
- **Joints** (all ease towards `SetTargetValue` with lerp factor 4):

  | Joint | Moves | Range | Measured |
  | --- | --- | --- | --- |
  | `Joint_SwivelPlatform` | turns the crane | −179.99 … 179.99, **stops, does not wrap** | degrees |
  | `Joint_BoomHoist` | raises the lower arm | −1 … 1 | 33.2° + 42.2° per unit; rests at 7.3° below about −0.6 |
  | `Joint_UpperArmBender` | bends the knuckle | −1 … 1 | upper arm at −48° + 50° per unit relative to the lower arm |
  | `Joint_TelescopicBoom` | extends the upper arm | 0 … 1 | 359 cm + 792 cm per unit, 1118 cm at most |
  | `Joint_CableDrum` | pays out cable | 10 … 1000 | cm |
  | `Joint_HookRotator` | turns the hook | −179.99 … 179.99 | degrees |

- **Geometry** in the crane's own frame (forward along its yaw):
  - The lower arm pivots 20 cm behind and 266 cm above the crane's origin and is 268.5 cm long to
    the knuckle.
  - The hook hangs 47 cm to the left of the arm, 3 cm beyond the upper arm's end, and 78 cm plus
    the paid-out cable below it.
  - Skeleton bones of `CraneSketelalHirerarhy`: `CraneTier2_Skel`, `base`, `Rotator`,
    `PistonA_Start`, `LowerArm`, `PistonA_End`, `PistonB_Start`, `UpperArm` (the knuckle),
    `PistonB_End`, `ExtendArmStart`, `ExtendArmMiddle`, `ExtendArmEnd` (the arm's end), `Hook`.
  - Reach goes out to about 13 m from the crane's axis.
- **The model is about half a metre out in height** when the arm is extended. Lowering the hook to a
  piece, or a load into its place, by watching the hook or the load (`HookJoint`, the piece's
  `CargoHook`) and paying out cable until it is there works where computing the cable length did not.
- **Latching:** `ServerAttachCargo` pulls the piece up to the hook, wherever the hook is. Latch with
  the hook joint about 55 cm above the piece's lifting point, as the game's own operators do, and
  start lifting at once. With the hook practically touching the piece it barely moves, the game takes
  it as still standing in its place, and sets it straight back down: the "instant unlatch".
- **Loads also come off by themselves** now and then, for the player too, without `ServerDetachCargo`
  being called through Lua; nothing in the crane's blueprint (hit events are off, there is no joint
  break handler) shows why. Latching again at once while the load still hangs by the hook works.
- **The hook turns the load** (`Joint_HookRotator`). A load hangs at the yaw it was picked up at; turn
  it to face its new place before lowering, or the game snaps it round when it is set down.
- **Attach and detach order** seen in the blueprint: `AdjustHookPositionForCargo`,
  `OnCargoAttached_Base`, `Lift Hook`, `OnCargoAttached`; and `OnCargoDetached_Base`, `CustomEvent`,
  `Lift Hook`, `OnCargoDetached`. `AttachedCargo` is set only after `ServerAttachCargo` returns.
- **Swinging:** with the swivel stopping at half a turn and a heavy load on a cable, lift the arm in
  place first, draw the telescope in, swing, then run it out again: that clears the ship's
  superstructure. Folding the whole arm in to swing pulls the load against the crane.
- **Placing:** the arm model also puts the hook a little to one side. Lining the load up by measuring
  where its `CargoHook` is against where it should be, and moving the arm by the difference until it
  is within a few centimetres, lets it drop cleanly between neighbours. Lifting the first stretch
  off a stack with the arm, not the cable, keeps it from dragging on the piece below.
- **Docking:** the Norland is often too big to dock where its cargo goes. Lying still with its
  engines off (`BoatEngine:IsEngineRunning()` false for every engine on the boat) is a usable test
  for "alongside and stopped".
- **Two cranes, one piece:** when neither crane reaches both where a piece is and where it goes, one
  can set it down in the hold where the other reaches it. Only one crane lifts at a time.
- **Its blueprint** holds `HookPhysicsConstraint`, `HookCable`, `HookAttachCollider` and a 90 kg
  physics hook (`HookCollider`, `HookTrigger`, `HookJoint`, collision profile `CraneHook`).

### Placing cargo

- **The cargo area grid** is 25 cm cells. The class 3 ship's hold is 12 x 12 x 4 cells. Large
  containers and pallets are 4 x 4 x 4 cells and fill the hold's full height, so a hold of them can
  look full while there is plenty of room if flat pieces (4 x 2 x 1) are stacked onto each other and
  the rest slid together. Clearing a single spot by moving what is in it onto the floor is not enough;
  the planner has to search (greedy, then drop moves that are not needed).
- **`ServerDetachCargo(area, position, rotation)`** puts the piece at that grid place in that area,
  wherever the area is: a piece released over an area a long way off appears there. The mod only sets a
  piece down where its crane really reaches.
- **The stationary dock crane (T01)** runs its boom out to about 8.6 m from its base; asked for a
  point further out it stops short and the game then pulls the piece to the hook.

Source: probe, playtest.

- **`FindPlaceForInventoryItemInGrid` ignores where it is asked to look** with `PositionRule = 1`
  (`Fit`): it always answers with the first free place counting from the grid's corner. With
  `PositionRule = 0` and `RotationRule = 0` (`Preserve`) it says whether the piece fits exactly at the
  place given, which is how a chosen spot can be checked.
- **Free spots** are best found from `CellsOccupancyMap` directly, as boxes of cells per item, keeping
  the piece's own rotation and its anchor (the offset of `GridTransform.position` from its lowest cell).
- **The warehouse puts cargo out** in whichever dock area it chooses, which can be far from the boat.
- **Collision while lifting and lowering:** drawing a carried piece's `SolidCollision` box in by
  10 cm did not stop it catching on its neighbours; `SetActorEnableCollision(false)` while it is lifted
  out and lowered in did. Switch it back on the moment the piece comes off the hook, or it falls
  through the dock.
- **Moving a joint goal straight to a new pose** moves all the joints at once at their own speeds, and
  the arm sags far below both poses on the way. Short steps, each a pose at the height wanted, keep it
  up.
- **Area sizes:** the class 3 ship's dock areas refused large containers in `BP_CargoSpawnArea_C` and
  `BP_CargoMissionSpawnArea_Light_C`; `BP_CargoMissionSpawnArea_Heavy_C` took them.

## Boats

Source: dump, probe, playtest.

- **Boat data:** `boat:GetBoatInfo()` gives a `UBoatInfo` data asset (`BoatName`, `Payload`,
  `MaxSpeed`, `GrossTonnage`, `StorageType`, `Tier`, `MaxCrewMembers`, …). Owned boats are in
  `CompanyOwnedBoatsComponent.ActiveBoats`.
- **Crew limits by boat type** (`MaxCrewMembers`):

  | Boat info | Max crew |
  | --- | --- |
  | `DA_ServiceShip4` | 6 |
  | `DA_ServiceBoat3`, `DA_CargoBoat3`, `DA_FishingBoat3` | 4 |
  | `DA_Ferry1`, `DA_FishingBoat4` | 3 |
  | `DA_FishingBoat1`, `DA_FishingBoat2`, `DA_CargoBoat1`, `DA_CargoBoat2`, `DA_RowBoat`, `DA_ServiceBoat1B`, `DA_ServiceBoat2`, `DA_SupportBoatC1`, `DA_SupportBoatC2`, `DA_Barge` | 0 |

  The value is read live, so raising it at runtime lets crew be assigned to that boat type.
- **Docked:** `boat.DockingComponent:IsDocked()`, with `bIsPulling` true while the boat is still being
  pulled in to the mooring point.
- **The helm:** `boat.DriverComponent:GetDriverExitLocation()` is where someone letting go of the
  wheel is put: a safe place to stand on the boat.
- **Camera:** a boat being driven has `SetFirstPersonCameraMode()` / `SetThirdPersonCameraMode()`; a
  script that moves the player off the helm has to put the camera back with these.
- **Why boats listed with people aboard** (fixed by SasBoatFixes): characters press down on what
  they stand on (`CharacterMovement.StandingDownwardForceScale` 1.0 with `bEnablePhysicsInteraction`).
  The game sets it to 0 for the player only. Heeled over in a turn at speed, the hull's righting
  force is weak enough that the push of people on the low side holds it there: the class 3 service
  vessel held 17° at 26 knots with six people aboard (about 600 kg on 131,800 kg) and levelled within
  five seconds of the push being switched off. Setting the value to 0 on every character fixes it.
- **Invert mouse in a crane** (fixed by SasBoatFixes): the game applies the settings
  `bInvertMouseX/Y` through enhanced input modifiers, `LAB132InputModifierMouseInversion` and
  `...MouseSensitivity` (and `...ControllerInversion/Sensitivity` for sticks), set on the
  `IA_CharacterLook` binding of `IMC_Character`, `IMC_BoatFirstPerson`, `IMC_Boat` and `IMC_WaterCannon`.
  `IMC_CraneContext` has `IA_CharacterLook` on `Mouse2D` and `Gamepad_Right2D` with no modifiers, so
  the settings do nothing in a crane's camera. An input context's bindings are in
  `DefaultKeyMappings.Mappings` (the plain `Mappings` array is empty). A modifier object can be
  appended to a binding's `Modifiers` at runtime and is shared; it keeps no state.
- **The wake** (fixed by SasBoatFixes): the water is NVIDIA WaveWorks, and wakes come from its
  local waves simulation over a square of water centred on
  `WaveWorksComponent.LocalWavesSimulationSettings.SimulationDomainCenter` (X, Y). The game moves
  that centre with the boat only while someone is at the helm (`boat.DriverComponent.Driver`), so a
  boat left under way sails out of the square and its wake vanishes. Setting the centre to the boat's
  position ten times a second keeps the wake, at its normal size and look.

### Taxis

Source: probe (a sweep through every taxi in the world).

Water taxis only run between a hub and its own docks:

| Hub | Docks served |
| --- | --- |
| Værøy | Mosken, Måstad, Teist Hammeren |
| Røst | Kveldsbeita, Kårøya, Lyngvær, Stavøya, Vedøya |
| Bodø | Fenes, Helligvær |
| Sørvågen | Moskenes, Reine, Å |

The Sørvågen taxi itself listed no destinations.

## Crew

Source: dump, probe, playtest.

- **Layout on a boat with crew** (`SasBoatCrewComponent` on the boat):
  - `CrewMembers` (`SasCrewMember`, created in `/Engine/Transient`), `CrewTaskCategories`,
    `CrewTasksDefinitions`.
  - `CrewGroups`: `SasCrewGroup` objects owned by the component (`SasCrewGroup_0`, …), each with
    `GroupName`, `CrewTaskCategory`, `CrewSlots`, `ScheduledTasks`.
  - Slots: `SasCrewSlot` objects owned by their group, each with `CrewGroup`,
    `SlotId { TaskGroupCategory, SlotIndex }`, `PrimarySlot` (slot 0 of the group), `CrewMember`,
    `TaskInstance`, `bAllowTaskAssignment`.
  - `SlotMap`: (category, index) → slot.
  - Fishing boat 3 has a "Fishing" group with 2 slots and an "Engineering" group with 1.
- **Categories:** `DA_CrewTaskCategory_Fishing`, `_Baiting` (under Fishing), `_Kiosk`,
  `_Engineering`. Engineering has no jobs at all.
- **Jobs** are `SasCrewTaskDefinition` assets: display name, category, start conditions
  (`SasCrewTaskCondition_FindSmartObjectActor`, `_FindInventory`, `_FindInventoryItem`,
  `_FindInteractable`, plus blueprint conditions) and a behaviour tree. The game has fish gutting,
  fish sorting, longline hauling, production-line transfer, six baiting jobs and the kiosk seller.
- **A crew member** (`SasCrewMember`): `CrewMemberName`, `Profile`, `Boat`, `Character`
  (`BP_SasCrewMemberCharacter_C`, driven by `BP_SasCrewAiController_C`), `CrewSlotId`, `CrewSlot`.
  The company's crew is in `SasCompanyCrewComponent.CrewMembers`.
- **Commands** are on the player state's `CompanyEmployeeComponent`: hire, fire, assign to boat,
  assign to slot, schedule a job, and the matching removals.
- **A boat without crew positions** (class 2 cargo boat) has a crew component with nothing in it.
  Building a group and slot the same way as above, adding them to `CrewGroups`, `SlotMap` and
  `CrewTaskCategories`, and raising `MaxCrewMembers` makes the position appear in the crew chart.
- **The game declines its own assign calls for a slot it did not build.** Both
  `AssignCrewMemberToSlot` and `ServerAssignCrewMemberToSlot` run without error and change nothing,
  even though the chart's `CanAssignCrewMember` says yes. Why is not known. Writing `slot.CrewMember`
  and `member.CrewSlot` / `member.CrewSlotId` by hand and calling `slot:OnRep_CrewMember(nil)` and
  `member:OnRep_CrewSlot()` works.
- **The crew chart caches.** `SasCrewChartViewModel` copies the boat's groups when first opened and
  does not refresh its "Resting" list after hand-made changes. `Initialize(sameComponent)` is
  ignored; `Initialize(otherComponent)` followed by `Initialize(component)` rebuilds it.
- **Saves:** a hand-made group and slot are not saved. After a load the crew member is still
  assigned to the boat, resting, with a stale `CrewSlotId` label; nothing breaks.
- **A crew member's old `CrewSlotId` stays set** after they leave a slot (it still said "Fishing #0"
  on a boat with no Fishing group), so check `CrewSlot`, not `CrewSlotId`, to see if they are seated.
- **Moving crew about:**
  - The AI cannot path anywhere on the class 2 cargo boat's deck: `MoveToLocation` with path finding
    fails at once, and a straight-line move is accepted but the character only turns. On a dock they
    path freely.
  - Idle crew run behaviour trees (`BT_EatChips`, `BT_Exercise`, …), which keep running in a crane
    seat. `controller.BrainComponent:StopLogic("…")` stops them and `RestartLogic()` restarts them.
  - `character:K2_SetActorLocation(place, false, {}, true)` moves them.
  - `character.BasedMovement.MovementBase:GetOwner()` (and its attach parents) says what they are
    standing on.

## Interface

Source: dump, playtest.

- **Inventory slots:** each slot of an inventory window is a `WBP_ItemInventorySlot_C`; its item is in
  `ViewModelData.Item` (crane cargo lists use `SlotData.Item`), and its window is
  `slot.WBP_ItemInventory`.
  - Tint a slot with `SetColorAndOpacity` on the widget and on `slot.Icon`, and fade it with
    `SetRenderOpacity`.
  - Taking an item out refills every slot and resets its look, so a tint applied once a second
    flickers. Re-tint from hooks on `SetSlotData` and `OnListItemObjectSet`, plus a check 30 ms
    later.
- **Toasts:** `Default__SasToastFunctionLibrary:SendSimpleToast(controller, channel, FText(text),
  controller)`, with channel `Channel.Toast.Simple.Info` (also `.Warning`, `.Fatal`).
  **A message containing a colon is silently dropped.**
- **In-world mission markers:**
  - The class is `SasEmployeeMissionComponent.MissionMarkerClass` (`BP_MissionMarker_C`). Spawn one,
    then fill `MarkerData`: `Icon.ResourceObject`
    (`/Game/ShipsAtSea/UI/WorldMap/Materials/Circle/MI_CargoMapIcon_Green`), `Icon.ImageSize` 86 × 86,
    `MissionMarkerTag` `UIMapMarker.Type.Marker.Mission.Cargo.M`, `WidgetTitle`, `Instigator`,
    `AttachedToActor`, `Location`.
  - Then call `marker:Update(marker.MarkerData)`, `K2_AttachToActor(cargo, …)` and
    `SetVisibility(true)`. `marker:Init(otherMarker.MarkerData)` copies a real marker's look.
  - The game's own markers have a mission object as `Instigator`; giving ours the player controller,
    or a title of our own, is how a mod tells its markers apart.

## Cheats and test saves

Source: dump, playtest.

- **The cheat manager** `SasCheatManager` has, among many others: `ModifyMoney(delta)`,
  `AddTravelledDistance(km)`, `AddBoat(type)`, `AddItem(id)`, `SkipTime(days)`, `TeleportTo(x, y, z,
  yaw)`, `SetGameSpeed`, `LevelUpAllSkills`, `DockActiveBoat`.
- **The release build has no cheat manager of its own**, and calling `controller:EnableCheats()`
  **hangs the game**. Building one with `StaticConstructObject(controller.CheatClass or
  SasCheatManager, controller)` and setting `controller.CheatManager` works, and `ModifyMoney` and
  `AddTravelledDistance` then do what they say. `CheatManagerEnablerMod` does the same each time a
  save is loaded, so one only goes missing between installing the mods and the next load.
- **Licences** (`BoatCertificate`) unlock by distance travelled (`RequiredDistance`, km). The figures
  for each licence were not recorded.
- The game keeps a "cheats were added" flag per player (`SetWasCheatsAdded`); what it affects is not
  known. Use a separate save.

## Unanswered questions

- Why the game's assign calls refuse a slot the game did not build.
- What Ctrl+Alt+S does in the game.
- Why a crew member on the cargo boat's deck cannot walk anywhere, and whether some boats have crew
  navigation on deck.
- The full stacking rules for cargo: what may go on what, and whether `FindPlace` ever picks a
  stacked place over a floor place.
- Whether emptying `HighlightedCellsMap` has any side effect when a player then uses the crane.
- Which dock cargo area the game intends unloaded cargo to go to; setting it down in the port's own
  `BP_CargoArea_C` counts as delivered.
- The distance each licence needs.
- Whether the class 3 cargo boat (crew limit 4) has a crew position that could operate a crane
  without any of the crew-chart work above.
- How any of this behaves in multiplayer; everything here was tested solo.
