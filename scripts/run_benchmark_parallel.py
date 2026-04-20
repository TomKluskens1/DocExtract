import argparse
import shutil
import json
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Start Cloud Run en HOGENT benchmarkreeksen parallel volgens het "
            "testprotocol, en start daarna de PWA-laptopbenchmark. "
            "Bewaart een overkoepelend samenvattingsbestand."
        )
    )
    parser.add_argument(
        "--cloud-base-url",
        default="https://extest-web-191306170452.europe-west1.run.app",
        help="Basis-URL van de Cloud Run backend.",
    )
    parser.add_argument(
        "--onprem-base-url",
        default="http://127.0.0.1:5000",
        help="Basis-URL van de HOGENT backend via SSH-tunnel.",
    )
    parser.add_argument(
        "--pdf-dir",
        default=str(Path(__file__).resolve().parents[2] / "Dataset"),
        help="Map met PDF-bestanden.",
    )
    parser.add_argument(
        "--dashboard-export-url",
        default="http://127.0.0.1:8080/api/measurements/export",
        help="MeasurementDashboard export-URL.",
    )
    parser.add_argument(
        "--output-dir",
        default="results",
        help="Basismap voor resultaten met submappen json en graphs.",
    )
    parser.add_argument(
        "--steady-wait",
        type=int,
        default=10,
        help="Aantal seconden tussen steady-state runs.",
    )
    parser.add_argument(
        "--cloud-cold-wait",
        type=int,
        default=600,
        help="Aantal seconden tussen cold-start candidate runs voor cloud.",
    )
    parser.add_argument(
        "--shuffle-seed",
        type=int,
        help="Optionele seed voor reproduceerbare aselecte volgorde in beide reeksen.",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=2,
        help="Aantal retries per mislukte request.",
    )
    parser.add_argument(
        "--retry-wait",
        type=int,
        default=20,
        help="Wachttijd tussen retries.",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop de volledige orchestratie zodra een subprocess faalt.",
    )
    parser.add_argument(
        "--pwa-base-url",
        default="http://127.0.0.1:5000",
        help="Basis-URL van de lokale PWA-app (standaard: %(default)s).",
    )
    parser.add_argument(
        "--skip-pwa",
        action="store_true",
        help="Sla de PWA-benchmarkstap over (handig als je alleen cloud+onprem wilt meten).",
    )
    parser.add_argument(
        "--pwa-energy-mode",
        choices=["lhm", "zero"],
        default="lhm",
        help="Energiemeetmodus voor de PWA-benchmark (standaard: lhm).",
    )
    return parser.parse_args()


def build_batch_id(prefix: str) -> str:
    return f"{prefix}-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"


def launch_runner(command: list[str], log_path: Path):
    log_file = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        command,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return process, log_file


def main():
    args = parse_args()
    script_dir = Path(__file__).resolve().parent
    shared_runner = script_dir / "run_benchmark.py"
    pwa_runner = script_dir / "run_benchmark_pwa_laptop.py"
    graphs_script = script_dir.parents[1] / "latex-hogent-bachproef-main" / "grafieken" / "graphs_results.py"

    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    cloud_dir = output_dir / "cloud"
    server_dir = output_dir / "server"
    pwa_dir = output_dir / "pwa"
    json_cloud_dir = cloud_dir / "json"
    json_server_dir = server_dir / "json"
    json_pwa_dir = pwa_dir / "json"
    graphs_cloud_dir = cloud_dir / "graphs"
    graphs_server_dir = server_dir / "graphs"
    graphs_pwa_dir = pwa_dir / "graphs"
    for path in (
        cloud_dir,
        server_dir,
        pwa_dir,
        json_cloud_dir,
        json_server_dir,
        json_pwa_dir,
        graphs_cloud_dir,
        graphs_server_dir,
        graphs_pwa_dir,
    ):
        path.mkdir(parents=True, exist_ok=True)

    cloud_batch_id = build_batch_id("cloud-benchmark")
    onprem_batch_id = build_batch_id("server-benchmark")
    pwa_batch_id = build_batch_id("pwa-benchmark")

    cloud_output = json_cloud_dir / "benchmark_cloud_results.json"
    cloud_dashboard_output = json_cloud_dir / "benchmark_cloud_dashboard_export.json"
    cloud_log = json_cloud_dir / "benchmark_cloud.log"

    onprem_output = json_server_dir / "benchmark_onprem_results.json"
    onprem_dashboard_output = json_server_dir / "benchmark_onprem_dashboard_export.json"
    onprem_log = json_server_dir / "benchmark_onprem.log"

    pwa_output = json_pwa_dir / "benchmark_pwa_results.json"
    pwa_dashboard_output = json_pwa_dir / "benchmark_pwa_dashboard_export.json"
    pwa_log = json_pwa_dir / "benchmark_pwa.log"

    common_args = [
        "--pdf-dir",
        str(Path(args.pdf_dir).expanduser().resolve()),
        "--steady-wait",
        str(args.steady_wait),
        "--max-retries",
        str(args.max_retries),
        "--retry-wait",
        str(args.retry_wait),
        "--dashboard-export-url",
        args.dashboard_export_url,
        "--shuffle",
    ]
    if args.shuffle_seed is not None:
        common_args.extend(["--shuffle-seed", str(args.shuffle_seed)])
    if args.fail_fast:
        common_args.append("--fail-fast")

    cloud_cmd = [
        sys.executable,
        str(shared_runner),
        "--base-url",
        args.cloud_base_url,
        "--architecture",
        "Cloud Run",
        "--warmup-repeats",
        "1",
        "--steady-repeats",
        "8",
        "--cold-repeats",
        "4",
        "--cold-wait",
        str(args.cloud_cold_wait),
        "--batch-id",
        cloud_batch_id,
        "--output",
        str(cloud_output),
        "--dashboard-export-output",
        str(cloud_dashboard_output),
        *common_args,
    ]

    onprem_cmd = [
        sys.executable,
        str(shared_runner),
        "--base-url",
        args.onprem_base_url,
        "--architecture",
        "HOGENT",
        "--warmup-repeats",
        "1",
        "--steady-repeats",
        "8",
        "--cold-repeats",
        "0",
        "--batch-id",
        onprem_batch_id,
        "--output",
        str(onprem_output),
        "--dashboard-export-output",
        str(onprem_dashboard_output),
        *common_args,
    ]

    pwa_cmd = [
        sys.executable,
        str(pwa_runner),
        "--base-url",
        args.pwa_base_url,
        "--pdf-dir",
        str(Path(args.pdf_dir).expanduser().resolve()),
        "--steady-repeats",
        "8",
        "--batch-id",
        pwa_batch_id,
        "--output",
        str(pwa_output),
        "--dashboard-export-output",
        str(pwa_dashboard_output),
        "--energy-mode",
        args.pwa_energy_mode,
    ]
    if args.shuffle_seed is not None:
        pwa_cmd.extend(["--shuffle-seed", str(args.shuffle_seed)])
    if args.dashboard_export_url:
        pwa_cmd.extend(["--dashboard-export-url", args.dashboard_export_url])

    summary = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "protocol_reference": str(Path(__file__).resolve().parents[2] / "meetprotocol_overzicht.md"),
        "cloud_batch_id": cloud_batch_id,
        "onprem_batch_id": onprem_batch_id,
        "pwa_batch_id": pwa_batch_id,
        "output_dir": str(output_dir),
        "architecture_dirs": {
            "cloud": str(cloud_dir),
            "server": str(server_dir),
            "pwa": str(pwa_dir),
        },
        "cloud": {
            "command": cloud_cmd,
            "log": str(cloud_log),
            "results": str(cloud_output),
            "dashboard_export": str(cloud_dashboard_output),
        },
        "onprem": {
            "command": onprem_cmd,
            "log": str(onprem_log),
            "results": str(onprem_output),
            "dashboard_export": str(onprem_dashboard_output),
        },
        "pwa": {
            "command": pwa_cmd,
            "log": str(pwa_log),
            "results": str(pwa_output),
            "dashboard_export": str(pwa_dashboard_output),
            "skipped": args.skip_pwa,
        },
    }
    summary_path = output_dir / "benchmark_parallel_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(f"Cloud batch_id:  {cloud_batch_id}", flush=True)
    print(f"Server batch_id: {onprem_batch_id}", flush=True)
    print(f"PWA batch_id:    {pwa_batch_id}", flush=True)
    print(f"Logs: {cloud_log} | {onprem_log}", flush=True)
    if not args.skip_pwa:
        print(f"PWA log: {pwa_log} (start na cloud+onprem)", flush=True)

    cloud_proc, cloud_log_handle = launch_runner(cloud_cmd, cloud_log)
    onprem_proc, onprem_log_handle = launch_runner(onprem_cmd, onprem_log)

    processes = [
        ("cloud", cloud_proc, cloud_log_handle),
        ("onprem", onprem_proc, onprem_log_handle),
    ]

    try:
        while processes:
            next_processes = []
            for name, proc, handle in processes:
                returncode = proc.poll()
                if returncode is None:
                    next_processes.append((name, proc, handle))
                    continue

                handle.flush()
                handle.close()
                print(f"{name} klaar met exit code {returncode}", flush=True)
                summary[name]["exit_code"] = returncode
                summary[name]["finished_at"] = datetime.now(timezone.utc).isoformat()
                summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

                if args.fail_fast and returncode != 0:
                    for other_name, other_proc, other_handle in next_processes:
                        if other_proc.poll() is None:
                            other_proc.terminate()
                            other_handle.flush()
                            other_handle.close()
                            summary[other_name]["terminated_due_to_fail_fast"] = True
                    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
                    raise SystemExit(returncode)

            processes = next_processes
            if processes:
                time.sleep(5)
    finally:
        for _, proc, handle in processes:
            if proc.poll() is None:
                proc.terminate()
            try:
                handle.close()
            except Exception:
                pass

    # PWA benchmark — sequentieel na cloud+onprem, want vereist LHM op dezelfde machine
    if not args.skip_pwa:
        print("\nCloud en on-premises klaar. PWA-benchmark starten...", flush=True)
        pwa_log_file = pwa_log.open("w", encoding="utf-8")
        pwa_proc = subprocess.run(
            pwa_cmd,
            stdout=pwa_log_file,
            stderr=subprocess.STDOUT,
            text=True,
        )
        pwa_log_file.flush()
        pwa_log_file.close()
        print(f"PWA klaar met exit code {pwa_proc.returncode}", flush=True)
        summary["pwa"]["exit_code"] = pwa_proc.returncode
        summary["pwa"]["finished_at"] = datetime.now(timezone.utc).isoformat()
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        if args.fail_fast and pwa_proc.returncode != 0:
            raise SystemExit(pwa_proc.returncode)

    if graphs_script.exists():
        graphs_cmd = [
            sys.executable,
            str(graphs_script),
            "--cloud",
            str(cloud_output),
            "--onprem",
            str(onprem_output),
            "--pwa",
            str(pwa_output),
            "--output-dir",
            str(output_dir / "_graphs_tmp"),
        ]
        print("Grafieken genereren...", flush=True)
        graphs_completed = subprocess.run(graphs_cmd)
        summary["graphs"] = {
            "command": graphs_cmd,
            "exit_code": graphs_completed.returncode,
            "output_dir": str(output_dir / "_graphs_tmp"),
        }
        if graphs_completed.returncode == 0:
            tmp_graphs_dir = output_dir / "_graphs_tmp"
            root_files = []
            for filename in (
                "results_0_scatter_all.png",
                "results_0_scatter_all.pdf",
                "results_all.json",
                "results_summary.json",
            ):
                source = tmp_graphs_dir / filename
                if source.exists():
                    destination = output_dir / filename
                    shutil.copy2(source, destination)
                    root_files.append(str(destination))
            copy_targets = {
                "cloud": graphs_cloud_dir,
                "server": graphs_server_dir,
                "pwa": graphs_pwa_dir,
            }
            architecture_scatter_prefixes = {
                "cloud": "results_1_scatter_cloud_run",
                "server": "results_1_scatter_hogent",
                "pwa": "results_1_scatter_pwa",
            }
            shared_prefixes = [
                "results_2_energy_boxplot",
                "results_3_response_time_boxplot",
                "results_4_phase_energy_bars",
                "results_5_energy_components_stacked",
                "results_6_per_pdf_comparison",
                "results_summary",
            ]
            for key, target_dir in copy_targets.items():
                prefixes = [architecture_scatter_prefixes[key], *shared_prefixes]
                copied_files = []
                for item in tmp_graphs_dir.iterdir():
                    if not item.is_file():
                        continue
                    if any(item.name.startswith(prefix) for prefix in prefixes):
                        destination = target_dir / item.name
                        shutil.copy2(item, destination)
                        copied_files.append(str(destination))
                summary["graphs"][key] = {
                    "output_dir": str(target_dir),
                    "files": copied_files,
                }
            summary["graphs"]["root_files"] = root_files
            shutil.rmtree(tmp_graphs_dir, ignore_errors=True)
            summary["graphs"]["output_dir"] = None
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        if graphs_completed.returncode != 0 and args.fail_fast:
            raise SystemExit(graphs_completed.returncode)

    print(f"Samenvatting opgeslagen: {summary_path}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Afgebroken door gebruiker.", file=sys.stderr)
        sys.exit(130)
