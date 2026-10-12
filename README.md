# Ships At Sea Logistics

Logistics planner for Ships At Sea. A UE4SS Lua mod exports contracts, cargo and warehouse contents
from the running game, and a PySide6 app turns that into a contract list, a view of the hold, and
route plans. The mod can also highlight, in the game's inventory windows, the cargo to load next.

## Parts

- `mod/SasLogistics` — UE4SS Lua mod. It reads game state and restyles inventory slots; it never
  changes game state.
  - F8 writes `snapshot.json` into its own folder under the game's `ue4ss\Mods\SasLogistics`.
  - F7 toggles writing it every 20 seconds.
  - F6 toggles highlighting the cargo listed in `highlight.txt`, which the app writes.
- `app/saslogi` — the planner. It polls the snapshot and refreshes on its own.
  - `model.py` loads a snapshot, `packing.py` fits cargo into the hold's cell grid, `planner.py`
    searches for routes, `ui.py` is the window.

## Requirements

- UE4SS experimental build installed in `SAS\Binaries\Win64` (the game is on Unreal Engine 5.7).
- Python 3 with PySide6, reachable through the `py` launcher.

## Use

```
.\deploy.ps1              # copy the mod into the game; Ctrl+R in game reloads UE4SS Lua mods
.\run.ps1                 # start the planner
.\install-shortcut.ps1    # desktop and Start Menu shortcuts with the app's own taskbar identity
cd app; py -m unittest discover tests
```

## How the planner behaves

- A route is planned once and then followed: work done at the current stop is ticked off and the
  route advances when the boat docks at the next stop. It is replanned on request, when a setting
  changes, when it is finished, or when the boat docks at a port it does not visit.
- Priorities: most money per km, fewest stops, fullest loads.
- Cargo keeps its place in the hold from loading until it comes off; nothing is re-stowed.
- A contract's cargo is loaded together where it fits. Splitting can be switched off entirely.
- Spare space carries waiting cargo closer to its destination; cargo on board that the route does
  not deliver is left at the stop nearest its destination.

## Known limits

- Distances are straight lines between ports, not sea routes.
- New cargo is placed first-fit; where the player actually sets it down with the crane may differ.
- No weight limit was found in the game data, so the weight limit is entered by hand.
- The route search is a beam search: a heuristic, not an exact optimiser.
- The mod must do all its work on the game thread. An earlier version used two background timers
  and crashed the game.
