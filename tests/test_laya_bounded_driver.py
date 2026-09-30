# Path: tests/test_laya_bounded_driver.py | Purpose: Exercise the real driver round cap and cleanup with isolated fake training tools.

import os
from pathlib import Path
import subprocess
import tarfile


ROOT = Path(__file__).resolve().parents[1]


def executable(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    path.chmod(0o755)


def test_round_cap_rotates_three_levels_and_stops_only_selected_workers(tmp_path):
    repo = tmp_path / "repo"
    driver = repo / "scripts/laya_overnight.sh"
    executable(driver, (ROOT / "scripts/laya_overnight.sh").read_text())
    (repo / "training-workers-linux.json").write_text('{}')
    executable(repo / "scripts/manage_linux_training_worker.sh", '#!/bin/bash\necho "$*" >> "$LAYA_CALLS"\n')
    executable(repo / ".venv/bin/python", '#!/bin/bash\nexit 0\n')
    executable(repo / "scripts/laya_policy_iteration_round.sh", '''#!/bin/bash
mkdir -p "$LAYA_REPO/data/laya-pi" "$LAYA_REPO/data/laya-exploration/batches"
echo '{}' > "$LAYA_REPO/data/laya-pi/features-$1.json"
echo '{}' > "$LAYA_REPO/data/laya-exploration/batches/eval-$1-L$2.jsonl"
''')
    archive = tmp_path / "code.tar"
    with tarfile.open(archive, "w") as out:
        out.add(repo / "scripts", arcname="scripts")
    bin_dir = tmp_path / "bin"
    executable(bin_dir / "git", '#!/bin/bash\ncase "$3" in archive) cat "$LAYA_ARCHIVE";; rev-parse) echo frozen;; *) exit 2;; esac\n')
    calls = tmp_path / "calls"
    # The frozen driver must continue even if the original file changes while
    # a round is running (Bash otherwise resumes at stale byte offsets).
    round_script = repo / 'scripts/laya_policy_iteration_round.sh'
    round_script.write_text(round_script.read_text()+'\nprintf "#!/bin/bash\\nexit 77\\n" > "$LAYA_REPO/scripts/laya_overnight.sh"\n')
    with tarfile.open(archive, 'w') as out:
        out.add(repo / 'scripts', arcname='scripts')
    env = {k: v for k, v in os.environ.items() if not k.startswith('LAYA_')}
    env.update(PATH=f'{bin_dir}:{env["PATH"]}', LAYA_ARCHIVE=str(archive), LAYA_CALLS=str(calls),
               LAYA_RUN_NAME="bounded", LAYA_PREFIX="t", LAYA_INSTANCES="01 02", LAYA_MAX_ROUNDS="3",
               LAYA_FINETUNE_EVERY="0", LAYA_SEEDS_PER_ROUND="6")
    result = subprocess.run([str(driver), "4"], env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    summary = (repo / "data/laya-pi/bounded/summary.log").read_text()
    assert "t-r1: level 2 seeds 6001+6" in summary
    assert "t-r2: level 1 seeds 5101+6" in summary
    assert "t-r3: level 3 seeds 8101+6 continuation rollout" in summary
    assert "t-r4:" not in summary
    assert "round limit reached: 3 new rounds" in summary
    assert "done; training workers stopped" in summary
    stops = [line for line in calls.read_text().splitlines() if line.startswith('stop ')]
    assert stops == ['stop --worker-count 20 --only 1', 'stop --worker-count 20 --only 2']
