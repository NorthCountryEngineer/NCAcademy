"""Create local-only Compose secrets without overwriting an existing setup."""
import os
from pathlib import Path
import secrets

root = Path(__file__).resolve().parents[1]
target = root / '.env'
values = ('APP_URL=http://localhost:8090\nAPP_PORT=8090\nAUTH_MODE=local\n'
          'DEMO_SEED=true\nPOSTGRES_PASSWORD=' + secrets.token_hex(32) +
          '\nSESSION_SECRET=' + secrets.token_hex(48) + '\n')
try:
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
except FileExistsError:
    print('Existing .env preserved.')
else:
    with os.fdopen(descriptor, 'w') as stream:
        stream.write(values)
    print('Created private .env for local demonstration. Live models remain disabled.')
