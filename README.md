# Ships At Sea Logistics

Logistics planner for Ships At Sea. A UE4SS Lua mod exports contracts, cargo and warehouse contents
from the running game, and a PySide6 app turns that into a contract list, a view of the hold, and
route plans. The mod can also highlight, in the game's inventory windows, the cargo to load next.

## Install

You need Windows, the Steam version of Ships At Sea, and two things installed first.

**1. UE4SS, experimental build.** The game runs on Unreal Engine 5.7, which the stable UE4SS
release does not support.

- Download `UE4SS_v3.0.1-<build>.zip` from
  https://github.com/UE4SS-RE/RE-UE4SS/releases/tag/experimental-latest
- Extract it into the game's `SAS\Binaries\Win64` folder, next to `Sas-Win64-Shipping.exe`. You
  should end up with `dwmapi.dll` and a `ue4ss` folder there.

**2. Python 3.10 or newer**, from https://www.python.org/downloads/, with the `py` launcher (the
installer includes it by default).

**Then install the planner:**

1. Download `ShipsAtSeaLogistics-<version>.zip` from the
   [latest release](https://github.com/foxyv/ShipsAtSeaLogistics/releases/latest) and extract it
   anywhere you like. Keep the folder; the app runs from it.
2. Copy the `mod\SasLogistics` folder from the extracted files into the game's
   `SAS\Binaries\Win64\ue4ss\Mods` folder, so that
   `ue4ss\Mods\SasLogistics\Scripts\main.lua` exists.
3. Open PowerShell in the extracted folder and install the app's one dependency:

   ```
   py -m pip install -r app\requirements.txt
   ```

4. Start the planner:

   ```
   powershell -ExecutionPolicy Bypass -File .\run.ps1
   ```

   To get desktop and Start Menu shortcuts with the app's own icon instead:

   ```
   powershell -ExecutionPolicy Bypass -File .\install-shortcut.ps1
   ```

If your game is in the default Steam library on `D:`, `deploy.ps1` does step 2 for you; otherwise
pass it the game folder: `.\deploy.ps1 -GameDir "X:\SteamLibrary\steamapps\common\Ships At Sea"`.

### First run

1. Start the game and load your save. The mod loads with the game.
2. Press **F8** in game. The planner picks the snapshot up within a few seconds.
3. Press **F7** to keep it updated every 20 seconds while you play.

The planner looks for the snapshot in every Steam library on the machine. If it says "No snapshot
yet", use the **Snapshot file…** button to point it at
`SAS\Binaries\Win64\ue4ss\Mods\SasLogistics\snapshot.json`.

### Uninstall

Delete `ue4ss\Mods\SasLogistics` from the game folder, delete the extracted folder, and delete the
two "Ships At Sea Logistics" shortcuts if you created them.

## Keys in game

- **F8** writes a snapshot now.
- **F7** toggles writing one every 20 seconds. It is off after each game start.
- **F6** toggles highlighting. In the warehouse window and the crane's cargo list, cargo to load is
  tinted green and cargo to unload orange. In the world, each piece of cargo in the hold that comes
  off at this port gets a floating "UNLOAD" marker, like the game's own markers for a tracked
  mission, so it can be picked out from the crane.

With [SasCraneOperator](https://github.com/foxyv/SasCraneOperator), the planner's **Send orders to the
crane operator** box has the boat's crane operator load and unload this port's cargo as planned. It
is separate from highlighting: either can be on without the other.
Cargo the route only "leaves in the warehouse" (not delivered at this port, put off at the stop
nearest its destination) is highlighted but not sent to the operator.

The mod reads game state, restyles inventory slots and places its own markers over cargo. It never
changes cargo, contracts, money or anything else the game saves.

## How the planner behaves

- A route is planned once and then followed: work done at the current stop is ticked off and the
  route advances when the boat docks at the next stop. It is replanned on request, when a setting
  changes, when it is finished, or when the boat docks at a port it does not visit.
- The boat counts as docked within 120 m of one of a port's dock cargo areas, or within 300 m of the
  port itself, and as in transit otherwise. In transit the
  start port reads "In transit", nothing switches to whichever port happens to be nearest, and a
  plan made then starts from the boat's own position.
- A contract whose cargo is all in its destination's warehouse is shown as ready to complete, with
  an instruction at that port to take the cargo out of the warehouse, which is what finishes it.
  When the boat is bringing the last of a contract whose other cargo is already stored there, the
  stop says so up front: unload, then take the stored part out too.
- At the port the boat is docked in, a load check compares what is on board with that stop's
  instructions: how much is loaded, what is still to load or unload, and anything aboard that the
  plan does not expect. **Check load now** asks the game for a fresh snapshot first.
- Distances are sea routes, not straight lines. The first time a save is loaded the mod samples the
  game's water depth across the map (about 20 seconds, once) into `depthgrid.txt`, and the planner
  works out the shortest route over water between every pair of ports (about 15 seconds, once)
  and saves them in `searoutes.json`. Until then it uses straight lines.
- The Map tab draws the coastline from the same depth map, north up, with the ports, how much
  cargo is waiting at each, your boat, and the planned route following its sea routes. The wheel
  zooms, dragging pans, and a double-click fits the map to the window again.
- Priorities: most money per distance, fewest stops, fullest loads. Distances can be shown in km,
  nautical miles or miles.
- Cargo keeps its place in the hold from loading until it comes off; nothing is re-stowed.
- A contract's cargo is loaded together where it fits. Splitting can be switched off entirely.
- Spare space carries waiting cargo closer to its destination; cargo on board that the route does
  not deliver is left at the stop nearest its destination.

## Known limits

- Tested in solo play only.
- Sea routes follow water at least 5 m deep on a 50 m grid. They are not the game's own route
  planner, which also keeps a clearance from the coast, so lengths will differ a little from it.
  Port pairs with no route found fall back to a straight line.
- New cargo is placed first-fit; where the player actually sets it down with the crane may differ.
- No weight limit was found in the game data, so the weight limit is entered by hand.
- The route search is a beam search: a heuristic, not an exact optimiser.

## Related

Boat handling fixes for the same game, including a fix for boats listing with passengers aboard:
https://github.com/foxyv/SasBoatFixes

## Working on it

- `mod/SasLogistics` — the UE4SS Lua mod. It must do all its work on the game thread, from a single
  timer; an earlier version used two background timers and crashed the game.
- `app/saslogi` — the planner: `model.py` loads a snapshot, `packing.py` fits cargo into the hold's
  cell grid, `planner.py` searches for routes, `searoutes.py` works out sea distances from the
  depth grid, `mapview.py` draws the map, `ui.py` is the window.

```
.\deploy.ps1              # copy the mod into the game
.\run.ps1                 # start the planner
.\package.ps1 v0.1.0      # build the release zip into dist\
cd app; py -m unittest discover tests
```

Ctrl+R in game reloads UE4SS Lua mods without a restart, if `EnableHotReloadSystem = 1` is set in
`ue4ss\UE4SS-settings.ini`.

## License

Everything here is under the MIT license: see `LICENSE`. The mod folder carries its own copy so the
license travels with the mod when it is deployed or shared without the rest of the repo.
