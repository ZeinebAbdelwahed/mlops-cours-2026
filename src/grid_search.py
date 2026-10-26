"""Lance 22 runs d'entraînement (un sous-processus par run) dans l'expérience « scoring »."""
import os
import subprocess
import sys
from itertools import product

CONFIGS = [
    ("dummy", []),
    *[
        ("logistic_regression", ["--C", str(c), "--class_weight", w])
        for c, w in product([0.01, 0.1, 1.0, 10.0], ["none", "balanced"])
    ],
    *[
        ("random_forest", ["--n_estimators", str(n), "--max_depth", str(d)])
        for n, d in product([100, 200, 400], [4, 8, "none"])
    ],
    *[
        ("gradient_boosting", ["--n_estimators", str(n), "--max_depth", str(d)])
        for n, d in product([100, 200], [2, 3])
    ],
]


def main() -> None:
    print(f"Lancement de {len(CONFIGS)} runs...")
    env = {**os.environ, "LAUNCHED_BY": "grid_search"}
    for i, (model, args) in enumerate(CONFIGS, 1):
        cmd = [sys.executable, "-m", "src.train", "--model", model, *args]
        print(f"[{i}/{len(CONFIGS)}] {' '.join(cmd[1:])}", flush=True)
        subprocess.run(cmd, check=True, env=env)
    print("✓ Tous les runs sont terminés.")


if __name__ == "__main__":
    main()
