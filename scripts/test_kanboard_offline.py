#!/usr/bin/env python3
"""Exécute les tests Kanboard sans connexion réseau ni installation.

Utiliser un interpréteur disposant déjà de pytest et des dépendances du dépôt.
Sans argument : tests/test_kanboard*.py. Les arguments explicites vont à pytest.
"""

import os
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    sys.path.insert(0, str(root))
    sys.dont_write_bytecode = True
    blocked = []

    def offline(event, _args):
        if event in {"socket.connect", "socket.getaddrinfo", "socket.sendto"}:
            blocked.append(event)
            raise RuntimeError("Accès réseau interdit pendant les tests Kanboard")

    sys.addaudithook(offline)
    import pytest

    targets = sys.argv[1:] or sorted(str(p) for p in Path("tests").glob("test_kanboard*.py"))
    result = pytest.main(["-q", "-p", "no:cacheprovider", *targets])
    if blocked:
        print(f"Échec : {len(blocked)} tentative(s) réseau bloquée(s).", file=sys.stderr)
        return 1
    print("Aucune tentative de connexion réseau pendant les tests.")
    return int(result)


if __name__ == "__main__":
    raise SystemExit(main())
