# -*- coding: utf-8 -*-
"""等待六条定向射线完成，并触发聚合与机器契约冻结。"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--poll-seconds", type=float, default=30.0)
    parser.add_argument("--timeout-hours", type=float, default=24.0)
    args = parser.parse_args()

    config_path = args.config.resolve()
    output_dir = args.output_dir.resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    expected = int(config["acceptance"]["expected_ray_count"])
    if args.poll_seconds <= 0.0 or args.poll_seconds > 60.0:
        raise ValueError("poll-seconds must be in (0, 60]")
    if args.timeout_hours <= 0.0:
        raise ValueError("timeout-hours must be positive")

    deadline = time.time() + float(args.timeout_hours) * 3600.0
    summary_paths = [
        output_dir / f"e2_forcing_refinement_ray_{index:04d}_summary.json"
        for index in range(expected)
    ]
    while True:
        observed = sum(path.is_file() for path in summary_paths)
        print(json.dumps({"observed_summary_count": observed, "expected": expected}), flush=True)
        if observed == expected:
            runner = Path(__file__).with_name("run_e2_forcing_boundary_refinement.py").resolve()
            command = [
                sys.executable,
                str(runner),
                "--config",
                str(config_path),
                "--output-dir",
                str(output_dir),
                "--workers",
                "1",
            ]
            completed = subprocess.run(command, check=False)
            if completed.returncode != 0:
                raise SystemExit(completed.returncode)
            return
        if time.time() >= deadline:
            raise TimeoutError(
                f"timed out waiting for {expected} summaries in {output_dir}"
            )
        time.sleep(min(float(args.poll_seconds), 60.0))


if __name__ == "__main__":
    main()
