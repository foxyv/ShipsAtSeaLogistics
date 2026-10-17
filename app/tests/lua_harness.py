"""Loads the SasLogistics script inside Python against a stand-in for UE4SS, so its logic can be tested.

The script is the real one, unchanged. Before it is loaded, `SAS_TEST` is set, which makes it hand out
its local functions and state in the global `SasTest` (see the end of the script). Everything UE4SS would
provide is stubbed in STUB; the game's objects are Python classes below (Lua calls `obj:Method()` on
them as it would on the game's). The script's side files (operators.txt and so on) land in a temporary
folder, never in the repository or the game.
"""

from __future__ import annotations

import math
import tempfile
from pathlib import Path

from lupa import LuaRuntime

MOD_NAME = "SasLogistics"
SCRIPT = Path(__file__).resolve().parents[2] / "mod" / "SasLogistics" / "Scripts" / "main.lua"

STUB = r"""
Printed = {}
print = function(text) Printed[#Printed + 1] = text end
Timers = {}
function LoopInGameThreadWithDelay(ms, fn) Timers[#Timers + 1] = { ms = ms, fn = fn } end
function LoopAsync() end
function ExecuteInGameThread() end
function ExecuteWithDelay() end
function RegisterHook() return 1, 1 end
function RegisterKeyBind() end
function NotifyOnNewObject() end
function FindAllOf() return nil end
function FindFirstOf() return nil end
function StaticFindObject() return nil end
function StaticConstructObject() return nil end
function LoadAsset() return nil end
function CreateInvalidObject() return nil end
function FName(text) return text end
function FText(text) return { ToString = function() return text end } end
Key = setmetatable({}, { __index = function(_, name) return name end })
ModifierKey = setmetatable({}, { __index = function(_, name) return name end })
"""


class Vec:
    """A vector or rotator the game would return: attributes, as Lua reads them."""

    def __init__(self, **fields):
        self.__dict__.update(fields)


class GameObject:
    """The part of a game object the script asks every object: is it valid, which one is it, what class."""

    _next = 1000

    def __init__(self, class_name="Object"):
        GameObject._next += 1
        self._address = GameObject._next
        self._class_name = class_name

    def IsValid(self):
        return True

    def GetAddress(self):
        return self._address

    def GetFullName(self):
        return f"{self._class_name} /Test/{self._class_name}_{self._address}"

    def GetClass(self):
        return Named(self._class_name)


class Named:
    def __init__(self, name):
        self._name = name

    def GetFName(self):
        return self

    def ToString(self):
        return self._name


class Joint:
    """A crane joint: the script sets a target, the joint moves towards it at a limited speed."""

    def __init__(self, value=0.0, speed=60.0):
        self.value, self.target, self.speed = value, value, speed

    def GetCurrentValue(self):
        return self.value

    def SetTargetValue(self, target):
        self.target = target

    def step(self, seconds):
        move = max(-self.speed * seconds, min(self.speed * seconds, self.target - self.value))
        self.value += move


class FakeCrane(GameObject):
    def __init__(self, class_name="BP_StationaryCrane_T02_WithCable_C", location=(0.0, 0.0, 0.0), yaw=0.0):
        super().__init__(class_name)
        self.location, self.yaw = location, yaw
        self.AttachedCargo = None
        self.Joint_HookRotator = Joint()

    def K2_GetActorLocation(self):
        return Vec(X=self.location[0], Y=self.location[1], Z=self.location[2])

    def K2_GetActorRotation(self):
        return Vec(Pitch=0.0, Yaw=self.yaw, Roll=0.0)


class FakeLoad(GameObject):
    """A load hanging from the hook rotator. Its facing follows the hook's turn a little behind it, and what
    the script reads can be jittered, as a swinging load's is."""

    def __init__(self, hook: Joint, start_yaw=0.0, direction=1.0, lag=0.6, noise=0.0, rng=None):
        super().__init__("BP_Pallet_C")
        self.hook, self.base, self.direction, self.lag, self.noise = hook, start_yaw, direction, lag, noise
        self.yaw = start_yaw
        self.rng = rng
        self.responds = True

    def step(self, seconds):
        if self.responds:
            target = self.base + self.direction * self.hook.value
            self.yaw += (target - self.yaw) * (1 - math.exp(-seconds / self.lag))

    def K2_GetActorRotation(self):
        jitter = self.rng.uniform(-self.noise, self.noise) if self.noise and self.rng else 0.0
        return Vec(Pitch=0.0, Yaw=((self.yaw + jitter + 180) % 360) - 180, Roll=0.0)


class Wrapped:
    """What the game's containers hand out: the element is got with :get()."""

    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value


class FakeArray:
    def __init__(self, items):
        self.items = list(items)

    def ForEach(self, fn):
        for index, item in enumerate(self.items, 1):
            fn(index, Wrapped(item))


class FakeMap:
    def __init__(self, pairs):
        self.pairs = list(pairs)

    def ForEach(self, fn):
        for key, value in self.pairs:
            fn(Wrapped(key), Wrapped(value))


class FakeText:
    def __init__(self, text):
        self.text = text

    def ToString(self):
        return self.text


class FakeItem(GameObject):
    """A piece of cargo's inventory item: it has a GUID (four 32-bit numbers) and a name."""

    def __init__(self, guid=(1, 2, 3, 4), name="Pallet"):
        super().__init__("InventoryItem")
        self.Guid = Vec(A=guid[0], B=guid[1], C=guid[2], D=guid[3])
        self.name = name

    def GetDisplayName(self):
        return FakeText(self.name)


class FakeCargo(GameObject):
    """A piece of crane cargo standing in the world."""

    def __init__(self, item):
        super().__init__("BP_Pallet_C")
        self.item = item

    def GetRepresentedInventoryItem(self):
        return self.item


class FakeInventory:
    """A grid inventory holding pieces: each is (item, x, y, z, size x, y, z[, yaw])."""

    def __init__(self, size, pieces):
        self.GridSize = Vec(X=size[0], Y=size[1], Z=size[2])
        cells, entries = [], []
        for piece in pieces:
            item, x, y, z, sx, sy, sz = piece[:7]
            yaw = piece[7] if len(piece) > 7 else 0.0
            cells += [(Vec(X=a, Y=b, Z=c), item) for a in range(x, x + sx) for b in range(y, y + sy) for c in range(z, z + sz)]
            entries.append(Vec(InventoryItem=item, GridTransform=Vec(
                position=Vec(X=x, Y=y, Z=z), Rotation=Vec(Pitch=0.0, Yaw=yaw, Roll=0.0))))
        self.CellsOccupancyMap = FakeMap(cells)
        self.GridInventoryItems = Vec(Entries=FakeArray(entries))


class FakeArea(GameObject):
    """A cargo area (a boat's hold, say) that can be changed between one look and the next."""

    def __init__(self, size=(12, 12, 4), pieces=(), class_name="BP_BoatCargoArea_C"):
        super().__init__(class_name)
        self.size = size
        self.set_pieces(pieces)

    def set_pieces(self, pieces):
        self.inventory = FakeInventory(self.size, pieces)

    def GetInventory(self):
        return self.inventory


class Mod:
    """The loaded script. `lua` is the runtime, `test` the SasTest table, `printed` what it logged."""

    def __init__(self, lua, folder):
        self.lua, self.folder = lua, folder
        self.g = lua.globals()
        self.test = self.g.SasTest

    @property
    def folder_path(self) -> Path:
        """The temporary folder the script thinks it lives in (its own folder is SasCraneOperator inside it)."""
        return Path(self.folder.name)

    @property
    def printed(self) -> list[str]:
        return [self.g.Printed[i] for i in range(1, len(self.g.Printed) + 1)]

    def table(self, value):
        """A Python dict or list as a Lua table (recursively)."""
        if isinstance(value, dict):
            return self.lua.table_from({k: self.table(v) for k, v in value.items()})
        if isinstance(value, (list, tuple)):
            return self.lua.table_from([self.table(v) for v in value])
        return value

    def lua_function(self, source):
        return self.lua.eval(source)


def load_mod(source: str | None = None, files: dict[str, str] | None = None) -> Mod:
    folder = tempfile.TemporaryDirectory()
    scripts = Path(folder.name) / MOD_NAME / "Scripts"
    scripts.mkdir(parents=True)
    # Files the script finds waiting when it starts, by path under the temporary folder (the mod's own folder
    # is SasCraneOperator, its neighbour SasLogistics).
    for name, content in (files or {}).items():
        path = Path(folder.name) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    (Path(folder.name) / "SasLogistics").mkdir(exist_ok=True)
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.execute(STUB)
    lua.globals().SAS_TEST = True
    loader = lua.eval("function(source, name) return load(source, name) end")
    # The chunk's name is where the script thinks it lives, so its side files go to the temporary folder.
    chunk = loader(source if source is not None else SCRIPT.read_text(encoding="utf-8"), "@" + str(scripts / "main.lua"))
    chunk()
    mod = Mod(lua, folder)
    return mod
