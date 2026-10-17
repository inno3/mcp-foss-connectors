"""Contre-verifie Kanboard sans reseau ; source du depot par defaut."""

import argparse
import hashlib
import importlib.util
import os
import sys
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    sys.path.insert(0, str(root))
    sys.dont_write_bytecode = True
    blocked = []

    def offline(event, args):
        if event in {'socket.connect', 'socket.getaddrinfo', 'socket.sendto'}:
            blocked.append(event)
            raise RuntimeError('Reseau interdit pendant la contre-verification')

    sys.addaudithook(offline)
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--source-file', type=Path, default=root / 'kanboard_mcp/server.py')
    options, targets = parser.parse_known_args()
    # Le serveur est charge avant la collecte : ne pas dependre des setdefault
    # des modules de test, ni d'identifiants presents dans l'environnement.
    os.environ.update({
        'KANBOARD_URL': 'https://kb.example.test/jsonrpc.php',
        'KANBOARD_USER': 'alice', 'KANBOARD_TOKEN': 'synthetic-primary',
        'KANBOARD_USER_ALT': 'bot', 'KANBOARD_TOKEN_ALT': 'synthetic-agent',
        'KANBOARD_DEFAULT_AS_USER': 'primary',
    })
    import pytest

    source = options.source_file.read_bytes()

    class SourcePlugin:
        def pytest_sessionstart(self, session):
            # Charger apres les plugins pytest : anyio reste reecrit normalement.
            import kanboard_mcp

            spec = importlib.util.spec_from_loader('kanboard_mcp.server', loader=None)
            module = importlib.util.module_from_spec(spec)
            module.__file__ = str(options.source_file)
            sys.modules[spec.name] = module
            exec(compile(source, module.__file__, 'exec'), module.__dict__)
            kanboard_mcp.server = module

    print('Source SHA-256:', hashlib.sha256(source).hexdigest())
    result = pytest.main(['-q', '-p', 'no:cacheprovider', *(targets or [
        'tests/test_kanboard_agent_identity.py', 'tests/test_kanboard_compat_016.py',
    ])], plugins=[SourcePlugin()])
    if blocked:
        print(f'ECHEC: {len(blocked)} tentative(s) reseau bloquee(s).')
        return 1
    print('Aucune tentative de connexion reseau pendant les tests.')
    return int(result)


if __name__ == '__main__':
    raise SystemExit(main())
