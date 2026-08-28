# -*- coding: utf-8 -*-
"""以有限并发执行独立E3射线，并为每条射线保存进程日志。"""

import argparse
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from run_e3_formal import expand_rays, validate_config


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--start-index", type=int, default=0)
    parser.add_argument("--stop-index", type=int)
    args = parser.parse_args()

    config_path = args.config.resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    validate_config(config)
    ray_count = len(expand_rays(config))
    stop = ray_count if args.stop_index is None else args.stop_index
    if args.workers < 1 or not (0 <= args.start_index <= stop <= ray_count):
        raise ValueError("invalid workers or ray index interval")

    output_dir = args.output_dir.resolve()
    log_dir = output_dir / "process_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    runner = Path(__file__).resolve().parent / "run_e3_formal.py"

    def run(ray_index):
        log_path = log_dir / f"ray_{ray_index:04d}.log"
        command = [
            sys.executable,
            str(runner),
            "--config",
            str(config_path),
            "--output-dir",
            str(output_dir),
            "--ray-index",
            str(ray_index),
        ]
        with log_path.open("w", encoding="utf-8") as log:
            completed = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT)
        return ray_index, completed.returncode, log_path

    failures = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [
            pool.submit(run, ray_index) for ray_index in range(args.start_index, stop)
        ]
        for future in as_completed(futures):
            ray_index, returncode, log_path = future.result()
            print(
                json.dumps(
                    {
                        "ray_index": ray_index,
                        "returncode": returncode,
                        "log": str(log_path),
                    }
                ),
                flush=True,
            )
            if returncode != 0:
                failures.append(ray_index)
    if failures:
        raise SystemExit(f"failed rays: {sorted(failures)}")


if __name__ == "__main__":
    main()
