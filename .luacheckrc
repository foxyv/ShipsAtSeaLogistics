-- luacheck settings for a UE4SS Lua mod. Run with tools/check_lua.py.
--
-- The mod's scripts run inside UE4SS, which provides the functions and objects below. Names the script
-- defines at the top level of a file are allowed as globals: they are deliberate (Lua allows only 200 local
-- variables in a file, and a big mod keeps its overflow in globals). Any other global, read or written, is
-- reported, because that is nearly always a typo.

std = "lua53"          -- the newest luacheck knows; UE4SS runs Lua 5.4, which is close enough
allow_defined_top = true
max_line_length = false
unused_args = false    -- callbacks take arguments they do not use
self = false

ignore = { "131/SasTest" }  -- SasTest is only read by the tests (see the end of the script)

read_globals = {
    -- UE4SS functions
    "FindAllOf", "FindFirstOf", "StaticFindObject", "StaticConstructObject", "LoadAsset", "RegisterHook",
    "UnregisterHook", "RegisterKeyBind", "IsKeyBindRegistered", "NotifyOnNewObject", "ExecuteInGameThread",
    "ExecuteWithDelay", "LoopInGameThreadWithDelay", "LoopAsync", "FText", "FName", "CreateInvalidObject",
    "RegisterConsoleCommandHandler", "RegisterConsoleCommandGlobalHandler", "RegisterProcessConsoleExecPreHook",
    "RegisterBeginPlayPostHook", "RegisterInitGameStatePostHook", "RegisterLoadMapPostHook",
    "RegisterCustomEvent", "RegisterULocalPlayerExecPreHook", "RegisterEngineTickPostHook", "UObject",
    "FindObject", "FindObjects", "ForEachUObject", "GetKeyName", "Key", "ModifierKey", "EObjectFlags", "EInternalObjectFlags",
    "CreateLogicModsDirectory", "Class", "FOutputDevice", "print",
    "SAS_TEST",  -- set by the tests before the script is loaded
}
