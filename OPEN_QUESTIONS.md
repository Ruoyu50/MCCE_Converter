# Open Questions

Things mcce has investigated but not resolved. Anyone picking up research on
NetEase iPad save mechanics can start here.

## 1. Game Mode field mechanics

**Question**: when does the iPad NetEase client write
`~local_player.PlayerGameMode`, and when does it read it?

**What we know**:
- iPad creates new worlds with `PlayerGameMode = 5`. We first read this as an
  invalid sentinel, but online research (below) shows **5 = "Default"** is a
  documented, valid Bedrock value (the player follows the world's default game
  mode). The earlier "player falls to (0, -2, 0)" failure was actually the
  Chunker 9-field player stub (missing `identifier`/`definitions`/etc.), NOT
  `PlayerGameMode = 5`.
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

**Online research findings (2026-05-20)** (source: minecraft.wiki):
- **Q1 — what `PlayerGameMode`/`GameType` = 5 means: ANSWERED.** Bedrock's
  game-mode enum is `0=Survival, 1=Creative, 2=Adventure, 5=Default,
  6=Spectator` (NOT Java's 0–3). minecraft.wiki "Bedrock Edition level format"
  documents GameType verbatim: *"0 is Survival, 1 is Creative, 2 is Adventure,
  5 is Default, and 6 is Spectator."* "Default" (5) means the player follows the
  **world's default game mode** (the one chosen on the Create-New-World screen /
  world settings), not an explicit personal override. So `PlayerGameMode = 5` is
  "inherit world default" — a normal valid value. This **corrects our earlier
  "invalid sentinel" reading**; the (0,-2,0) failure was a separate cause (the
  Chunker stub's missing identity fields).
- **Q2 — when 5 becomes an explicit 0/1/2/6: PARTIALLY ANSWERED** (from the enum
  semantics; no single authoritative trigger-list found). Since 5 = "follow
  world default", the player stays at 5 until they explicitly set their
  *personal* game mode (an in-game personal-mode switch), which writes the
  explicit value. Switching the *world default* changes the world's default
  (which a Default/5 player then follows). That explains why both "switch
  default" and "switch personal" were observed to change behaviour, while
  "just created, never touched personal mode" keeps 5.
- **Still unresolved:** where iPad stores the *world default game mode* that a
  Default(5) player follows. Phase 3e overrode `level.dat.GameType` and iPad
  ignored it on load — so the honored world-default-mode is NOT
  `level.dat.GameType`; it lives in the new-world UI selection state, outside
  the save data. Separately, an *explicit* `PlayerGameMode` we wrote (Phase 3d,
  value 1) was also ignored on load.
- Keywords searched: "Bedrock PlayerGameMode 5 default", "bedrock world default
  game mode", "PlayerGameMode not set / invalid", "level.dat GameType values
  bedrock". Authoritative pages: minecraft.wiki "Bedrock Edition level format",
  "Game mode", "Commands/gamemode". Did NOT find an authoritative
  trigger-condition list for exactly when the engine writes `PlayerGameMode`.

**Why it matters**: would let users export a freshly-created iPad blank world
and use it as a template for `java-to-netease`, instead of needing a save with
real play history.

**Status**: shelved 2026-05-20. See
`EXPERIMENTS/02_gamemode_field_mechanics.md` for the data this question is based
on.

## (Future open questions go here.)
