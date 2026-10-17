"""Print one simulated game as a timeline: python sim_trace.py CASE SEED [agent file]"""
import random
import sys

import sim

case, seed = sys.argv[1], int(sys.argv[2])
agent = sim.load(sys.argv[3] if len(sys.argv) > 3 else "agent.py")
if len(sys.argv) > 4:
    import json
    for k, v in json.loads(sys.argv[4]).items():
        setattr(agent, k, v)
g = sim.Game(sim.CASES[case], agent, random.Random(seed))
orig_ask, orig_spawn, orig_hd = g.ask, g.spawn, g.hurt_defence


def ask(phase):
    ids = {id(d) for d in g.defs}
    c0 = g.coins
    orig_ask(phase)
    new = [(sim.DEF[d["k"]][0][7:10], d["x"], d["y"]) for d in g.defs if id(d) not in ids]
    print(f"t={g.t:5.1f} {phase:5} coins {c0:5.0f} foes "
          f"{[(sim.ENEMY[e['k']][0][-6:], round(e['x'], 1), e['y'], round(e['hp'])) for e in g.foes]}")
    print(f"        defs {[(sim.DEF[d['k']][0][7:10], d['x'], d['y'], round(d['hp'])) for d in g.defs if id(d) in ids]}")
    print(f"        -> {new}")


def spawn():
    orig_spawn()
    e = g.foes[-1]
    print(f"t={g.t:5.1f} spawn {sim.ENEMY[e['k']][0]} row {e['y']}")


def hd(d, dmg):
    alive = d in g.defs
    orig_hd(d, dmg)
    if alive and d not in g.defs:
        print(f"t={g.t:5.1f}   gone {sim.DEF[d['k']][0]} ({d['x']},{d['y']})")


g.ask, g.spawn, g.hurt_defence = ask, spawn, hd
print("WON" if g.play() else f"LOST t={g.t:.1f} kills {g.kills}/{len(g.c['seq'])}")
