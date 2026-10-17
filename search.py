"""Random search over agent knobs in the simulator (sim.py), on generated cases like the public ones.

Run: .venv/bin/python search.py [agent file] [configs] [seeds per case]
The 4 real public cases are weighted x3. Prints the best knob settings found.
"""
import json
import multiprocessing as mp
import random
import sys

import sim

TYPES = [0, 1, 2, 3]


def gen_case(rng):
    n = rng.randint(10, 18)
    kinds = rng.sample(TYPES, rng.choice([2, 3, 4, 4, 4]))
    cuts = sorted(rng.sample(range(1, n), len(kinds) - 1))
    counts = [b - a for a, b in zip([0] + cuts, cuts + [n])]
    rows = {}
    for k in kinds:
        size = rng.choice([2, 3, 3, 4, 6])
        pattern = rng.choice(["random", "block", "alt"])
        if pattern == "block":
            start = rng.randint(0, 6 - size)
            rows[k] = list(range(start, start + size))
        elif pattern == "alt":
            rows[k] = [r for r in range(rng.randint(0, 1), 6, 2)][:size] or [0]
        else:
            rows[k] = sorted(rng.sample(range(6), size))
    seq = [k for k, c in zip(kinds, counts) for _ in range(c)]
    rng.shuffle(seq)
    tight = rng.random() < 0.8  # the public levels other than TC01 give only 140-170 coins per enemy
    return dict(budget=max(900, round(n * (rng.uniform(120, 200) if tight else rng.uniform(200, 290)), -2)),
                interval=rng.choice([3.0, 5.0, 8.0, 10.0, 12.0, 10.0, 0]),
                gap=(round(rng.uniform(1, 6), 1), round(rng.uniform(0, 0.3), 2), round(rng.uniform(0.6, 1.0), 1)),
                seq=seq, rows=rows)


def case_set(n_generated, seed=1234):
    rng = random.Random(seed)
    cases = [(name, c, 3) for name, c in sim.CASES.items() if name in ("TC01", "TC02", "TC05", "TC09")]
    cases += [(f"gen{i}", gen_case(rng), 1) for i in range(n_generated)]
    return cases


_agent = None
_cases = None
_seeds = 4


def _init(path, n_generated, seeds):
    global _agent, _cases, _seeds
    _agent = sim.load(path)
    _cases = case_set(n_generated)
    _seeds = seeds


def evaluate(knobs):
    for k, v in knobs.items():
        setattr(_agent, k, v)
    total = weight = wins = games = 0
    per = {}
    for name, case, w in _cases:
        sc = wn = 0
        for seed in range(_seeds):
            g = sim.Game(case, _agent, random.Random(seed))
            won = g.play()
            sc += g.score(won)
            wn += won
        per[name] = wn / _seeds
        total += w * sc / _seeds
        weight += w
        wins += w * wn / _seeds
        games += w
    return total / weight, wins / games, per


SPACE = {
    "SHIELD_X": [3, 4, 5, 6],
    "OPEN_SOLDIERS": ["first", "rows"],
    "OPEN_SOLDIER_MAX": [1, 2, 3, 4, 6],
    "RESERVE": [0, 150, 250, 400],
    "WIZ_RESERVE": [0, 250, 400],
    "RICH_MARGIN": [0, 150, 300],
    "MAX_SOLDIERS": [1, 2, 3],
    "HOLD_MARGIN": [0.8, 1.0, 1.2, 1.6],
    "URGENT_SLACK": [0, 3, 6],
    "PILE_MIN": [1, 2, 3],
    "BLOCK_FWD": [1, 2, 3],
    "WIZ_MODE": ["penguin", "soldiers", "auto"],
    "OPEN_ARCHERS": [0, 1, 2, 3],
    "ARCHER_MAX": [0, 2, 4, 6],
    "ARCHER_RICH": [0, 300, 600, 1200],
    "ARCHER_ANY": [False, True],
    "BACKSTOP_WIZARD": [False, True],
    "BACKSTOP_MAX_INTERVAL": [0, 6, 12],
    "JIT_SHIELDS": [False, True],
}


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "agent_next.py"
    n_cfg = int(sys.argv[2]) if len(sys.argv) > 2 else 200
    seeds = int(sys.argv[3]) if len(sys.argv) > 3 else 4
    rng = random.Random(int(sys.argv[5]) if len(sys.argv) > 5 else 7)
    if len(sys.argv) > 4:  # a JSON file narrowing the space: {"KNOB": [values...]}
        SPACE.update(json.load(open(sys.argv[4])))
    base = {k: v[0] for k, v in SPACE.items()}
    probe = sim.load(path)
    base = {k: getattr(probe, k) for k in SPACE if hasattr(probe, k)}
    configs = [dict(base)] + [{k: rng.choice(v) for k, v in SPACE.items() if k in base} for _ in range(n_cfg)]
    with mp.Pool(8, initializer=_init, initargs=(path, 40, seeds)) as pool:
        results = pool.map(evaluate, configs, chunksize=2)
    ranked = sorted(zip(results, configs), key=lambda rc: -rc[0][0])
    print("baseline:", round(results[0][0], 1), "score,", round(results[0][1] * 100), "% wins")
    for (score, winrate, per), cfg in ranked[:5]:
        real = {k: per[k] for k in ("TC01", "TC02", "TC05", "TC09")}
        print(f"\nscore {score:.1f}  wins {winrate * 100:.0f}%  real cases {real}")
        print(json.dumps(cfg))


if __name__ == "__main__":
    main()
