import argparse
import json
import mimetypes
import random
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import urlencode
from urllib import error, request


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Automatiseer benchmark-runs tegen de DocExtract /extract endpoint. "
            "Voert warm-up, steady-state en cold-start candidate runs uit en logt elke run."
        )
    )
    parser.add_argument(
        "--base-url",
        default="https://extest-web-191306170452.europe-west1.run.app",
        help="Basis-URL van de backend, bv. https://...run.app of http://127.0.0.1:5000",
    )
    parser.add_argument(
        "--pdf",
        action="append",
        dest="pdfs",
        default=[],
        help="Pad naar een PDF. Mag meerdere keren opgegeven worden.",
    )
    parser.add_argument(
        "--pdf-dir",
        default=str(Path(__file__).resolve().parents[2] / "Dataset"),
        help="Map met PDF's. Alle .pdf bestanden worden alfabetisch toegevoegd.",
    )
    parser.add_argument(
        "--warmup-repeats",
        type=int,
        default=1,
        help="Aantal warm-up runs per PDF.",
    )
    parser.add_argument(
        "--steady-repeats",
        type=int,
        default=8,
        help="Aantal steady-state runs per PDF.",
    )
    parser.add_argument(
        "--cold-repeats",
        type=int,
        default=4,
        help="Aantal cold-start candidate runs per PDF.",
    )
    parser.add_argument(
        "--steady-wait",
        type=int,
        default=10,
        help="Aantal seconden wachten tussen steady-state runs.",
    )
    parser.add_argument(
        "--cold-wait",
        type=int,
        default=600,
        help="Aantal seconden wachten tussen cold-start candidate runs.",
    )
    parser.add_argument(
        "--architecture",
        default="Cloud Run",
        help="Waarde voor het architecture form field. Standaard: Cloud Run",
    )
    parser.add_argument(
        "--output",
        default="benchmark_results.json",
        help="Pad voor het JSON resultaatbestand.",
    )
    parser.add_argument(
        "--batch-id",
        default=f"benchmark-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}",
        help="Unieke batch-id die meegestuurd en opgeslagen wordt bij elke run.",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=2,
        help="Aantal retries na een mislukte extractie-aanroep.",
    )
    parser.add_argument(
        "--retry-wait",
        type=int,
        default=20,
        help="Aantal seconden wachten tussen retries.",
    )
    parser.add_argument(
        "--dashboard-export-url",
        help="Optionele MeasurementDashboard export-URL, bv. http://127.0.0.1:8080/api/measurements/export",
    )
    parser.add_argument(
        "--dashboard-export-output",
        default="benchmark_dashboard_export.json",
        help="Bestand waarin de dashboard-export opgeslagen wordt als --dashboard-export-url gebruikt wordt.",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop onmiddellijk bij de eerste fout.",
    )
    parser.add_argument(
        "--shuffle",
        action="store_true",
        help="Voer de PDF-volgorde per fase aselect uit.",
    )
    parser.add_argument(
        "--shuffle-seed",
        type=int,
        help="Optionele seed voor reproduceerbare aselecte volgorde.",
    )
    return parser.parse_args()


def collect_pdfs(args) -> list[Path]:
    pdfs: list[Path] = [Path(p).expanduser().resolve() for p in args.pdfs]
    if args.pdf_dir:
        pdf_dir = Path(args.pdf_dir).expanduser().resolve()
        pdfs.extend(sorted(pdf_dir.glob("*.pdf")))

    unique: list[Path] = []
    seen = set()
    for pdf in pdfs:
        key = str(pdf)
        if key in seen:
            continue
        seen.add(key)
        unique.append(pdf)

    missing = [str(pdf) for pdf in unique if not pdf.exists()]
    if missing:
        raise FileNotFoundError(f"PDF niet gevonden: {', '.join(missing)}")
    if not unique:
        raise ValueError("Geen PDF's opgegeven. Gebruik --pdf of --pdf-dir.")
    return unique


def build_multipart_body(pdf_path: Path, architecture: str, batch_id: str):
    boundary = f"----DocExtractBoundary{int(time.time() * 1000)}"
    mime = mimetypes.guess_type(pdf_path.name)[0] or "application/pdf"

    parts = []
    parts.append(
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="architecture"\r\n\r\n'
            f"{architecture}\r\n"
        ).encode("utf-8")
    )
    parts.append(
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="batch_id"\r\n\r\n'
            f"{batch_id}\r\n"
        ).encode("utf-8")
    )
    parts.append(
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{pdf_path.name}"\r\n'
            f"Content-Type: {mime}\r\n\r\n"
        ).encode("utf-8")
    )
    parts.append(pdf_path.read_bytes())
    parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    return boundary, b"".join(parts)


def post_extract(base_url: str, pdf_path: Path, architecture: str, batch_id: str):
    boundary, body = build_multipart_body(pdf_path, architecture, batch_id)
    endpoint = base_url.rstrip("/") + "/extract"
    req = request.Request(
        endpoint,
        data=body,
        method="POST",
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Accept": "application/json",
        },
    )

    started = time.time()
    try:
        with request.urlopen(req, timeout=900) as response:
            raw = response.read().decode("utf-8")
            payload = json.loads(raw)
            return response.status, payload, time.time() - started
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"error": raw}
        return exc.code, payload, time.time() - started


def fetch_dashboard_export(dashboard_export_url: str, batch_id: str):
    params = urlencode({"download": "0", "date_filter": "TODAY", "batch_id": batch_id})
    separator = "&" if "?" in dashboard_export_url else "?"
    url = f"{dashboard_export_url}{separator}{params}"
    req = request.Request(url, method="GET", headers={"Accept": "application/json"})
    with request.urlopen(req, timeout=120) as response:
        raw = response.read().decode("utf-8")
        return response.status, json.loads(raw)


def iter_phase_runs(phase: str, pdfs: list[Path], repeats: int) -> Iterable[tuple[str, int, int, int, int, Path]]:
    if repeats <= 0:
        return

    total_runs = len(pdfs) * repeats
    run_number = 0
    for repeat_index in range(1, repeats + 1):
        for pdf_index, pdf_path in enumerate(pdfs, start=1):
            run_number += 1
            yield phase, run_number, total_runs, repeat_index, pdf_index, pdf_path


def build_phase_plan(phase: str, pdfs: list[Path], repeats: int, rng: random.Random | None):
    runs = list(iter_phase_runs(phase, pdfs, repeats))
    if rng is not None:
        rng.shuffle(runs)
    return runs


def main():
    args = parse_args()
    pdfs = collect_pdfs(args)
    output_path = Path(args.output).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    summary = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "base_url": args.base_url,
        "architecture": args.architecture,
        "batch_id": args.batch_id,
        "shuffle": bool(args.shuffle),
        "shuffle_seed": args.shuffle_seed,
        "pdfs": [str(pdf) for pdf in pdfs],
        "plan": {
            "pdf_count": len(pdfs),
            "warmup_repeats_per_pdf": args.warmup_repeats,
            "steady_repeats_per_pdf": args.steady_repeats,
            "cold_repeats_per_pdf": args.cold_repeats,
            "warmup_runs_total": len(pdfs) * args.warmup_repeats,
            "steady_runs_total": len(pdfs) * args.steady_repeats,
            "cold_runs_total": len(pdfs) * args.cold_repeats,
            "steady_wait_s": args.steady_wait,
            "cold_wait_s": args.cold_wait,
        },
        "results": [],
    }

    rng = random.Random(args.shuffle_seed) if args.shuffle else None
    warmup_plan = build_phase_plan("warmup", pdfs, args.warmup_repeats, rng)
    measurement_plan = build_phase_plan("steady", pdfs, args.steady_repeats, rng)
    measurement_plan.extend(build_phase_plan("cold_candidate", pdfs, args.cold_repeats, rng))

    for idx, (_, phase_index, phase_total, repeat_index, pdf_order, pdf_path) in enumerate(warmup_plan, start=1):
        print(
            f"[warmup {idx}/{len(warmup_plan)}] warmup {phase_index}/{phase_total} "
            f"(repeat {repeat_index}, pdf {pdf_order}/{len(pdfs)}) -> {pdf_path.name}",
            flush=True,
        )

        attempt = 0
        status_code = 0
        payload = {}
        wall_time_s = 0.0
        ok = False
        while attempt <= args.max_retries:
            attempt += 1
            status_code, payload, wall_time_s = post_extract(
                args.base_url, pdf_path, args.architecture, args.batch_id
            )
            ok = 200 <= status_code < 300
            if ok:
                break
            if attempt <= args.max_retries:
                print(
                    f"  retry {attempt}/{args.max_retries} na HTTP {status_code}, wachten {args.retry_wait}s",
                    flush=True,
                )
                time.sleep(args.retry_wait)

        if not ok:
            print(f"  warmup fout: HTTP {status_code} -> {payload}", flush=True)
            if args.fail_fast:
                break

    total_runs = len(measurement_plan)
    run_counter = 0

    for phase, phase_index, phase_total, repeat_index, pdf_order, pdf_path in measurement_plan:
        run_counter += 1

        print(
            f"[{run_counter}/{total_runs}] {phase} {phase_index}/{phase_total} "
            f"(repeat {repeat_index}, pdf {pdf_order}/{len(pdfs)}) -> {pdf_path.name}",
            flush=True,
        )

        attempt = 0
        status_code = 0
        payload = {}
        wall_time_s = 0.0
        ok = False
        while attempt <= args.max_retries:
            attempt += 1
            status_code, payload, wall_time_s = post_extract(
                args.base_url, pdf_path, args.architecture, args.batch_id
            )
            ok = 200 <= status_code < 300
            if ok:
                break
            if attempt <= args.max_retries:
                print(
                    f"  retry {attempt}/{args.max_retries} na HTTP {status_code}, wachten {args.retry_wait}s",
                    flush=True,
                )
                time.sleep(args.retry_wait)

        metrics = payload.get("metrics", {}) if isinstance(payload, dict) else {}
        result = {
            "run_number": run_counter,
            "phase": phase,
            "phase_index": phase_index,
            "phase_total": phase_total,
            "batch_id": args.batch_id,
            "repeat_index_for_pdf": repeat_index,
            "pdf_order": pdf_order,
            "pdf": str(pdf_path),
            "requested_at": datetime.now(timezone.utc).isoformat(),
            "http_status": status_code,
            "wall_time_s": wall_time_s,
            "ok": ok,
            "attempts_used": attempt,
            "measurement_id": metrics.get("measurement_id"),
            "response_time": metrics.get("execution_time_s"),
            "setup_time_s": metrics.get("setup_time_s"),
            "document_status": metrics.get("document_status"),
            "payload": payload,
        }
        summary["results"].append(result)
        output_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

        if not result["ok"]:
            print(f"  fout: HTTP {status_code} -> {payload}", flush=True)
            if args.fail_fast:
                break

        wait_s = 0
        if phase == "steady" and phase_index < phase_total:
            wait_s = args.steady_wait
        elif phase == "cold_candidate" and phase_index < phase_total:
            wait_s = args.cold_wait

        if wait_s > 0:
            print(f"  wachten: {wait_s}s", flush=True)
            time.sleep(wait_s)

    ok_count = sum(1 for item in summary["results"] if item["ok"])
    if args.dashboard_export_url:
        try:
            status_code, export_payload = fetch_dashboard_export(args.dashboard_export_url, args.batch_id)
            dashboard_output = Path(args.dashboard_export_output).expanduser().resolve()
            dashboard_output.parent.mkdir(parents=True, exist_ok=True)
            dashboard_output.write_text(json.dumps(export_payload, indent=2), encoding="utf-8")
            summary["dashboard_export"] = {
                "status_code": status_code,
                "output": str(dashboard_output),
                "measurement_count": export_payload.get("measurement_count"),
            }
            output_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
            print(
                f"Dashboard-export opgeslagen: {dashboard_output} "
                f"({export_payload.get('measurement_count')} metingen)",
                flush=True,
            )
        except Exception as exc:
            summary["dashboard_export"] = {"error": str(exc)}
            output_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
            print(f"Dashboard-export mislukt: {exc}", flush=True)

    print(
        f"Klaar. batch_id={args.batch_id} | "
        f"{ok_count}/{len(summary['results'])} runs succesvol. "
        f"Resultaatbestand: {output_path}",
        flush=True,
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Afgebroken door gebruiker.", file=sys.stderr)
        sys.exit(130)
    except Exception as exc:
        print(f"Fout: {exc}", file=sys.stderr)
        sys.exit(1)
