"""Rule-based agent: no LLM, every decision comes from the rules below.

Each row enemies may use gets a shield as the blocker; rows monks may use also get a
soldier behind it. At every pause each row with enemies is checked, most urgent first:
  1. nothing in front of the leading enemy: emergency blocker (penguin when it is close)
  2. a wizard in the row: a penguin in front of the blocker (500 kills a full wizard)
  3. not enough damage to kill the row before the blocker falls: a penguin for clowns and
     wizards (soldiers barely hurt them where they stand), soldiers for the rest
  4. blocker getting weak: a fresh shield in front of it
Rows that may still get enemies keep at least a shield, and a penguin's worth of coins is
kept back while enemies are still to come. Spending costs little score; losing costs most.
If anything here crashes, the starter's fallback() still places soldiers.

Seen in play: clowns stand ~4 tiles in front of the blocker and wizards ~6 (snapshot
positions), monks right at it. A soldier more than ~4 tiles from its target does little,
so a soldier 2 behind the shield can't beat a clown's regen. A pirate's blast also hits the
shields in the rows above and below, so near pirate rows the shields are staggered (3 / 5).
"""
import sys

PENGUIN, SOLDIER, ARCHER, SHIELD = 0, 1, 2, 3
SHIELD_X = 4          # blocker column away from pirates
ENEMY_TYPES = {0: "EnemClown", 1: "EnemyMonk", 2: "EnemyWizard", 3: "EnemyPirate"}
ENEMY_HP = {"EnemClown": 200, "EnemyMonk": 280, "EnemyWizard": 500, "EnemyPirate": 30}
ENEMY_DPS = {"EnemClown": 17, "EnemyMonk": 36, "EnemyWizard": 50, "EnemyPirate": 0}
STANDOFF = {"EnemClown": 4, "EnemyMonk": 1, "EnemyWizard": 6, "EnemyPirate": 0}  # tiles from the blocker
REGEN = {"EnemClown": 15, "EnemyWizard": 15}
SOLDIER_DPS, SOLDIER_FAR_DPS, SOLDIER_REACH = 40, 10, 4
PIRATE_BLAST = 200
# strategy knobs (sim.py compares settings of these)
OPEN_SOLDIERS = "first"  # "all" monk rows at the opening, or only the "first" enemy's row
OPEN_ARCHERS = 0        # archers bought at the opening when the wave has clowns or wizards
RESERVE = 250           # coins kept back while enemies are still to come
RICH_MARGIN = 150       # spare coins (above the reserve) before acting early on parked clowns / monk rows
MAX_SOLDIERS = 1        # soldiers per row unless its blocker is about to fall (then up to 3)
BACKSTOP_WIZARD = True   # wizard in a row: penguin behind the shield at once, instead of in front of it
BACKSTOP_SPARE = False   # a spare shield behind a backstop penguin, so the row stays blocked after it slides
BACKSTOP_MAX_INTERVAL = 6  # ...only when pauses come often: after the slide the row is empty until the next one
JIT_SHIELDS = False      # shield a row only once an enemy that may use it spawns before the next pause
BACKSTOP_CLOWN = False   # clowns in a row: penguin behind the shield at once, instead of in front later
CLOWN_ARCHERS = 0       # archers on the board before parked clowns are left to them instead of penguins

_games = {}  # sessionID -> what the opening told us about the wave (lost between real calls)
REMEMBER = False  # True only in tests that want the opening's wave kept for later pauses
PAUSE_DRIFT = 1.2  # real pauses fall at 0.3 + k * (interval + PAUSE_DRIFT) s of game time


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
        self.queued = state["counts"]["enemiesQueued"]
        # keep enough for one emergency penguin while enemies are still to come
        self.reserve = RESERVE if self.queued > 0 and self.game["reinforce"] else 0
        if self.reserve and self.upcoming().count("EnemyWizard") >= 2:
            self.reserve += costs[PENGUIN]  # two wizards may need a penguin on the same pause

    def _spawn_times(self):
        """Game time of each spawn, from the opening's spawn settings (the first is now)."""
        spawn = self.state.get("spawnSettings") or {}
        total = self.state["counts"].get("totalEnemiesInWave") or 0
        try:
            gap, rate, low = (float(spawn[k]) for k in ("interArrivalTime", "timeDecreaseRate", "minInterArrivalTime"))
        except (KeyError, TypeError, ValueError):
            return []
        t, times = float(self.state.get("elapsedSeconds") or 0), []
        for _ in range(total):
            times.append(t)
            t += gap
            gap = max(low, gap - rate)
        return times

    def soon(self):
        """Rows an enemy may enter before the pause after next is answered; None if unknown."""
        seq, times = self.game["sequence"], self.game["spawn_times"]
        if not JIT_SHIELDS or not seq or len(times) != len(seq) or not self.game["reinforce"]:
            return None
        now = float(self.state.get("elapsedSeconds") or 0)
        horizon = now + self.game["interval"] + 2
        rows = set()
        for kind, t in zip(seq[len(seq) - self.queued:], times[len(seq) - self.queued:]):
            if t <= horizon:
                rows |= {y for y in range(6) if kind in self.game["row_types"][y]}
        return rows

    def upcoming(self):
        """Enemy types still to spawn, when the level fixes the order."""
        seq = self.game["sequence"]
        return seq[len(seq) - self.queued:] if seq and self.queued else []

    def rich(self, extra=0):
        return self.coins - extra >= self.reserve + RICH_MARGIN

    def _game_info(self):
        sid = self.state.get("sessionID")
        wave = self.state.get("upcomingEnemies") or []
        if not wave:
            # The SDK runs every call in a fresh process and only the opening gets the wave, so a
            # pause must read what it needs from the board (memory below only helps tests and sim.py).
            if REMEMBER and sid in _games:
                return _games[sid]
            return self._game_from_board()
        settings = self.state.get("gameSettings") or {}
        row_types = {y: set() for y in range(6)}
        counts = {}
        for group in wave:
            kind = ENEMY_TYPES.get(group.get("enemyId"), "EnemClown")
            counts[kind] = counts.get(kind, 0) + group.get("count", 0)
            for y in group.get("allowedRows") or range(6):
                row_types[y].add(kind)
        game = _games[sid] = {
            "row_types": row_types,
            "counts": counts,
            "pirate_rows": {y for y in range(6) if "EnemyPirate" in row_types[y]},
            "sequence": [ENEMY_TYPES.get(k, "EnemClown") for k in
                         (self.state.get("spawnSettings") or {}).get("spawnSequence") or []],
            "spawn_times": self._spawn_times(),
            "reinforce": settings.get("reinforcementsEnabled", True),
            "interval": settings.get("reinforcementIntervalSeconds", 3.0),
            "known": True,
        }
        return game

    def _game_from_board(self):
        """What a pause can tell about the level without the opening's wave.
        Pirate rows: the opening staggers shields near pirate rows (3 on even rows, 5 on odd)."""
        staggered = {_xy(d)[1] for d in self.defences
                     if d["type"] == "defenceShield" and _xy(d)[0] == (3 if _xy(d)[1] % 2 == 0 else 5)}
        return {
            "row_types": {y: set(ENEMY_HP) for y in range(6)},
            "counts": {k: 1 for k in ENEMY_HP},
            "known": False,
            "pirate_rows": staggered,
            "sequence": [],
            "spawn_times": [],
            "reinforce": True,
            "interval": guess_interval(float(self.state.get("elapsedSeconds") or 0)),
        }

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

    def shield_col(self, y):
        """Blocker column. Near pirate rows, neighbours alternate 3 / 5 so one blast hits one shield."""
        pirates = self.game["pirate_rows"]
        if pirates & {y - 1, y, y + 1}:
            return 3 if y % 2 == 0 else 5
        return SHIELD_X

    def blast_safe(self, x, y):
        """Is (x, y) out of reach of a pirate blowing up at a blocker (its own row or a neighbour's)?"""
        pirates = self.game["pirate_rows"]
        if self.queued == 0 and not any(e["type"] == "EnemyPirate" for e in self.enemies):
            pirates = set()  # none left to blow up
        for p in (y - 1, y, y + 1):
            if p not in pirates:
                continue
            bx = self.shield_col(p) + 0.4  # the pirate stops just in front of the blocker
            dy = 1.5 * abs(p - y)
            if (1.5 * (x - bx)) ** 2 + dy ** 2 <= 2.2 ** 2:
                return False
        return True

    def soldier_slots(self, anchor, y):
        """Columns behind the blocker at `anchor`, nearest first, out of pirate blasts if possible."""
        behind = list(range(anchor - 1, -1, -1))
        safe = [x for x in behind if self.blast_safe(x, y)]
        return safe or behind

    # ---- the plan ------------------------------------------------------------------

    def plan(self):
        if self.state["phase"] == "start":
            self.opening()
        rows = self.threatened_rows()
        # rows in order of how soon they leak, each one finished before the next gets any coins
        # (a wizard still at x=10 must not take the money a row about to break needs)
        for y, _ in rows:
            self.emergency(y)
            self.wizard(y)
            self.firepower(y)
            self.blocker(y)
        self.cover_empty_rows()
        self.prepare()
        self.archers(rows)
        return self.cards

    def opening(self):
        types = self.game["row_types"]
        rows = [y for y in range(6) if types[y]]
        first = sorted({_xy(e)[1] for e in self.enemies})
        rows = first + [y for y in rows if y not in first]
        heavy = not self.game["reinforce"]
        if heavy:  # no pauses to react in: build the whole defence now
            self.reserve = 0
        soon = None if heavy else self.soon()
        for y in rows:  # a blocker in every row an enemy may reach before we are asked again
            if soon is None or y in soon or y in first:
                self.place(SHIELD, self.shield_col(y), y, urgent=True)
        # soldiers where they work: monks walk up to them (pirates blowing up on a shield count as kills)
        melee = [y for y in rows if "EnemyMonk" in types[y]]
        melee.sort(key=lambda y: y not in first)
        if heavy:
            self.heavy_opening(rows, melee)
            return
        if OPEN_SOLDIERS == "first":
            melee = [y for y in melee if y in first]
        for y in melee:
            self.place_first(SOLDIER, self.soldier_slots(self.shield_col(y), y), y)
        if self.game["counts"].get("EnemClown", 0) + self.game["counts"].get("EnemyWizard", 0):
            for y in self.archer_rows()[:OPEN_ARCHERS]:
                self.place(ARCHER, 0, y)

    def heavy_opening(self, rows, melee):
        """No pauses to react in. Behind each shield a penguin waits: when the pile in front breaks
        the shield, the penguin is hit and slides through all of it (wizards too). Soldiers behind
        that for monks, then archers, which reach every row, with what is left."""
        types = self.game["row_types"]
        counts = self.game["counts"]

        def backstop(y):
            s = self.shield_col(y)
            self.place_first(PENGUIN, [x for x in range(s - 1, -1, -1) if self.blast_safe(x, y)], y)

        for y in rows:
            if "EnemyWizard" in types[y]:
                backstop(y)
        for y in melee:
            self.place_first(SOLDIER, self.soldier_slots(self.shield_col(y), y), y)
        for y in rows:
            if "EnemClown" in types[y] and "EnemyWizard" not in types[y]:
                backstop(y)
        total = sum(counts.values())
        for y in melee:  # a second soldier where monks will come often
            if counts.get("EnemyMonk", 0) / max(len(melee), 1) >= 2:
                self.place_first(SOLDIER, self.soldier_slots(self.shield_col(y), y), y)
        archers = min(4, max(1, total // 4))
        for y in self.archer_rows():
            if archers > 0 and self.place(ARCHER, 0, y):
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
        return sorted(rows, key=lambda r: self.leak_time(r[0]))  # soonest to leak first

    def leak_time(self, y):
        """Rough seconds until row y's enemies reach the base: the shields in the way, then the walk."""
        foes, front, guards = self.row_state(y)
        wall = self.wall_time(foes, guards) if guards else 0
        return wall + front / 1.5 * 0.7 + (0 if guards else -5)

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
            x = self.place_first(SHIELD, range(min(self.shield_col(y), front - 1), -1, -1), y, urgent=True)
            if x is not None and lead == "EnemyMonk":
                self.place_first(SOLDIER, self.soldier_slots(x, y), y, urgent=True)

    def wizard(self, y):
        foes, front, guards = self.row_state(y)
        if not guards or not any(e["type"] == "EnemyWizard" for e in foes):
            return
        if any(d["type"] == "defencePenguin" for d in guards):
            return
        first = max(_xy(d)[0] for d in guards)
        if BACKSTOP_WIZARD and self.game["interval"] <= BACKSTOP_MAX_INTERVAL and self.wall_time(foes, guards) > self.game["interval"] + 3 and self.backstop(y, first, True):
            return
        self.place_first(PENGUIN, range(front - 1, first, -1), y, urgent=True)

    def firepower(self, y):
        foes, front, guards = self.row_state(y)
        if not guards:
            return
        if self.holds(foes, guards):
            return
        shields = [d for d in guards if d["type"] == "defenceShield"]
        anchor = max(_xy(d)[0] for d in (shields or guards))
        first = max(_xy(d)[0] for d in guards)
        has_penguin = bool(working_penguins(foes, guards))
        regen = [e for e in foes if e["type"] in REGEN]
        piled = sum(e.get("isAttacking", False) for e in regen)
        # the blocker falls before the next pause: this row may spend the emergency reserve
        urgent = self.wall_time(foes, guards) < self.game["interval"] + 3
        # clowns and wizards: one penguin clears the whole pile, so let a pile build up on the
        # shield (a clown needs ~40 s to break one) unless it is in danger or the wave is over
        if BACKSTOP_CLOWN and regen and not has_penguin and self.backstop(y, first, urgent):
            guards = self.row_state(y)[2]
            has_penguin = True
        archers = sum(d["type"] == "defenceArcher" for d in self.defences if d["health"] > 0)
        if piled and not urgent and not has_penguin and archers < CLOWN_ARCHERS and \
                not any(e["type"] == "EnemyWizard" for e in regen):
            for x, ay in [(0, r) for r in self.archer_rows()]:
                if self.place(ARCHER, x, ay):
                    return  # the archers will grind the parked clowns down
        if archers >= CLOWN_ARCHERS > 0 and not urgent and not any(e["type"] == "EnemyWizard" for e in regen):
            piled = 0
        if regen and not has_penguin and (urgent or piled >= 2 or (piled and (self.queued == 0 or self.rich(250)))):
            if self.penguin(y, front, first, urgent):
                guards = self.row_state(y)[2]
                has_penguin = True
        if regen and not any(e["type"] == "EnemyMonk" for e in foes) and (has_penguin or not urgent):
            return  # far soldiers can't out-damage regen: leave it to the penguin
        monk = any(e["type"] == "EnemyMonk" and e["health"] > 150 for e in foes)
        if urgent and monk and not has_penguin and not self.holds(foes, guards):
            # a lone soldier loses to a monk: a penguin kills it outright
            if self.penguin(y, front, first, urgent=True):
                guards = self.row_state(y)[2]
                has_penguin = True
        for _ in range(3):
            if self.holds(foes, guards):
                return
            soldiers = sum(d["type"] == "defenceSolider" for d in guards)
            need = urgent or (soldiers == 0 and any(e["type"] == "EnemyMonk" for e in foes))
            cap = 3 if urgent else MAX_SOLDIERS
            if soldiers < cap and self.place_first(SOLDIER, self.soldier_slots(anchor, y), y, need) is not None:
                guards = self.row_state(y)[2]
                continue
            break
        if urgent and not has_penguin and not self.holds(foes, guards):
            self.penguin(y, front, first, urgent=True)

    def backstop(self, y, first, urgent=False):
        """A penguin right behind the blocker: when the pile breaks it, the penguin is hit next."""
        behind = [x for x in range(first - 1, -1, -1) if self.blast_safe(x, y)]
        x = self.place_first(PENGUIN, behind, y, urgent)
        if x is None:
            return False
        if BACKSTOP_SPARE and not any(d["type"] == "defenceShield" and _xy(d)[1] == y and _xy(d)[0] < x
                                      for d in self.defences if d["health"] > 0):
            self.place_first(SHIELD, range(x - 1, -1, -1), y)  # keeps the row blocked after the slide
        return True

    def penguin(self, y, front, first, urgent):
        """In front of the blocker if there's room, else behind it as a backstop: when the line
        breaks the enemies hit it and it slides through the whole row."""
        if self.place_first(PENGUIN, range(front - 1, first, -1), y, urgent) is not None:
            return True
        behind = [x for x in range(first - 1, -1, -1) if self.blast_safe(x, y)]  # a blast would set it off
        return urgent and self.place_first(PENGUIN, behind, y, urgent) is not None

    def wall_time(self, foes, guards):
        """Seconds until the shields in front of the soldiers are broken."""
        incoming = sum(ENEMY_DPS.get(e["type"], 30) for e in foes)
        blasts = sum(e["type"] == "EnemyPirate" for e in foes)
        wall = sum(d["health"] for d in guards if d["type"] == "defenceShield") - PIRATE_BLAST * blasts
        return max(wall, 0) / incoming if incoming else (99 if wall > 0 else 0)

    def holds(self, foes, guards):
        """Can the soldiers kill the row before the blockers in front of them fall?"""
        if working_penguins(foes, guards):
            foes = [e for e in foes if e["health"] > 500]  # the slide will finish the rest
        if not foes:
            return True
        soldiers = [_xy(d)[0] for d in guards if d["type"] == "defenceSolider"]
        front_guard = max(_xy(d)[0] for d in guards)
        ttk, pirates_only = 0.0, True
        for e in foes:
            if e["type"] == "EnemyPirate":
                continue
            pirates_only = False
            stand = min(_xy(e)[0], front_guard + STANDOFF.get(e["type"], 1))  # where it will stop
            dps = sum(SOLDIER_DPS if stand - s <= SOLDIER_REACH else SOLDIER_FAR_DPS for s in soldiers)
            net = dps - REGEN.get(e["type"], 0)
            if net <= 0:
                return False
            ttk += e["health"] / net
        if pirates_only:  # they blow up on the shield, which counts as a kill
            return self.wall_time(foes, guards) > 0
        return ttk * 1.2 + 1 < self.wall_time(foes, guards)

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
            # never further forward than one past the usual column: a blocker far forward keeps
            # the enemies out of the soldiers' reach and the game drags on
            self.place_first(SHIELD, range(min(front - 1, fx + 2, self.shield_col(y)), fx, -1), y)

    def prepare(self):
        """With coins to spare, give rows monks may use a soldier before a monk arrives."""
        if self.state["phase"] == "start" or self.queued == 0 or not self.game["known"]:
            return  # without the wave we can't tell which rows monks use
        upcoming = self.upcoming()
        if upcoming and "EnemyMonk" not in upcoming:
            return
        for y in range(6):
            if "EnemyMonk" not in self.game["row_types"][y] or not self.rich(self.costs[SOLDIER]):
                continue
            row = [d for d in self.defences if d["health"] > 0 and _xy(d)[1] == y]
            shields = [_xy(d)[0] for d in row if d["type"] == "defenceShield"]
            if shields and not any(d["type"] == "defenceSolider" for d in row):
                self.place_first(SOLDIER, self.soldier_slots(max(shields), y), y)

    def cover_empty_rows(self):
        if self.state["counts"]["enemiesQueued"] <= 0:
            return
        soon = self.soon()
        for y in range(6):
            if not self.game["row_types"][y] or (soon is not None and y not in soon):
                continue
            if any(_xy(d)[1] == y for d in self.defences if d["health"] > 0):
                continue
            self.place(SHIELD, self.shield_col(y), y, urgent="EnemyPirate" in self.game["row_types"][y])

    def archer_rows(self):
        """Archers sit at x=0, best in rows enemies can't use, else behind the most defences."""
        types = self.game["row_types"]
        return sorted(range(6), key=lambda y: (bool(types[y]), "EnemyPirate" in types[y],
                                               -sum(_xy(d)[1] == y for d in self.defences)))

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


def guess_interval(elapsed):
    """The pause interval, from when this pause falls (seen in TC01, TC02, TC05: 4.5 / 8.7 / 13.0,
    11.5 / 22.8 / 34.0, 13.5 / 26.8 / 40.0). On a tie the longer interval, the cautious guess."""
    best, err = 10.0, 0.08
    for interval in (15, 12, 10, 8, 6, 5, 4, 3, 2):
        k = (elapsed - 0.3) / (interval + PAUSE_DRIFT)
        if k >= 0.5 and abs(k - round(k)) < err - 1e-9:
            best, err = float(interval), abs(k - round(k))
    return best


def working_penguins(foes, guards):
    """Penguins that will slide soon: in front of every other defence, or a backstop behind a shield
    a wizard is beaming (~14 s). Clowns take ~40 s per shield, so behind one a backstop just waits."""
    penguins = [d for d in guards if d["type"] == "defencePenguin"]
    if any(e["type"] == "EnemyWizard" for e in foes):
        return penguins
    others = [_xy(d)[0] for d in guards if d["type"] != "defencePenguin"]
    return [d for d in penguins if _xy(d)[0] > max(others, default=-1)]


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
