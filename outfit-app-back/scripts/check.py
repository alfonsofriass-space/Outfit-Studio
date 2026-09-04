from __future__ import annotations

import os
import platform
import subprocess
import sys

CHECKS = (
    ("Ruff lint", (sys.executable, "-m", "ruff", "check", ".")),
    ("Ruff format", (sys.executable, "-m", "ruff", "format", "--check", ".")),
    ("Pytest", (sys.executable, "-m", "pytest")),
)


def _check_environment() -> dict[str, str]:
    environment = os.environ.copy()
    if sys.platform == "linux" and "microsoft" in platform.release().casefold():
        # Algunas terminales de WSL heredan TEMP/TMP de Windows. Pytest
        # crea y retira capturas concurrentes allí, lo que puede dejar rutas
        # intermedias ausentes. El temporal nativo de Linux evita esa carrera.
        environment.update(TMPDIR="/tmp", TMP="/tmp", TEMP="/tmp")
    return environment


def main() -> int:
    environment = _check_environment()
    for label, command in CHECKS:
        print(f"\n==> {label}", flush=True)
        result = subprocess.run(command, check=False, env=environment)
        if result.returncode:
            return result.returncode

    print("\nAll checks passed.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
