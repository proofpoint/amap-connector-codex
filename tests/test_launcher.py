"""Host launcher contract checks using a synthetic Docker client, not isolation proof."""
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def docker_fixture(tmp_path, exists=False):
    binary = tmp_path / "docker"
    binary.write_text('''#!/usr/bin/env python3
import json,os,sys
with open(os.environ['LAUNCHER_TEST_LOG'],'a') as f: f.write(json.dumps(sys.argv[1:])+'\\n')
if sys.argv[1:3]==['container','inspect']: sys.exit(0 if os.environ.get('LAUNCHER_TEST_EXISTS')=='1' else 1)
if sys.argv[1]=='attach':
 for line in sys.stdin: sys.stdout.write(line); sys.stdout.flush()
''')
    binary.chmod(0o755)
    compose = tmp_path / "compose.yaml"
    compose.write_text("services: {}\n")
    log = tmp_path / "calls.jsonl"
    env = {**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
           "LAUNCHER_TEST_LOG": str(log), "LAUNCHER_TEST_EXISTS": "1" if exists else "0"}
    return compose, log, env


def test_launcher_preserves_stdin_stdout_and_stops_exact_container(tmp_path):
    compose, log, env = docker_fixture(tmp_path)
    frame = '{"id":1,"method":"initialize"}\n'
    result = subprocess.run([str(ROOT / "deploy/launch-app-server.sh"), str(compose), "amap-test-instance"],
                            input=frame, text=True, capture_output=True, env=env, timeout=5)
    assert result.returncode == 0
    assert result.stdout == frame
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert calls[0] == ["container", "inspect", "amap-test-instance"]
    assert calls[-1] == ["stop", "--time", "2", "amap-test-instance"]
    assert any("--no-TTY" in call and "--interactive" in call for call in calls)


def test_surviving_remote_container_blocks_second_controller(tmp_path):
    compose, log, env = docker_fixture(tmp_path, exists=True)
    result = subprocess.run([str(ROOT / "deploy/launch-app-server.sh"), str(compose), "amap-test-instance"],
                            text=True, capture_output=True, env=env, timeout=5)
    assert result.returncode == 2
    assert result.stdout == ""
    assert "operator inspection" in result.stderr
    assert len(log.read_text().splitlines()) == 1
