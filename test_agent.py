"""Offline tests for agent.py: random boards (fuzz) and hand-made tactical situations.

Run: .venv/bin/python -m unittest test_agent -v
"""
import io
import random
import time
import unittest
from contextlib import redirect_stderr

import agent

COSTS = {"0": 250, "1": 200, "2": 300, "3": 150}
ENEMY = {0: ("EnemClown", 200), 1: ("EnemyMonk", 280), 2: ("EnemyWizard", 500), 3: ("EnemyPirate", 30)}
DEFENCE = {0: ("defencePenguin", 200), 1: ("defenceSolider", 150), 2: ("defenceArcher", 100), 3: ("defenceShield", 700)}
_sid = [0]


def enemy(kind, x, y, health=None, attacking=False):
    name, hp = ENEMY[kind]
    return {"id": f"{name}_{x}{y}", "type": name, "health": hp if health is None else health, "maxHealth": hp,
            "position": {"x": x, "y": y}, "isAttacking": attacking, "isDead": False}


def defence(kind, x, y, health=None):
    name, hp = DEFENCE[kind]
    return {"id": f"{name}_{x}{y}", "type": name, "health": hp if health is None else health, "maxHealth": hp,
            "position": {"x": x, "y": y}}


def state(phase="pause", enemies=(), defences=(), coins=3000, queued=5, wave=None, reinforce=True,
          interval=3.0, sid=None):
    if sid is None:
        _sid[0] += 1
        sid = f"s{_sid[0]}"
    s = {
        "phase": phase, "timeBudgetSeconds": 15, "teamID": "0060", "sessionID": sid, "elapsedSeconds": 5.0,
        "counts": {"activeDefenceCount": len(defences), "activeEnemyCount": len(enemies),
                   "enemiesQueued": queued, "totalEnemiesInWave": queued + len(enemies)},
        "defences": list(defences), "enemies": list(enemies),
        "budget": {"remaining": coins, "spent": 0, "unlocked": coins, "total": coins + 1000},
        "unitCosts": dict(COSTS), "candidatePlacements": [],
    }
    if phase == "start":
        s["upcomingEnemies"] = wave if wave is not None else [{"enemyId": 0, "count": 10, "allowedRows": list(range(6))}]
        s["spawnSettings"] = {"interArrivalTime": 1.0, "timeDecreaseRate": 0.0, "minInterArrivalTime": 1.0}
        s["gameSettings"] = {"reinforcementsEnabled": reinforce, "reinforcementIntervalSeconds": interval,
                             "reinforcementHoldSeconds": 3.0, "sendScreenshotToo": False}
    return s


def run(s):
    """Call the agent; fail if the rules crashed into the starter's fallback."""
    err = io.StringIO()
    with redirect_stderr(err):
        cards = agent.get_reinforcements(s)
    assert "rules failed" not in err.getvalue(), err.getvalue()
    return [(c["defenceId"], c["defenceGridPosition"]["x"], c["defenceGridPosition"]["y"]) for c in cards]


def opening_then(pause, wave=None, coins=3000, reinforce=True):
    """Play the opening on an empty board so the agent learns the wave, then the given pause."""
    sid = f"g{random.random()}"
    run(state("start", enemies=[enemy(0, 10, 0)], coins=coins, wave=wave, reinforce=reinforce, sid=sid))
    pause["sessionID"] = sid
    return run(pause)


class Validity(unittest.TestCase):
    def assert_valid(self, s, cards):
        taken = {(round(d["position"]["x"]), round(d["position"]["y"])) for d in s["defences"] if d["health"] > 0}
        seen, cost = set(), 0
        for kind, x, y in cards:
            self.assertIn(kind, (0, 1, 2, 3))
            self.assertTrue(0 <= x <= 10 and 0 <= y <= 5, (x, y))
            self.assertNotIn((x, y), taken | seen, f"occupied tile {(x, y)}")
            seen.add((x, y))
            cost += COSTS[str(kind)]
        self.assertLessEqual(cost, s["budget"]["remaining"])

    def test_fuzz_random_boards(self):
        rng = random.Random(7)
        for i in range(3000):
            phase = "start" if i % 4 == 0 else "pause"
            tiles = rng.sample([(x, y) for x in range(11) for y in range(6)], rng.randint(0, 14))
            defences = [defence(rng.randint(0, 3), x, y, rng.choice([None, rng.uniform(-5, 700)])) for x, y in tiles]
            enemies = [enemy(rng.randint(0, 3), rng.randint(0, 10), rng.randint(0, 5),
                             rng.choice([None, rng.uniform(-5, 500)]), rng.random() < .4)
                       for _ in range(rng.randint(0 if phase == "pause" else 1, 12))]
            for e in enemies:
                e["isDead"] = rng.random() < .1
                e["position"]["x"] += rng.uniform(-.4, .4)  # positions are not always whole tiles
            wave = [{"enemyId": k, "count": rng.randint(1, 8),
                     "allowedRows": rng.choice([None, [], sorted(rng.sample(range(6), rng.randint(1, 6)))])}
                    for k in rng.sample(range(4), rng.randint(1, 4))]
            s = state(phase, enemies, defences, coins=rng.choice([0, 100, 149, 1300, 2600, 3900, 5000]),
                      queued=rng.randint(0, 15), wave=wave, reinforce=rng.random() < .8,
                      interval=rng.choice([3.0, 5.0, 8.0]), sid=rng.choice(["fixed", f"r{i}", None]))
            t = time.perf_counter()
            cards = run(s)
            self.assertLess(time.perf_counter() - t, 1.0)
            self.assert_valid(s, cards)

    def test_missing_optional_keys(self):
        s = state("start", enemies=[enemy(1, 10, 2)])
        del s["gameSettings"], s["spawnSettings"]
        s["upcomingEnemies"] = [{"enemyId": 1, "count": 3}]
        self.assert_valid(s, run(s))

    def test_reloaded_mid_game(self):
        """A pause with no opening seen (agent.py edited mid-game) still defends."""
        s = state(enemies=[enemy(1, 6, 3)], sid="never-opened")
        cards = run(s)
        self.assertTrue(any(y == 3 for _, _, y in cards))


class Opening(unittest.TestCase):
    def test_every_allowed_row_gets_a_shield(self):
        cards = run(state("start", enemies=[enemy(0, 10, 2)], coins=5000))
        self.assertEqual({y for k, x, y in cards if k == 3}, set(range(6)))

    def test_only_allowed_rows(self):
        wave = [{"enemyId": 0, "count": 6, "allowedRows": [1, 4]}]
        cards = run(state("start", enemies=[enemy(0, 10, 1)], wave=wave))
        self.assertEqual({y for _, _, y in cards}, {1, 4})

    def test_poor_budget_still_blocks_every_row(self):
        cards = run(state("start", enemies=[enemy(1, 10, 0)], coins=1300))
        self.assertEqual({y for k, x, y in cards if k == 3}, set(range(6)))
        self.assertTrue(any(k == 1 and y == 0 and x < 4 for k, x, y in cards), "soldier behind the first shield")

    def test_wizard_opening_gets_penguin(self):
        wave = [{"enemyId": 2, "count": 3, "allowedRows": [2]}]
        cards = run(state("start", enemies=[enemy(2, 10, 2)], wave=wave))
        self.assertTrue(any(k == 0 and y == 2 for k, x, y in cards), cards)

    def test_no_reinforcements_builds_more(self):
        wave = [{"enemyId": 0, "count": 12, "allowedRows": list(range(6))},
                {"enemyId": 2, "count": 2, "allowedRows": list(range(6))}]
        light = run(state("start", enemies=[enemy(0, 10, 0)], coins=3900, wave=wave))
        heavy = run(state("start", enemies=[enemy(0, 10, 0)], coins=3900, wave=wave, reinforce=False))
        cost = lambda cards: sum(COSTS[str(k)] for k, _, _ in cards)
        self.assertGreater(cost(heavy), cost(light))
        self.assertTrue(any(k == 0 for k, _, _ in heavy), "penguins for the wizards")


class Pirates(unittest.TestCase):
    WAVE = [{"enemyId": 0, "count": 4, "allowedRows": [1, 2, 3, 4]}, {"enemyId": 1, "count": 3, "allowedRows": [0, 1, 2]},
            {"enemyId": 2, "count": 2, "allowedRows": [3, 4, 5]}, {"enemyId": 3, "count": 2, "allowedRows": [0, 1, 5]}]

    def test_tc02_opening(self):
        """TC02 (lost once): 1700 coins, pauses 10 s apart, pirates in rows 0, 1, 5."""
        cards = run(state("start", enemies=[enemy(0, 10, 2)], coins=1700, wave=self.WAVE, interval=10.0))
        shields = {y: x for k, x, y in cards if k == 3}
        self.assertEqual(set(shields), set(range(6)))
        for a, b in [(0, 1), (4, 5)]:  # neighbouring pirate rows: one blast must not reach both shields
            self.assertGreaterEqual(abs(shields[a] - shields[b]), 2, shields)
        self.assertGreaterEqual(1700 - sum(COSTS[str(k)] for k, _, _ in cards), 250, "penguin money kept")
        for k, x, y in cards:
            if k == 1:
                self.assertLess(x, shields[y], (x, y))
        self.assertTrue(any(k == 1 and y in (0, 1, 2) for k, _, y in cards), "a soldier for the monks")


class TC09(unittest.TestCase):
    WAVE = [{"enemyId": 0, "count": 2, "allowedRows": [0, 2, 4]}, {"enemyId": 1, "count": 7, "allowedRows": [0, 2, 4]},
            {"enemyId": 2, "count": 4, "allowedRows": [1, 3, 5]}, {"enemyId": 3, "count": 2, "allowedRows": [1, 3, 5]}]

    def test_opening_skips_pirate_soldiers(self):
        cards = run(state("start", enemies=[enemy(1, 10, 4)], coins=2100, wave=self.WAVE, interval=10.0))
        self.assertEqual({y for k, _, y in cards if k == 1}, {4}, "soldier only where the first monk is")
        self.assertGreaterEqual(2100 - sum(COSTS[str(k)] for k, _, _ in cards), 500)

    def test_breaking_row_spends_reserve_on_backstop(self):
        """The real pause that lost TC09: row 4 shield at 180, monk on it, 300 coins."""
        defs = [defence(1, 2, 0), defence(3, 3, 0), defence(1, 3, 1), defence(3, 5, 1), defence(1, 2, 2),
                defence(3, 3, 2), defence(3, 5, 3, 500), defence(1, 2, 4), defence(3, 3, 4, 180), defence(3, 5, 5)]
        foes = [enemy(1, 4, 4, 10, True), enemy(0, 7, 4, 119, True), enemy(1, 10, 4)]
        cards = opening_then(state(defences=defs, enemies=foes, coins=300, queued=9), wave=self.WAVE, coins=2100)
        self.assertTrue(any(k == 0 and y == 4 for k, _, y in cards), cards)


class TC01Drag(unittest.TestCase):
    def test_pile_behind_backstop_gets_front_penguin_and_no_forward_shields(self):
        """Real TC01 at 25.6 s: 4 clowns parked on row 4, backstop penguin behind; game dragged 120 s."""
        defs = [defence(3, 4, y) for y in range(6) if y != 4] + [defence(3, 4, 4, 210), defence(1, 3, 4),
                                                                  defence(0, 1, 4), defence(2, 0, 1), defence(2, 0, 2)]
        foes = [enemy(0, 8, 4, hp, True) for hp in (165, 189, 192, 200)] + [enemy(0, 8, 5, 115, True)]
        cards = opening_then(state(defences=defs, enemies=foes, coins=1250, queued=0), coins=5000)
        self.assertTrue(any(k == 0 and y == 4 and 4 < x < 8 for k, x, y in cards), cards)
        self.assertFalse(any(k == 3 and x > 5 for k, x, y in cards), cards)


class Tactics(unittest.TestCase):
    def test_quiet_board_keeps_coins(self):
        defs = [defence(3, 4, y) for y in range(6)] + [defence(1, 2, y) for y in range(6)]
        self.assertEqual(opening_then(state(defences=defs, enemies=[enemy(0, 9, 3)])), [])

    def test_leaked_monk_gets_penguin_in_front(self):
        cards = opening_then(state(enemies=[enemy(1, 2, 3)], defences=[defence(3, 4, 0)]))
        self.assertIn((0, 1, 3), cards)

    def test_wizard_in_row_gets_penguin_between(self):
        defs = [defence(3, 4, 1), defence(1, 2, 1)]
        cards = opening_then(state(enemies=[enemy(2, 9, 1, attacking=True)], defences=defs))
        self.assertTrue(any(k == 0 and y == 1 for k, x, y in cards), cards)

    def test_clown_pile_gets_penguin(self):
        defs = [defence(3, 4, 3, 300), defence(1, 2, 3)]
        foes = [enemy(0, 7, 3, attacking=True) for _ in range(4)]
        cards = opening_then(state(enemies=foes, defences=defs))
        self.assertTrue(any(k == 0 and y == 3 for k, x, y in cards), cards)

    def test_monk_row_without_soldier_gets_one(self):
        cards = opening_then(state(enemies=[enemy(1, 6, 5)], defences=[defence(3, 4, 5)]))
        self.assertTrue(any(k == 1 and y == 5 and x < 4 for k, x, y in cards), cards)

    def test_pirate_wave_keeps_soldiers_out_of_blast(self):
        wave = [{"enemyId": 3, "count": 6, "allowedRows": [2]}, {"enemyId": 1, "count": 4, "allowedRows": [2]}]
        foes = [enemy(1, 7, 2), enemy(1, 8, 2), enemy(3, 9, 2)]
        cards = opening_then(state(enemies=foes, defences=[defence(3, 4, 2)]), wave=wave)
        for k, x, y in cards:
            if k == 1 and y == 2:
                self.assertLessEqual(x, 2, "soldier within a pirate blast of the shield")

    def test_dying_shield_gets_help_in_front(self):
        defs = [defence(3, 4, 0, 80), defence(1, 2, 0), defence(1, 1, 0)]
        cards = opening_then(state(enemies=[enemy(0, 8, 0, attacking=True)], defences=defs))
        self.assertTrue(any(k in (0, 3) and y == 0 and x > 4 for k, x, y in cards), cards)

    def test_walking_clown_waits_settled_clown_gets_penguin(self):
        defs = [defence(3, 4, y) for y in range(6)] + [defence(1, 3, 2)]
        self.assertEqual(opening_then(state(defences=defs, enemies=[enemy(0, 9, 2)])), [])
        cards = opening_then(state(defences=defs, enemies=[enemy(0, 8, 2, attacking=True)]))
        self.assertEqual([c for c in cards if c[0] != 3], [(0, 7, 2)])

    def test_no_soldiers_wasted_on_far_clowns(self):
        defs = [defence(3, 4, y) for y in range(6)]
        cards = opening_then(state(defences=defs, enemies=[enemy(0, 8, 1, attacking=True)], coins=5000))
        self.assertFalse(any(k == 1 for k, _, _ in cards), cards)

    def test_wizard_from_spawn_gets_penguin(self):
        defs = [defence(3, 4, y) for y in range(6)]
        quick = opening_then(state(defences=defs, enemies=[enemy(2, 10, 4, attacking=True)]))
        self.assertIn((0, 3, 4), quick, "pauses every 3 s: backstop right behind the shield")
        wave = [{"enemyId": 2, "count": 4, "allowedRows": [4]}]
        run(state("start", enemies=[enemy(2, 10, 4)], wave=wave, interval=10.0, sid="slow-pauses"))
        slow = run(state(enemies=[enemy(2, 10, 4, attacking=True)], defences=defs, sid="slow-pauses"))
        self.assertIn((0, 9, 4), slow, "pauses every 10 s: in front, so the shield survives")

    def test_emptied_row_gets_shield_while_enemies_queued(self):
        defs = [defence(3, 4, y) for y in range(5)]
        cards = opening_then(state(defences=defs, queued=4))
        self.assertIn((3, 4, 5), cards)

    def test_reserve_kept_for_emergencies(self):
        """With little money, non-urgent buys must leave a penguin's worth."""
        defs = [defence(3, 4, y) for y in range(6)] + [defence(1, 2, 0)]
        cards = opening_then(state(defences=defs, enemies=[enemy(0, 8, 0), enemy(0, 9, 0)], coins=400))
        self.assertLessEqual(sum(COSTS[str(k)] for k, _, _ in cards), 150)

    def test_broke_returns_nothing(self):
        self.assertEqual(opening_then(state(enemies=[enemy(1, 3, 0)], coins=100)), [])


if __name__ == "__main__":
    unittest.main()
