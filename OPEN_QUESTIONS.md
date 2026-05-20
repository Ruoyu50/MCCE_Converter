# Open Questions

Things mcce has investigated but not resolved. Anyone picking up research on
NetEase iPad save mechanics can start here.

## 1. Game Mode field mechanics

**Question**: when does the iPad NetEase client write
`~local_player.PlayerGameMode`, and when does it read it?

**What we know**:
- iPad creates new worlds with `PlayerGameMode = 5` (invalid sentinel, outside
  the standard 0=survival/1=creative/2=adventure/3=spectator range). This makes
  the player fail to instantiate (the player falls to (0, -2, 0)) until
  `PlayerGameMode` becomes a valid value.
- iPad writes a valid `PlayerGameMode` to `~local_player` when the user switches
  game mode in-game (either default or personal).
- iPad does NOT use `~local_player.PlayerGameMode` to decide the loaded game
  mode — that decision comes from the new-world UI selection, which is stored
  somewhere we can't write to (probably app-sandbox or device-level state,
  outside the save data).
- iPad also does NOT use `level.dat.GameType` for the loaded mode (tested by
  overriding it; iPad ignored the override).

**What we don't know**:
- A reliable in-iPad workflow to convert a fresh blank world's
  `PlayerGameMode=5` to a valid value, so that fresh blank worlds can serve as
  clean templates.
- Whether `PlayerGameMode` is read by iPad in any code path other than the
  player-entity-instantiation check (which is the failure we hit with `5`).
- Where iPad actually stores its "what mode did the user pick when creating this
  world" state.

**Why it matters**: would let users export a freshly-created iPad blank world
and use it as a template for `java-to-netease`, instead of needing a save with
real play history.

**Status**: shelved 2026-05-20. See
`EXPERIMENTS/02_gamemode_field_mechanics.md` for the data this question is based
on.

## (Future open questions go here.)
