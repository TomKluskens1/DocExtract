import subprocess
import sys
from pathlib import Path


def main():
    script_dir = Path(__file__).resolve().parent
    shared_runner = script_dir / "run_benchmark.py"

    forwarded_args = sys.argv[1:]
    command = [
        sys.executable,
        str(shared_runner),
        "--base-url",
        "http://127.0.0.1:5000",
        "--architecture",
        "HOGENT",
        "--warmup-total",
        "5",
        "--steady-repeats",
        "5",
        "--cold-total",
        "0",
        "--output",
        "benchmark_onprem_results.json",
        "--dashboard-export-output",
        "benchmark_onprem_dashboard_export.json",
    ]
    command.extend(forwarded_args)

    completed = subprocess.run(command)
    raise SystemExit(completed.returncode)


if __name__ == "__main__":
    main()
