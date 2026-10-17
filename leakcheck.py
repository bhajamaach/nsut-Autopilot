"""Count simulated losses where the row the enemy leaked through had NO defence (an invariant violation).
Run: .venv/bin/python leakcheck.py [agent file] [seeds]"""
import random
import sys

import search
import sim

agent = sim.load(sys.argv[1] if len(sys.argv) > 1 else "agent.py")
seeds = int(sys.argv[2]) if len(sys.argv) > 2 else 6
bad = total_losses = games = 0
examples = []
for name, case, _ in search.case_set(40):
    for seed in range(seeds):
        g = sim.Game(case, agent, random.Random(seed))
        orig = g.step

        def step(g=g, orig=orig):
            r = orig()
            if r == "lost":
                e = min(g.foes, key=lambda e: e["x"])
                g.leak_row_empty = not any(d["y"] == e["y"] for d in g.defs)
                g.leak_row = e["y"]
            return r
        g.step = step
        won = g.play()
        games += 1
        if not won:
            total_losses += 1
            if getattr(g, "leak_row_empty", False):
                bad += 1
                if len(examples) < 5:
                    examples.append((name, seed, g.leak_row, round(g.t, 1)))
print(f"games {games}  losses {total_losses}  leaks through an EMPTY row {bad}")
print(examples)
