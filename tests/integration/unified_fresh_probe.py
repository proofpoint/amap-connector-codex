"""Fresh canonical creation and continued native use, without an isolation adapter."""
import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import subprocess
import tempfile

from unified_runtime_probe import ordinary


async def probe(output):
    build = subprocess.check_output(['codex', '--version'], text=True).splitlines()[0]
    with tempfile.TemporaryDirectory(prefix='amap-fresh-') as directory:
        result = await ordinary(Path(directory), build)
    evidence = {'timestamp_utc': datetime.now(timezone.utc).isoformat(),
                'codex_version': build, 'os': platform.system(),
                'architecture': platform.machine(),
                'scope': 'portable fresh unified service and native TUI; no adapter or host acceptance',
                'external_inference': False, 'user_auth_used': False,
                'fleet_submissions': 0, 'ordinary': result,
                'probe_assertions_passed': True, 'fleet_ready': False}
    output.write_text(json.dumps(evidence, indent=2) + '\n')
    print(json.dumps(evidence))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    asyncio.run(probe(parser.parse_args().output))
