"""Rule-based agent: no LLM, every decision comes from the rules below.

Each threatened row gets a shield at SHIELD_X as the blocker and soldiers behind it.
At every pause each row with enemies is checked, most urgent first:
  1. nothing in front of the leading enemy: emergency blocker (penguin when it is close)
  2. a wizard in the row: a penguin in front of the blocker (500 kills a full wizard)
  3. not enough damage to kill the row before the blocker falls: soldiers, then a penguin
  4. blocker getting weak: a fresh shield in front of it
Rows that may still get enemies keep at least a shield. Coins are kept when not needed,
since leftover coins and surviving defences both score.
If anything here crashes, the starter's fallback() still places soldiers.
"""
import sys

PENGUIN, SOLDIER, ARCHER, SHIELD = 0, 1, 2, 3
SHIELD_X = 4          # blocker column: soldiers at 2, 1, 0 stay out of a pirate blast at the shield
ENEMY_TYPES = {0: "EnemClown", 1: "EnemyMonk", 2: "EnemyWizard", 3: "EnemyPirate"}
ENEMY_HP = {"EnemClown": 200, "EnemyMonk": 280, "EnemyWizard": 500, "EnemyPirate": 30}
ENEMY_DPS = {"EnemClown": 17, "EnemyMonk": 36, "EnemyWizard": 50, "EnemyPirate": 0}
REGEN = {"EnemClown": 15, "EnemyWizard": 15}
SOLDIER_DPS = 40
PIRATE_BLAST = 200

_games = {}  # sessionID -> what the opening told us about the wave


def log(message):
    print(f"[agent] {message}", file=sys.stderr, flush=True)  # shows in the play window


def _xy(unit):
    return round(unit["position"]["x"]), round(unit["position"]["y"])


def get_reinforcements(state):
    coins = state["budget"]["remaining"]
    costs = {int(k): v for k, v in state["unitCosts"].items()}
    defences = [d for d in state["defences"] if d["health"] > 0]
    enemies = [e for e in state["enemies"] if not e["isDead"] and e["health"] > 0]
    taken = {_xy(d) for d in defences}
    try:
        raw = Planner(state, defences, enemies, coins, costs).plan()
        placements = check(raw, set(taken), coins, costs)
        log(f"rules: {[(p['defenceId'], p['x'], p['y']) for p in raw]} coins {coins:.0f}")
        return placements
    except Exception as exc:  # never lose a pause to a bug: use the starter's rule
        log(f"rules failed: {type(exc).__name__}: {str(exc)[:100]}")
    if state["phase"] == "start":
        rows = sorted({r for e in state.get("upcomingEnemies", []) for r in e.get("allowedRows") or range(6)})
    else:
        rows = sorted({_xy(e)[1] for e in enemies})
    if coins < min(costs.values()) or not rows:
        return []
    return fallback(rows, taken, coins, costs)


class Planner:
    def __init__(self, state, defences, enemies, coins, costs):
        self.state, self.defences, self.enemies = state, defences, enemies
        self.coins, self.costs = coins, costs
        self.taken = {_xy(d) for d in defences}
        self.cards = []
        self.game = self._game_info()
        queued = state["counts"]["enemiesQueued"]
        # keep enough for one emergency penguin while enemies are still to come
        self.reserve = costs[PENGUIN] if queued > 0 and self.game["reinforce"] else 0

    def _game_info(self):
        sid = self.state.get("sessionID")
        if self.state["phase"] == "start" or sid not in _games:
            wave = self.state.get("upcomingEnemies") or []
            settings = self.state.get("gameSettings") or {}
            row_types = {y: set() for y in range(6)}
            counts = {}
            for group in wave:
                kind = ENEMY_TYPES.get(group.get("enemyId"), "EnemClown")
                counts[kind] = counts.get(kind, 0) + group.get("count", 0)
                for y in group.get("allowedRows") or range(6):
                    row_types[y].add(kind)
            if not wave:  # unknown (agent reloaded mid-game): assume anything anywhere
                row_types = {y: set(ENEMY_HP) for y in range(6)}
                counts = {k: 1 for k in ENEMY_HP}
            _games[sid] = {
                "row_types": row_types,
                "counts": counts,
                "pirates": counts.get("EnemyPirate", 0) > 0,
                "reinforce": settings.get("reinforcementsEnabled", True),
                "interval": settings.get("reinforcementIntervalSeconds", 3.0),
            }
        return _games[sid]

    # ---- placing -------------------------------------------------------------------

    def free(self, x, y):
        return 0 <= x <= 10 and 0 <= y <= 5 and (x, y) not in self.taken

    def place(self, kind, x, y, urgent=False):
        limit = self.coins if urgent else self.coins - self.reserve
        if not self.free(x, y) or self.costs[kind] > limit:
            return False
        self.taken.add((x, y))
        self.coins -= self.costs[kind]
        self.cards.append({"defenceId": kind, "x": x, "y": y})
        self.defences.append({"type": _DEF_TYPE[kind], "health": _DEF_HP[kind], "position": {"x": x, "y": y}})
        return True

    def place_first(self, kind, xs, y, urgent=False):
        for x in xs:
            if self.place(kind, x, y, urgent):
                return x
        return None

    def soldier_slots(self, anchor):
        """Columns behind the blocker at `anchor`, nearest first, away from a pirate blast."""
        gap = 2 if self.game["pirates"] else 1
        return list(range(anchor - gap, -1, -1))

    # ---- the plan ------------------------------------------------------------------

    def plan(self):
        if self.state["phase"] == "start":
            self.opening()
        rows = self.threatened_rows()
        for y, _ in rows:
            self.emergency(y)
        for y, _ in rows:
            self.wizard(y)
        for y, _ in rows:
            self.firepower(y)
        for y, _ in rows:
            self.blocker(y)
        self.cover_empty_rows()
        self.archers(rows)
        return self.cards

    def opening(self):
        types = self.game["row_types"]
        rows = [y for y in range(6) if types[y]]
        first = sorted({_xy(e)[1] for e in self.enemies})
        rows = first + [y for y in rows if y not in first]
        heavy = not self.game["reinforce"] or self.game["interval"] > 5
        if heavy:  # no pauses to react in: build the whole defence now
            self.reserve = 0
        for y in rows:  # a blocker everywhere first; pirate rows can't be reacted to at all
            self.place(SHIELD, SHIELD_X, y, urgent=True)
        for y in rows:
            if y in first or heavy or self.coins - self.costs[SOLDIER] >= self.reserve + 300:
                self.place(SOLDIER, SHIELD_X - 2, y)
        if not heavy:
            return
        wizards = self.game["counts"].get("EnemyWizard", 0)
        for y in rows:
            if wizards > 0 and "EnemyWizard" in types[y]:
                if self.place(PENGUIN, SHIELD_X + 1, y):
                    wizards -= 1
        total = sum(self.game["counts"].values())
        for y in rows:
            if total / max(len(rows), 1) >= 2 and types[y] - {"EnemyPirate"}:
                self.place(SOLDIER, SHIELD_X - 3, y)
        archers = min(3, total // 6)
        for y in reversed(rows):
            if archers > 0 and self.coins - self.costs[ARCHER] >= 0.15 * self.state["budget"]["remaining"]:
                if self.place(ARCHER, 0, y):
                    archers -= 1

    def row_state(self, y):
        foes = sorted((e for e in self.enemies if _xy(e)[1] == y), key=lambda e: e["position"]["x"])
        if not foes:
            return None
        front = _xy(foes[0])[0]
        reach = front + (1 if foes[0].get("isAttacking") else 0)
        guards = [d for d in self.defences if d["health"] > 0 and _xy(d)[1] == y and _xy(d)[0] <= reach]
        return foes, front, guards

    def threatened_rows(self):
        rows = []
        for y in range(6):
            rs = self.row_state(y)
            if rs:
                rows.append((y, rs[1]))
        return sorted(rows, key=lambda r: r[1])  # nearest the base first

    def emergency(self, y):
        foes, front, guards = self.row_state(y)
        if guards:
            return
        lead = foes[0]["type"]
        behind = list(range(front - 1, -1, -1))
        if front <= 0:
            self.place(SOLDIER, 0, y, urgent=True)
        elif lead == "EnemyPirate":
            self.place_first(SHIELD, behind, y, urgent=True)
        elif front <= 3 or lead == "EnemyWizard":
            x = self.place_first(PENGUIN, behind, y, urgent=True)
            if x is not None:
                self.place_first(SOLDIER, range(x - 1, -1, -1), y, urgent=True)
        else:
            x = self.place_first(SHIELD, range(min(SHIELD_X, front - 1), -1, -1), y, urgent=True)
            if x is not None:
                self.place_first(SOLDIER, self.soldier_slots(x) or range(x - 1, -1, -1), y, urgent=True)

    def wizard(self, y):
        foes, front, guards = self.row_state(y)
        if not guards or not any(e["type"] == "EnemyWizard" for e in foes):
            return
        if any(d["type"] == "defencePenguin" for d in guards):
            return
        first = max(_xy(d)[0] for d in guards)
        self.place_first(PENGUIN, range(front - 1, first, -1), y, urgent=True)

    def firepower(self, y):
        foes, front, guards = self.row_state(y)
        if not guards:
            return
        shields = [d for d in guards if d["type"] == "defenceShield"]
        anchor = max(_xy(d)[0] for d in (shields or guards))
        for _ in range(3):
            if self.holds(foes, guards):
                return
            soldiers = sum(d["type"] == "defenceSolider" for d in guards)
            urgent = soldiers == 0 and any(e["type"] != "EnemyPirate" for e in foes)
            if soldiers < 3 and self.place_first(SOLDIER, self.soldier_slots(anchor), y, urgent) is not None:
                guards = self.row_state(y)[2]
                continue
            break
        if not self.holds(foes, guards) and not any(d["type"] == "defencePenguin" for d in guards):
            first = max(_xy(d)[0] for d in guards)
            self.place_first(PENGUIN, range(front - 1, first, -1), y)

    def holds(self, foes, guards):
        """Can the soldiers kill the row before the blockers in front of them fall?"""
        dps = SOLDIER_DPS * sum(d["type"] == "defenceSolider" for d in guards)
        if any(d["type"] == "defencePenguin" for d in guards):
            foes = [e for e in foes if e["health"] > 500]  # the slide will finish the rest
        if not foes:
            return True
        ttk, incoming, blasts = 0.0, 0.0, 0
        for e in foes:
            if e["type"] == "EnemyPirate":
                blasts += 1
                continue
            net = dps - REGEN.get(e["type"], 0)
            if net <= 0:
                return False
            ttk += e["health"] / net
            incoming += ENEMY_DPS.get(e["type"], 30)
        wall = sum(d["health"] for d in guards if d["type"] == "defenceShield") - PIRATE_BLAST * blasts
        if incoming == 0:  # pirates only: the shield takes the blasts, a soldier gets the kills
            return dps > 0 and wall > 0
        return ttk * 1.2 + 1 < max(wall, 0) / incoming

    def blocker(self, y):
        foes, front, guards = self.row_state(y)
        if not guards:
            return
        first = max(guards, key=lambda d: d["position"]["x"])
        incoming = sum(ENEMY_DPS.get(e["type"], 30) for e in foes)
        blasts = sum(e["type"] == "EnemyPirate" for e in foes)
        weak = first["type"] != "defenceShield" or first["health"] < incoming * 6 + PIRATE_BLAST * blasts
        if weak and first["type"] != "defencePenguin":
            fx = _xy(first)[0]
            self.place_first(SHIELD, range(min(front - 1, fx + 2), fx, -1), y)

    def cover_empty_rows(self):
        if self.state["counts"]["enemiesQueued"] <= 0:
            return
        for y in range(6):
            if not self.game["row_types"][y]:
                continue
            if any(_xy(d)[1] == y for d in self.defences if d["health"] > 0):
                continue
            self.place(SHIELD, SHIELD_X, y, urgent="EnemyPirate" in self.game["row_types"][y])

    def archers(self, rows):
        """Spare coins on a crowded board buy archers: they hit any row, so they help the weakest."""
        if len(self.enemies) < 4 or self.state["phase"] == "start":
            return
        have = sum(d["type"] == "defenceArcher" for d in self.defences if d["health"] > 0)
        busy = {y for y, _ in rows}
        quiet = [y for y in range(6) if y not in busy] + [y for y, _ in reversed(rows)]
        for y in quiet:
            if have >= 3 or self.coins - self.costs[ARCHER] < self.reserve + 1200:
                return
            if self.place(ARCHER, 0, y):
                have += 1


_DEF_TYPE = {PENGUIN: "defencePenguin", SOLDIER: "defenceSolider", ARCHER: "defenceArcher", SHIELD: "defenceShield"}
_DEF_HP = {PENGUIN: 200, SOLDIER: 150, ARCHER: 100, SHIELD: 700}


def check(raw, taken, coins, costs):
    """Keep the placements that are on the board, on a free cell and affordable."""
    chosen = []
    for p in raw[:24]:
        try:
            kind, x, y = int(p["defenceId"]), int(p["x"]), int(p["y"])
        except (KeyError, TypeError, ValueError):
            continue
        if kind in costs and 0 <= x <= 10 and 0 <= y <= 5 and (x, y) not in taken and costs[kind] <= coins:
            taken.add((x, y))
            coins -= costs[kind]
            chosen.append({"defenceId": kind, "defenceGridPosition": {"x": x, "y": y}})
    return chosen


def fallback(rows, taken, coins, costs):
    """A soldier at x=1 in each threatened row, as far as the coins go."""
    chosen = []
    for y in rows:
        if costs[1] <= coins and (1, y) not in taken:
            taken.add((1, y))
            chosen.append({"defenceId": 1, "defenceGridPosition": {"x": 1, "y": y}})
            coins -= costs[1]
    return chosen
