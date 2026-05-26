import subprocess
import sys
from pathlib import Path


def main():
    script_dir = Path(__file__).resolve().parent
    shared_runner = script_dir / "run_benchmark.py"
    graphs_script = script_dir.parent / "graphs" / "graphs_results.py"

    results_root = script_dir / "results"
    output_dir = results_root / "server" / "json"
    output_dir.mkdir(parents=True, exist_ok=True)
    results_path = output_dir / "benchmark_onprem_results.json"
    dashboard_export_path = output_dir / "benchmark_onprem_dashboard_export.json"

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
        str(results_path),
        "--dashboard-export-output",
        str(dashboard_export_path),
    ]
    command.extend(forwarded_args)

    completed = subprocess.run(command)
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)

    if not results_path.exists():
        print(f"Resultaatbestand niet gevonden, grafieken overgeslagen: {results_path}", flush=True)
        raise SystemExit(0)

    if not graphs_script.exists():
        print(f"graphs_results.py niet gevonden op {graphs_script}, grafieken overgeslagen.", flush=True)
        raise SystemExit(0)

    graphs_cmd = [
        sys.executable,
        str(graphs_script),
        "--onprem",
        str(results_path),
        "--ground-truth",
        str(results_root / "ground_truth_expected_fields.json"),
        "--output-dir",
        str(results_root),
    ]
    print("Grafieken genereren...", flush=True)
    graphs_completed = subprocess.run(graphs_cmd)
    raise SystemExit(graphs_completed.returncode)


if __name__ == "__main__":
    main()
