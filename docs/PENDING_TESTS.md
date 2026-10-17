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
  Dr. Phil hold from the log (12x12x4, 11 pieces): 3 moves for the first piece, 6 for a second. A hard
  hold can take the search up to a second (a single hitch) before it plans or gives up; it is tested in
  the SasCraneOperator repository (`python -m pytest tests`).
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
- [ ] **Loading biggest first, small pieces last.** With several pieces marked to load, the operator
  takes the widest-on-the-floor first (then the tallest) and leaves the small ones to fill gaps. In
  simulation on the real footprints this cuts loads the hold could not take from 18% to under 3%
  (under 1% with the whole-hold rearranging). In play, watch that a load the planner accepted
  now goes in, and that the extra moving of small pieces off big ones is not excessive.
- [ ] **A piece that cannot fit is reported to the planner.** When the operator finds no way to
  make room, the log has `cannot be fitted into the hold as it stands; leaving it, and telling the
  planner`, a toast says which piece, and the operator goes on with the next piece. The planner (with
  the snapshot open) drops that piece from its route and says how many it left out; once the hold
  changes (something comes off) it is planned for again.
- [ ] **Setting down furthest from the boat.** On the dock the operator fills the spot furthest from the
  boat's hold first (within the crane's reach), not furthest from the crane, so nothing is built between
  the boat and the crates still to come. The dock crane now picks by the same rule instead of taking the
  first place the game offers. Watch that she no longer lowers a load into a wall of crates by the boat.
- [ ] **Version stamps.** After a restart the log has `version ..., file checksum ...` lines for
  SasCraneOperator, SasBoatFixes and SasLogistics (seen with operator `2026-10-17.15`).
- [ ] **Taking cargo off for room.** With a full hold and a load that will not fit, a piece the planner
  leaves in the warehouse at this port (the Oil Barrel for Stavoya, say) is taken off, the biggest first,
  and the load goes in. The log says `no room for the ... even by rearranging: taking the ... off`, and a
  toast names the piece. A piece the planner did not name is never taken off, and with room to spare
  nothing is.
- [ ] **A stranded piece on the hook, and a bollard carried off by it.** With a piece hanging on the dock
  crane when the game loads: about 40 s after load the log has `MOORING: ... putting it back at ...` for
  a bollard stuck to the piece, then `... is now 0 cm from where it was put` (confirmed once, with
  version `2026-10-17.15`); the operator then sets the piece on the dock (`DROP: left on the hook`), not in
  the boat. Check that with no stray bollard nothing is moved, and that the boat is never tilted
  (`TILT:` lines appear only if it is).
- [ ] **The first runs on GitHub.** The workflows (`.github/workflows/ci.yml` in the three repositories) have
  only been run locally in clean copies. After the first push, check each Actions run goes green; if
  luacheck's download or its checksum fails there, the install step is where to look.
- [ ] **Marks and the operator agree.** A piece carries an UNLOAD marker in the game only if the operator
  will move it. A piece the route merely leaves in a warehouse (the Oil Barrel for Stavoya) has none.
- [ ] **Caught on something.** If a load snags on the way, the crane lifts a metre and carries on, a
  few times at most (`caught on something` in the log).
- [ ] **Steadier hook for the player.** Driving a crane yourself, a load that comes off the hook within
  1.5 s of latching is latched again.
- [ ] **Taking the helm again after the wake fix** (SasBoatFixes). The wake should not jump or lose a
  section when you go back to the helm after it has been held on the boat.

## ShipsAtSeaLogistics

- [ ] **Deadlines from the game.** When an urgent contract exists (small cargo, so far), press F8 and
  look in `snapshot.json` for `deadlineTicks` on that mission and `calendar.nowTicks`. The difference,
  divided by 36,000,000,000, should be the game hours left, and match the contract's timer in the game.
  If it doesn't, the clock or the deadline is in a different unit and the lateness check needs fixing.
  Also check which objective class carries the deadline (`objectives` on the mission): the export
  looks for a "CalendarWait" one.
- [ ] **Delivered pieces away from their destination.** A piece the game lists as delivered but that is
  aboard or at another port (the Pallet with dried fish was) is planned as cargo still to deliver; the
  route summary names it, the planner's hold usage matches the game's, and it no longer asks for loads
  that cannot fit. Delivering it should pay (it is thought to be a partial delivery that was reloaded).
- [ ] **Planning in the background.** Change a setting or press Replan from here: the route tab shows
  "Calculating the route…" with a moving bar and the old plan greyed, the window stays responsive, and
  the new plan replaces it when ready. Several quick changes plan once more with the last values.
- [ ] **Weighted score in the planner.** Choose "Weighted score": six weights appear, a preset fills
  them in, and every change replans. The old three priorities are unchanged.

- [ ] **Send orders to the crane operator.** Ticked, the operator works the planned loads and unloads
  as before. Unticked, he moves only pieces marked by hand with F5. Highlighting works with either
  setting. Start the planner before the game so `crane_orders.txt` exists.

## Done

Confirmed in play, kept here for reference: turning a load once, in one direction, before a set-down;
the operator's character on deck after a load; the operator put back on the boat and in the position
after a load (`putting them back on the boat` in the log); Shift+F5 switching the hover hint off and on; the
invert mouse setting in a crane's camera (both settings); the crane's motor sounds; the wake keeps going when you leave the helm; parking
along one route and smooth motion between steps; Røst's large-cargo dock recognised as Røst.
