"""Rough offline simulator of the AutoPilot game, to test agent.py without the browser.

Built from the player guide plus what the run logs showed (clowns stop ~4 tiles in front of
the first defence, wizards ~6, a soldier far from its target does little). It is NOT the real
game: use it to find losing patterns, then confirm with PLAY.

Run: .venv/bin/python sim.py [games per case] [agent file]
"""
import importlib.util
import io
import random
import sys
from contextlib import redirect_stderr

DT = 0.05
COSTS = {0: 250, 1: 200, 2: 300, 3: 150}
DEF = {0: ("defencePenguin", 200), 1: ("defenceSolider", 150), 2: ("defenceArcher", 100), 3: ("defenceShield", 700)}
# type, hp, speed (tiles/s), stop distance in front of the defence (tiles), dps, regen
ENEMY = {
    0: ("EnemClown", 200, 2 / 1.5, 3.6, 17, 15),
    1: ("EnemyMonk", 280, 3.5 / 1.5, 0.5, 36, 0),
    2: ("EnemyWizard", 500, 3 / 1.5, 5.6, 50, 15),
    3: ("EnemyPirate", 30, 8 / 1.5, 0.3, 0, 0),
}

CASES = {  # the 4 public cases as played, plus made-up ones to avoid tuning to them only
    "TC01": dict(budget=5000, interval=3.0, gap=(1.0, 0.0, 1.0), seq=[0, 0, 1, 0, 0, 1, 0, 0, 0, 1, 0, 0, 1, 0, 0, 0, 0, 0],
                 rows={0: range(6), 1: range(6)}),
    "TC02": dict(budget=1700, interval=10.0, gap=(5.0, 0.3, 0.8), seq=[0, 1, 0, 3, 1, 2, 0, 3, 1, 2, 0],
                 rows={0: [1, 2, 3, 4], 1: [0, 1, 2], 2: [3, 4, 5], 3: [0, 1, 5]}),
    "TC09": dict(budget=2100, interval=10.0, gap=(4.5, 0.25, 0.6), seq=[1, 0, 2, 1, 3, 1, 2, 1, 0, 1, 2, 3, 1, 2, 1],
                 rows={0: [0, 2, 4], 1: [0, 2, 4], 2: [1, 3, 5], 3: [1, 3, 5]}),
    "TC05": dict(budget=2000, interval=12.0, gap=(5.5, 0.25, 1.0), seq=[0, 2, 1, 3, 2, 3, 0, 2, 1, 3, 2, 3],
                 rows={0: [2, 3], 1: [0, 1], 2: [0, 5], 3: [4, 5]}),
    "wizard-rush": dict(budget=2600, interval=5.0, gap=(2.0, 0.1, 0.8), seq=[2] * 6 + [0] * 4 + [1] * 4,
                        rows={0: range(6), 1: range(6), 2: range(6)}),
    "pirate-swarm": dict(budget=1300, interval=3.0, gap=(1.5, 0.1, 0.5), seq=[3, 1] * 6,
                         rows={1: range(6), 3: range(6)}),
    "no-pauses": dict(budget=3900, interval=0, gap=(2.0, 0.1, 0.8), seq=[0, 1, 2, 0, 1, 3, 0, 1, 2, 0, 1, 0],
                      rows={k: range(6) for k in range(4)}),
}


class Game:
    def __init__(self, case, agent, rng):
        self.c, self.agent, self.rng = case, agent, rng
        self.t, self.coins, self.spent = 0.0, case["budget"], 0
        self.defs, self.foes, self.kills, self.lost = [], [], 0, 0
        self.queue = list(case["seq"])
        self.next_spawn, self.gap = 0.0, case["gap"][0]
        self.sid = f"sim{rng.random()}"
        self.n = 0

    # ---- agent interface ----
    def snapshot(self, phase):
        s = {
            "phase": phase, "timeBudgetSeconds": 15, "teamID": "sim", "sessionID": self.sid, "elapsedSeconds": self.t,
            "counts": {"activeDefenceCount": len(self.defs), "activeEnemyCount": len(self.foes),
                       "enemiesQueued": len(self.queue), "totalEnemiesInWave": len(self.c["seq"])},
            "defences": [{"id": f"d{i}", "type": DEF[d["k"]][0], "health": d["hp"], "maxHealth": DEF[d["k"]][1],
                          "position": {"x": d["x"], "y": d["y"]}} for i, d in enumerate(self.defs)],
            "enemies": [{"id": f"e{i}", "type": ENEMY[e["k"]][0], "health": e["hp"], "maxHealth": ENEMY[e["k"]][1],
                         "position": {"x": round(e["x"]), "y": e["y"]}, "isAttacking": e["attacking"], "isDead": False}
                        for i, e in enumerate(self.foes)],
            "budget": {"remaining": self.coins, "spent": self.spent, "unlocked": self.coins + self.spent,
                       "total": self.c["budget"] + 50 * len(self.c["seq"])},
            "unitCosts": {str(k): v for k, v in COSTS.items()}, "candidatePlacements": [],
        }
        if phase == "start":
            s["upcomingEnemies"] = [{"enemyId": k, "count": self.c["seq"].count(k), "allowedRows": list(r)}
                                    for k, r in self.c["rows"].items()]
            s["spawnSettings"] = {"interArrivalTime": self.c["gap"][0], "timeDecreaseRate": self.c["gap"][1],
                                  "minInterArrivalTime": self.c["gap"][2], "spawnSequence": list(self.c["seq"])}
            s["gameSettings"] = {"reinforcementsEnabled": self.c["interval"] > 0,
                                 "reinforcementIntervalSeconds": self.c["interval"] or 3.0,
                                 "reinforcementHoldSeconds": 3.0, "sendScreenshotToo": False}
        return s

    def ask(self, phase):
        with redirect_stderr(io.StringIO()):
            cards = self.agent.get_reinforcements(self.snapshot(phase))
        seen = set()
        for card in cards:
            k, x, y = card["defenceId"], card["defenceGridPosition"]["x"], card["defenceGridPosition"]["y"]
            if not (0 <= x <= 10 and 0 <= y <= 5) or k not in COSTS or (x, y) in seen:
                continue
            if any(d["x"] == x and d["y"] == y for d in self.defs) or COSTS[k] > self.coins:
                continue
            seen.add((x, y))
            self.coins -= COSTS[k]
            self.spent += COSTS[k]
            self.defs.append({"k": k, "x": x, "y": y, "hp": DEF[k][1], "windup": 0})

    # ---- simulation ----
    def spawn(self):
        k = self.queue.pop(0)
        y = self.rng.choice(list(self.c["rows"][k]))
        self.foes.append({"k": k, "x": 10.0, "y": y, "hp": ENEMY[k][1], "attacking": False, "windup": 0.0, "tgt": None})
        g = self.c["gap"]
        self.next_spawn = self.t + self.gap
        self.gap = max(g[2], self.gap - g[1])

    def first_defence(self, e):
        ahead = [d for d in self.defs if d["y"] == e["y"] and d["x"] <= e["x"] + 0.3]
        return max(ahead, key=lambda d: d["x"]) if ahead else None

    def hurt_defence(self, d, dmg):
        d["hp"] -= dmg
        if d["k"] == 0 and d in self.defs:  # penguin: slides down its row
            self.defs.remove(d)
            for e in list(self.foes):
                if e["y"] == d["y"] and e["x"] >= d["x"] - 0.5:
                    self.hurt_enemy(e, 500)
        elif d["hp"] <= 0 and d in self.defs:
            self.defs.remove(d)
            self.lost += 1

    def blast(self, x, y):
        for d in list(self.defs):
            if (1.5 * (d["x"] - x)) ** 2 + (1.5 * (d["y"] - y)) ** 2 <= 4:
                self.hurt_defence(d, 200)

    def hurt_enemy(self, e, dmg):
        if e not in self.foes:
            return
        e["hp"] -= dmg
        if e["hp"] <= 0:
            self.foes.remove(e)
            self.kills += 1
            self.coins += 50
            if e["k"] == 3:
                self.blast(e["x"], e["y"])

    def step(self):
        for e in list(self.foes):
            name, hp, speed, stop, dps, regen = ENEMY[e["k"]]
            d = self.first_defence(e)
            if d is not None and e["x"] - d["x"] <= stop:
                e["attacking"] = True
                if e["k"] == 3:
                    self.foes.remove(e)
                    self.kills += 1
                    self.blast(e["x"], e["y"])
                    continue
                if e["k"] == 2:
                    if e["tgt"] is None:
                        e["windup"] += DT
                        if e["windup"] < 1.75:
                            continue
                    e["tgt"] = d
                self.hurt_defence(d, dps * DT)
            else:
                e["attacking"] = False
                e["x"] -= speed * DT
                if e["x"] < -0.5:
                    return "lost"
            if regen and e["hp"] < hp:
                e["hp"] = min(hp, e["hp"] + regen * DT)
        for d in list(self.defs):
            if d["k"] == 1:
                row = [e for e in self.foes if e["y"] == d["y"] and e["x"] >= d["x"] - 0.5]
                if row:
                    e = min(row, key=lambda e: e["x"])
                    self.hurt_enemy(e, (40 if e["x"] - d["x"] <= 4.4 else 10) * DT)
            elif d["k"] == 2 and self.foes:
                low = min(e["y"] for e in self.foes)
                e = min((e for e in self.foes if e["y"] == low), key=lambda e: e["x"])
                self.hurt_enemy(e, 26 * DT)
        return None

    def play(self):
        self.spawn()
        self.ask("start")
        next_pause = self.c["interval"] or 1e9
        while True:
            if self.queue and self.t >= self.next_spawn:
                self.spawn()
            if self.step() == "lost":
                return False
            self.t += DT
            if not self.queue and not self.foes:
                return True
            if self.t >= next_pause:
                self.ask("pause")
                next_pause = self.t + self.c["interval"]
            if self.t > 600:
                return False

    def score(self, won):
        total = len(self.c["seq"])
        kills = 25 * self.kills / total
        if not won:
            return kills
        placed = self.spent  # rough: survival by defences lost, economy by coins spent
        return 25 + kills + 25 * max(0, 1 - self.lost / max(1, len(self.defs) + self.lost)) \
            + 10 * max(0, 1 - self.t / 120) + 15 * max(0, 1 - placed / (self.c["budget"] + 50 * total) / 1.5)


def load(path):
    spec = importlib.util.spec_from_file_location("agent_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    games = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    agent = load(sys.argv[2] if len(sys.argv) > 2 else "agent.py")
    print(f"{'case':14} {'wins':>9} {'avg score':>10}")
    for name, case in CASES.items():
        wins, total = 0, 0.0
        for seed in range(games):
            g = Game(case, agent, random.Random(seed))
            won = g.play()
            wins += won
            total += g.score(won)
        print(f"{name:14} {wins:4}/{games:<4} {total / games:10.1f}")


if __name__ == "__main__":
    main()
