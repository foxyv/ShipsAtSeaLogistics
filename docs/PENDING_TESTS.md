# Pending tests

Changes made but not yet seen working in play. Nothing here needs setting up on purpose: note it
when it comes up naturally, then tick it off or report what happened. Test after a full game restart,
not Ctrl+R. Keep the debug logging in until these are done.

## SasCraneOperator

- [ ] **Crane switches are batched.** When the next piece needs the other crane, the operator first
  does the other work the crane he is in can reach. Look for `staying in this crane:` in
  `ue4ss\UE4SS.log`. The case that failed before: tyres standing on a building-materials pallet marked
  to unload (he moved to the other crane straight away, with the same "moving a BP_Tires_L_C off ..."
  line seven times).
- [ ] **Why a crane was chosen.** Whenever he picks a crane other than the one he is in, the log has
  `choosing <crane> for the <cargo>: ...`, saying which cranes can reach both places and the
  distances. Check the reasons look right.
- [ ] **A piece teleporting into the hold on release.** Every drop now writes a `DROP:` line (where
  the piece was when let go, where the hook was, where it was sent, and how far apart those are) and
  a `DROP+:` line a second later (where it ended up, and how far it moved since release). A jump of
  more than a few centimetres between release and the sent-to place is the game snapping the piece
  into the grid; a large one points at the lowering, not the game.
- [ ] **Rearranging a full-looking hold.** When a piece does not fit and clearing one spot fails, the
  operator now plans moves across the whole hold (stacking flat pieces, sliding others together).
  The log says `a 4x4x4 piece does not fit: N move(s) to rearrange the hold`. Tested offline on the
  Dr. Phil hold from the log (12x12x4, 11 pieces): 3 moves for the first piece, 6 for a second.
  In play, check each move is possible for the crane, and that he does not shuffle the same
  pieces back and forth afterwards.
- [ ] **Hook over the crate before it comes down.** The log has an `AIM:` line per pick saying how
  far the hook was from the piece's lifting point after how many corrections. A crate that was
  grabbed half a metre to the side should now be taken squarely.
- [ ] **Dock crane reach.** A piece to load further out than the dock crane's boom (about 8.6 m) is
  first brought to the nearer dock area (`bringing the ... first: it is out of the crane's reach`),
  not pulled to the hook.
- [ ] **Nothing put ashore out of reach, and the dock crane needs the boat docked.** Shutting down
  an engine 120 m off a port must not unload anything; the toast says the dock is out of the crane's
  reach. The class 1 and 2 boats' dock crane works only with the boat made fast.
- [ ] **Caught on something.** If a load snags on the way, the crane lifts a metre and carries on, a
  few times at most (`caught on something` in the log).
- [ ] **Steadier hook for the player.** Driving a crane yourself, a load that comes off the hook within
  1.5 s of latching is latched again.
- [ ] **Taking the helm again after the wake fix** (SasBoatFixes). The wake should not jump or lose a
  section when you go back to the helm after it has been held on the boat.

## ShipsAtSeaLogistics

- [ ] **Send orders to the crane operator.** Ticked, the operator works the planned loads and unloads
  as before. Unticked, he moves only pieces marked by hand with F5. Highlighting works with either
  setting. Start the planner before the game so `crane_orders.txt` exists.

## Done

Confirmed in play, kept here for reference: the operator put back on the boat and in the position
after a load (`putting them back on the boat` in the log); Shift+F5 switching the hover hint off and on; the
invert mouse setting in a crane's camera (both settings); the crane's motor sounds; the wake keeps going when you leave the helm; parking
along one route and smooth motion between steps; Røst's large-cargo dock recognised as Røst.
