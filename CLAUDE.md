# AutoPilot hackathon (GDG NSUT, 17 Oct 2026), 6-hour event

We write ONE file, `agent.py`, with `get_reinforcements(state) -> list[card]`, for a lane tower-defence game.
Full rules: `docs/playerGuide_event.md` (unit stats, state schema) and `docs/README_PARTICIPANT.md` (SDK usage).

## Setup (done)
- SDK 0.3.0 is installed in `.venv` (Python 3.12). `agent.py` is the organisers' starter and passes VERIFY.
- `.venv/bin/autopilot verify --agent agent.py` runs offline checks (safety scan + dry run).
- `.venv/bin/autopilot` opens the interactive menu: 1 PLAY, 2 PUBLISH PUBLIC, 3 PUBLISH PRIVATE, 4 VERIFY.
- `.venv/bin/autopilot play --agent agent.py --team <ID> --password <PW> --test-case TC01` runs PLAY from a script.
  The user must open https://13-200-183-134.sslip.io/ in Chrome, type the team ID, press ▶ and click Allow. The game runs in real time in the browser; there is NO local simulator.
- Team ID: 0060 (password: ask the user, not stored here).

## Rules that matter
- Grid x 0-10, y 0-5. Enemies spawn at x=10 and walk left in a fixed row. Any enemy crossing x=0 = LOSS.
- Card: `{"defenceId": 0-3, "defenceGridPosition": {"x":..,"y":..}}`. Return `[]` to place nothing.
- The referee drops cards that are off the board, on an occupied tile, a repeated tile, or unaffordable; the others still apply.
- Called at `phase=="start"` (has `upcomingEnemies` with `allowedRows`, `spawnSettings`, `gameSettings`), then `phase=="pause"` every `reinforcementIntervalSeconds`.
  Time limit is `state["timeBudgetSeconds"]` (15 s per the docs; the PDF says 7, so read the value, don't hard-code it). A crash or timeout places nothing.
- Only the stdlib + `autopilot_sdk` are allowed. No network except `LLMClient` (60 calls/min per team). No subprocesses.
- Coins: 1300-3900 to start on public cases, +50 per kill. Use `state["budget"]["remaining"]`.
- Score for a WIN (max 100): win 25, kills 25, defences standing 25, speed 10, coins left 15. A LOSS scores kills only (max 25). Never losing is the top priority.
- Public cases: random rows. Hidden/private cases: fixed seed, deterministic, LLM at temperature 0.
  PUBLISH PRIVATE runs ONCE and locks agent.py. Don't over-tune to the public cases.

## Units (type strings keep the game's misspellings: `defenceSolider`, `EnemClown`)
Defences: 0 Penguin 250c/200hp: when first hit, slides down its row dealing 500 to every enemy it touches, single use.
1 Soldier 200c/150hp: ~40 DPS, its row, only on its tile or in front. 2 Archer 300c/100hp: ~26 DPS, any row, targets the LOWEST row first, then the enemy nearest the base.
3 Shield 150c/700hp: no attack; enemies stop at it and projectiles/beams hit it first.
Enemies: Clown 200hp +15/s regen, ranged ~3.3 tiles, ~17 DPS. Monk 280hp, fast melee, ~36 DPS.
Wizard 500hp +15/s regen, beam ~5.3 tiles, 50 DPS after a 1.75 s wind-up. Pirate 30hp, very fast, explodes at the FIRST defence for 200 to all within ~1.3 tiles (also when killed early).

## Strategy (implemented in agent.py as rules, no LLM; not yet played live)
The starter's LLM prompt contains wrong facts (penguin "can ram again", archer "highest damage"). Replace it with a deterministic rule-based agent:
- Wizard row: penguin as the first defence (500 kills a full-HP wizard), or a shield plus 2 soldiers.
- Clown row: one soldier barely beats the regen (net ~25 DPS), so add a shield in front or a 2nd soldier.
- Monk row: soldier behind a shield.
- Pirate row: a cheap far-forward shield with nothing within 2 tiles as blast bait. Never let a pirate's blast reach a penguin.
- Don't overspend: leftover coins and surviving defences both score. Add more on pauses from kill income.
Order of work: PLAY TC01 to check the pipeline, PUBLISH PUBLIC the starter as a baseline, write the rule agent, then test on all public cases.
