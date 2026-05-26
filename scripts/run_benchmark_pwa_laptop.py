import argparse
import json
import random
import re
import subprocess
import sys
import time
import threading
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib import request as urllib_request
from urllib.parse import urlencode


SCRIPT_DIR = Path(__file__).resolve().parent
RESULTS_ROOT = SCRIPT_DIR / "results"
PWA_JSON_DIR = RESULTS_ROOT / "pwa" / "json"
DEFAULT_OUTPUT = PWA_JSON_DIR / "benchmark_pwa_results.json"
DEFAULT_DASHBOARD_OUTPUT = PWA_JSON_DIR / "benchmark_pwa_dashboard_export.json"
GRAPHS_SCRIPT = SCRIPT_DIR.parent / "graphs" / "graphs_results.py"

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
except ImportError:  # pragma: no cover - runtime dependency
    sync_playwright = None
    PlaywrightTimeoutError = Exception


LHM_DEFAULT_URL = "http://localhost:8085/data.json"
CO2_INTENSITY_G_PER_KWH = 200.5

def log_event(message: str) -> None:
    timestamp = datetime.now().strftime("%H:%M:%S")
    line = f"[{timestamp}] {message}"
    print(line.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(sys.stdout.encoding or "utf-8", errors="replace"), flush=True)


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Geautomatiseerde benchmarkrunner voor de PWA-laptopvariant (Windows/AMD). "
            "Automatiseert browser, upload en opslagflow. De PWA-app zelf meet en bewaart "
            "de energiemetrics; dit script automatiseert enkel de handmatige UI-flow."
        )
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:5000",
        help="Basis-URL van de lokale PWA-app.",
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
        default=str(Path(__file__).resolve().parents[2] / "Dataset" / "PDF"),
        help="Map met PDF's. Alle .pdf bestanden worden alfabetisch toegevoegd.",
    )
    parser.add_argument(
        "--warmup-total",
        type=int,
        default=2,
        help="Totaal aantal warm-up runs over de volledige PDF-pool.",
    )
    parser.add_argument(
        "--steady-repeats",
        type=int,
        default=5,
        help="Aantal actieve meetruns per PDF.",
    )
    parser.add_argument(
        "--steady-chunk-size",
        type=int,
        default=15,
        help="Aantal steady runs per browserbatch vóór browser-reset en nieuw batch-id.",
    )
    parser.add_argument(
        "--batch-id",
        default=f"pwa-benchmark-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}",
        help="Unieke batch-id die via de URL wordt meegestuurd naar de PWA.",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="Pad voor het JSON resultaatbestand.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        default=True,
        help="Hervat automatisch op basis van een bestaand JSON-resultaatbestand.",
    )
    parser.add_argument(
        "--no-resume",
        dest="resume",
        action="store_false",
        help="Start altijd een nieuwe benchmark, zelfs als het outputbestand al bestaat.",
    )
    parser.add_argument(
        "--dashboard-export-url",
        help="Optionele MeasurementDashboard export-URL.",
    )
    parser.add_argument(
        "--dashboard-export-output",
        default=str(DEFAULT_DASHBOARD_OUTPUT),
        help="Bestand voor de dashboard-export als --dashboard-export-url gebruikt wordt.",
    )
    parser.add_argument(
        "--skip-graphs",
        action="store_true",
        help="Sla de automatische grafiekstap na de benchmark over.",
    )
    parser.add_argument(
        "--energy-mode",
        choices=["lhm", "prompt", "zero"],
        default="lhm",
        help=(
            "lhm    = gebruik de automatisch ingevulde energiewaarde uit de PWA-app. "
            "prompt = overschrijf de energiewaarde handmatig per run. "
            "zero   = sla op met 0 J (timing-only)."
        ),
    )
    parser.add_argument(
        "--lhm-url",
        default=LHM_DEFAULT_URL,
        help="URL van de LibreHardwareMonitor HTTP-server (standaard: %(default)s).",
    )
    parser.add_argument(
        "--lhm-baseline",
        action="store_true",
        default=True,
        help="Behoud CLI-compatibiliteit; baseline-correctie gebeurt niet meer in dit script.",
    )
    parser.add_argument(
        "--no-lhm-baseline",
        dest="lhm_baseline",
        action="store_false",
        help="Sla de idle-baselinemeting over.",
    )
    parser.add_argument(
        "--shuffle-seed",
        type=int,
        help="Optionele seed voor reproduceerbare aselecte volgorde.",
    )
    parser.add_argument(
        "--browser",
        choices=["firefox", "chromium"],
        default="chromium",
        help="Browserengine voor Playwright.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        default=False,
        help="Forceer headless uitvoeren (standaard uit).",
    )
    parser.add_argument(
        "--no-headless",
        dest="headless",
        action="store_false",
        help="Zichtbaar venster tonen (standaard).",
    )
    parser.add_argument(
        "--timeout-ms",
        type=int,
        default=1800000,
        help="Timeout per browseractie in milliseconden.",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="Aantal pogingen per warmup/steady run na browser- of PWA-fouten.",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# PDF helpers
# ---------------------------------------------------------------------------

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


def fetch_dashboard_export(dashboard_export_url: str, batch_id: str):
    params = urlencode({"download": "0", "date_filter": "TODAY", "batch_id": batch_id})
    separator = "&" if "?" in dashboard_export_url else "?"
    url = f"{dashboard_export_url}{separator}{params}"
    req = urllib_request.Request(url, method="GET", headers={"Accept": "application/json"})
    with urllib_request.urlopen(req, timeout=120) as response:
        raw = response.read().decode("utf-8")
        return response.status, json.loads(raw)


def build_plan(pdfs: list[Path], steady_repeats: int, seed: int | None):
    runs = []
    for repeat_index in range(1, steady_repeats + 1):
        for pdf_index, pdf_path in enumerate(pdfs, start=1):
            runs.append(
                {
                    "phase": "steady",
                    "repeat_index_for_pdf": repeat_index,
                    "pdf_order": pdf_index,
                    "pdf": pdf_path,
                }
            )
    rng = random.Random(seed)
    rng.shuffle(runs)
    for idx, item in enumerate(runs, start=1):
        item["run_number"] = idx
        item["phase_index"] = idx
        item["phase_total"] = len(runs)
    return runs


def chunk_runs(runs: list[dict], chunk_size: int) -> list[list[dict]]:
    if chunk_size <= 0:
        return [runs]
    return [runs[idx:idx + chunk_size] for idx in range(0, len(runs), chunk_size)]


def build_chunk_batch_id(parent_batch_id: str, chunk_index: int) -> str:
    return f"{parent_batch_id}-chunk-{chunk_index:02d}"


def build_warmup_plan(pdfs: list[Path], warmup_total: int, seed: int | None):
    if warmup_total <= 0:
        return []
    ordered_pdfs = list(pdfs)
    if seed is not None:
        random.Random(seed).shuffle(ordered_pdfs)
    runs = []
    for idx in range(warmup_total):
        pdf_path = ordered_pdfs[idx % len(ordered_pdfs)]
        pdf_index = pdfs.index(pdf_path) + 1
        runs.append(
            {
                "phase": "warmup",
                "repeat_index_for_pdf": (idx // len(ordered_pdfs)) + 1,
                "pdf_order": pdf_index,
                "pdf": pdf_path,
                "phase_index": idx + 1,
                "phase_total": warmup_total,
            }
        )
    return runs


def build_summary_template(args, pdfs: list[Path], warmup_plan: list[dict], plan: list[dict], output_path: Path):
    steady_chunks = chunk_runs(plan, args.steady_chunk_size)
    total_warmup_runs = len(warmup_plan) * len(steady_chunks)
    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "base_url": args.base_url,
        "architecture": "PWA",
        "batch_id": args.batch_id,
        "output_path": str(output_path),
        "pdfs": [str(pdf) for pdf in pdfs],
        "plan": {
            "pdf_count": len(pdfs),
            "warmup_total": args.warmup_total,
            "warmup_runs_per_chunk": len(warmup_plan),
            "warmup_runs_total": total_warmup_runs,
            "steady_repeats_per_pdf": args.steady_repeats,
            "steady_runs_total": len(plan),
            "steady_chunk_size": args.steady_chunk_size,
            "steady_chunk_count": len(steady_chunks),
            "shuffle_seed": args.shuffle_seed,
            "energy_mode": args.energy_mode,
            "browser": args.browser,
            "resume_enabled": args.resume,
            "max_retries": args.max_retries,
        },
        "energy_measurement": {
            "tool": "PWA-app",
            "sensor": "app-managed energy flow",
            "poll_interval_s": None,
            "baseline_w": None,
            "lhm_url": args.lhm_url if args.energy_mode == "lhm" else None,
        },
        "results": [],
    }


def load_existing_summary(output_path: Path):
    if not output_path.exists():
        return None
    try:
        return json.loads(output_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Bestaand outputbestand bevat ongeldige JSON: {output_path}") from exc


def ensure_resume_compatibility(existing_summary: dict, args, pdfs: list[Path], warmup_plan: list[dict], plan: list[dict], output_path: Path):
    expected_pdfs = [str(pdf) for pdf in pdfs]
    expected_batch_id = existing_summary.get("batch_id") or args.batch_id
    existing_plan = existing_summary.get("plan") or {}
    mismatches = []

    if existing_summary.get("architecture") != "PWA":
        mismatches.append("architecture")
    if existing_summary.get("pdfs") != expected_pdfs:
        mismatches.append("pdfs")
    if existing_plan.get("warmup_total") not in (None, args.warmup_total):
        mismatches.append("warmup_total")
    if existing_plan.get("steady_repeats_per_pdf") not in (None, args.steady_repeats):
        mismatches.append("steady_repeats_per_pdf")
    if existing_plan.get("steady_chunk_size") not in (None, args.steady_chunk_size):
        mismatches.append("steady_chunk_size")
    if existing_plan.get("shuffle_seed") != args.shuffle_seed:
        mismatches.append("shuffle_seed")
    if existing_plan.get("steady_runs_total") not in (None, len(plan)):
        mismatches.append("steady_runs_total")

    if mismatches:
        mismatch_str = ", ".join(mismatches)
        raise ValueError(
            f"Kan bestaande benchmark niet hervatten wegens mismatch in: {mismatch_str}. "
            f"Gebruik --no-resume of een ander --output bestand."
        )

    existing_summary["batch_id"] = expected_batch_id
    existing_summary["output_path"] = str(output_path)
    existing_summary["plan"]["warmup_runs_per_chunk"] = len(warmup_plan)
    existing_summary["plan"]["warmup_runs_total"] = len(warmup_plan) * len(chunk_runs(plan, args.steady_chunk_size))
    existing_summary["plan"]["steady_chunk_count"] = len(chunk_runs(plan, args.steady_chunk_size))
    existing_summary["plan"]["resume_enabled"] = args.resume
    existing_summary["plan"]["max_retries"] = args.max_retries
    existing_summary.setdefault("resume_history", []).append(
        {
            "resumed_at": datetime.now(timezone.utc).isoformat(),
            "existing_results": len(existing_summary.get("results") or []),
        }
    )
    return existing_summary


def write_summary(output_path: Path, summary: dict) -> None:
    output_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")


def chunk_steady_phase_indexes(runs_chunk: list[dict]) -> set[int]:
    return {run["phase_index"] for run in runs_chunk}


def get_chunk_results(summary: dict, chunk_index: int) -> list[dict]:
    return [item for item in summary["results"] if item.get("chunk_index") == chunk_index]


def get_chunk_resume_state(summary: dict, chunk_index: int, runs_chunk: list[dict], warmup_plan: list[dict]):
    chunk_results = get_chunk_results(summary, chunk_index)
    completed_warmups = sum(1 for item in chunk_results if item.get("phase") == "warmup")
    completed_steady_indexes = {
        item.get("phase_index")
        for item in chunk_results
        if item.get("phase") == "steady" and item.get("ok") and isinstance(item.get("phase_index"), int)
    }
    expected_steady_indexes = chunk_steady_phase_indexes(runs_chunk)
    chunk_complete = completed_steady_indexes >= expected_steady_indexes and completed_warmups >= len(warmup_plan)
    return {
        "completed_warmups": completed_warmups,
        "completed_steady_indexes": completed_steady_indexes,
        "chunk_complete": chunk_complete,
    }


def record_result(summary: dict, output_path: Path, result: dict) -> None:
    summary["results"].append(result)
    write_summary(output_path, summary)


def close_pwa_session_safe(context, browser) -> None:
    if context is None and browser is None:
        return
    try:
        if context is not None and browser is not None:
            close_pwa_session(context, browser)
    except Exception as exc:
        log_event(f"Fout bij sluiten van browsercontext: {exc}")


def append_failure_result(
    summary: dict,
    output_path: Path,
    run_number: int,
    phase: str,
    phase_index: int,
    phase_total: int,
    chunk_batch_id: str,
    chunk_index: int,
    repeat_index_for_pdf: int,
    pdf_order: int,
    pdf_path: Path,
    error_message: str,
):
    result = {
        "run_number": run_number,
        "phase": phase,
        "phase_index": phase_index,
        "phase_total": phase_total,
        "batch_id": chunk_batch_id,
        "chunk_index": chunk_index,
        "repeat_index_for_pdf": repeat_index_for_pdf,
        "pdf_order": pdf_order,
        "pdf": str(pdf_path),
        "requested_at": datetime.now(timezone.utc).isoformat(),
        "wall_time_s": None,
        "ok": False,
        "energy_joules_net": 0.0,
        "energy_joules_gross": 0.0,
        "energy_joules_baseline_correction": 0.0,
        "energy_source": "runner-error",
        "lhm_samples_count": 0,
        "lhm_mean_power_w": 0.0,
        "db_id": None,
        "pending_info": None,
        "sync_status_before": None,
        "sync_status_after": error_message,
        "json_output": None,
        "measurement_payload": None,
        "hardware_context": None,
        "model_size": None,
        "document_status": None,
        "response_time": None,
        "setup_time_s": None,
        "setup_energy_joules": None,
        "gpu_joules": None,
        "gpu_nvidia_joules": None,
        "gpu_amd_joules": None,
        "gpu_amd_core_joules": None,
        "gpu_amd_soc_joules": None,
        "cpu_joules": None,
        "dram_joules": None,
        "network_joules": None,
        "other_system_joules": None,
        "network_bytes_estimate": None,
        "gpu_avg_watts": None,
        "pue_factor": None,
        "carbon_intensity_gco2_kwh": None,
        "supplier": None,
        "start_date": None,
        "end_date": None,
        "kwh_quantity": None,
        "co2eq_quantity": None,
    }
    record_result(summary, output_path, result)


def prompt_energy(run_number: int, pdf_name: str) -> float:
    while True:
        raw = input(f"Run {run_number}: energie (J) voor {pdf_name} (handmatige invoer), Enter = 0: ").strip()
        if raw == "":
            return 0.0
        try:
            return float(raw.replace(",", "."))
        except ValueError:
            print("Ongeldige invoer. Gebruik een getal zoals 12.5", flush=True)


def extract_sync_result(sync_text: str):
    db_id = None
    if sync_text:
        match = re.search(r"ID:\s*(\d+)", sync_text)
        if match:
            db_id = int(match.group(1))
    ok = "Opgeslagen in DB" in (sync_text or "")
    return ok, db_id


def compute_co2eq_fallback(kwh_quantity):
    if kwh_quantity in (None, ""):
        return None
    try:
        return round(float(kwh_quantity) * CO2_INTENSITY_G_PER_KWH / 1000, 3)
    except (TypeError, ValueError):
        return None


def ensure_measurement_co2eq(measurement_snapshot: dict | None) -> dict | None:
    if not measurement_snapshot:
        return None
    measurement = dict(measurement_snapshot)
    if measurement.get("co2eq_quantity") in (None, ""):
        measurement["co2eq_quantity"] = compute_co2eq_fallback(measurement.get("kwh_quantity"))
    return measurement


def measurement_ok(measurement: dict | None) -> bool:
    if not measurement:
        return False
    required = ["supplier", "start_date", "end_date", "kwh_quantity"]
    for field in required:
        value = measurement.get(field)
        if value is None:
            return False
        if isinstance(value, str) and value.strip() == "":
            return False
    return True


def build_pwa_measurement_payload(
    measurement_snapshot: dict | None,
    energy_j: float,
    gross_energy_j: float,
    baseline_energy_j: float,
) -> dict | None:
    payload = ensure_measurement_co2eq(measurement_snapshot)
    if not payload:
        return None
    payload["energy_joules"] = energy_j
    payload["energy_joules_net"] = energy_j
    payload["energy_joules_gross"] = gross_energy_j
    payload["energy_joules_baseline_correction"] = baseline_energy_j
    return payload


def attach_page_logging(page) -> None:
    def on_console(msg):
        try:
            text = msg.text.strip()
            if text:
                log_event(f"[browser:{msg.type}] {text}")
        except Exception:
            pass

    def on_page_error(exc):
        try:
            log_event(f"[browser:error] {exc}")
        except Exception:
            pass

    def on_request_failed(req):
        try:
            failure = req.failure or ""
            log_event(f"[browser:requestfailed] {req.method} {req.url} | {failure}")
        except Exception:
            pass

    page.on("console", on_console)
    page.on("pageerror", on_page_error)
    page.on("requestfailed", on_request_failed)


def open_pwa_session(playwright_instance, args, batch_id: str):
    global _PERSISTENT_BROWSER, _PERSISTENT_CONTEXT
    app_url = args.base_url.rstrip("/") + "/?" + urlencode({"batch_id": batch_id})
    if _PERSISTENT_BROWSER is None or _PERSISTENT_CONTEXT is None:
        browser_factory = getattr(playwright_instance, args.browser)
        _PERSISTENT_BROWSER = browser_factory.launch(headless=args.headless)
        _PERSISTENT_CONTEXT = _PERSISTENT_BROWSER.new_context()
    page = _PERSISTENT_CONTEXT.new_page()
    page.set_default_timeout(args.timeout_ms)
    attach_page_logging(page)

    log_event(f"PWA openen op {app_url}")
    goto_started = time.monotonic()
    page.goto(app_url, wait_until="networkidle")
    log_event(f"PWA geladen na {time.monotonic() - goto_started:.1f}s")

    try:
        gpu_badge = page.locator("#gpuBadge").inner_text().strip()
        if gpu_badge:
            log_event(f"WebGPU status bij start: {gpu_badge}")
    except Exception:
        pass

    return _PERSISTENT_BROWSER, _PERSISTENT_CONTEXT, page


def close_pwa_session(context, browser) -> None:
    # Browser en context blijven leven over chunks heen zodat het model in VRAM
    # blijft en Service Worker / IndexedDB / Cache niet opnieuw moeten laden.
    # Alleen de pages worden gesloten zodat heap, listeners en resource-timings
    # tussen chunks vrijkomen.
    if context is None:
        return
    for pg in list(context.pages):
        try:
            pg.close()
        except Exception:
            pass


def shutdown_persistent_browser() -> None:
    global _PERSISTENT_BROWSER, _PERSISTENT_CONTEXT
    if _PERSISTENT_CONTEXT is not None:
        try:
            _PERSISTENT_CONTEXT.close()
        except Exception:
            pass
    if _PERSISTENT_BROWSER is not None:
        try:
            _PERSISTENT_BROWSER.close()
        except Exception:
            pass
    _PERSISTENT_BROWSER = None
    _PERSISTENT_CONTEXT = None


def wait_with_status_logging(page, wait_target, description: str, status_selector: str = "#status", poll_interval_s: float = 2.0):
    start = time.monotonic()
    last_status = None
    last_elapsed_bucket = -1

    while True:
        try:
            wait_target.wait_for(state="hidden", timeout=int(poll_interval_s * 1000))
            elapsed = time.monotonic() - start
            status_text = page.locator(status_selector).inner_text().strip()
            if status_text and status_text != last_status:
                log_event(f"{description}: {status_text}")
            log_event(f"{description}: klaar na {elapsed:.1f}s")
            return elapsed
        except PlaywrightTimeoutError:
            elapsed = time.monotonic() - start
            try:
                status_text = page.locator(status_selector).inner_text().strip()
            except Exception:
                status_text = ""
            elapsed_bucket = int(elapsed // poll_interval_s)
            if status_text and status_text != last_status:
                log_event(f"{description}: {status_text}")
                last_status = status_text
            elif elapsed_bucket != last_elapsed_bucket:
                log_event(f"{description}: bezig... {elapsed:.1f}s")
                last_elapsed_bucket = elapsed_bucket


def run_pwa_warmup(page, pdf_path: Path, warmup_number: int, warmup_total: int, timeout_ms: int):
    token = f"warmup-{warmup_number}-{uuid.uuid4().hex[:8]}"
    page.set_input_files("#pdfFile", str(pdf_path))
    page.evaluate("(meta) => window.__setBenchmarkMode('warmup', meta)", {"token": token})
    started = time.time()
    page.locator("#submitBtn").click()
    page.wait_for_function(
        "(expectedToken) => window.__lastCompletedToken === expectedToken",
        arg=token,
        timeout=timeout_ms,
    )
    wall_time_s = time.time() - started
    measurement = ensure_measurement_co2eq(page.evaluate("() => window.__lastMeasurement"))
    ok = measurement_ok(measurement)
    log_event(
        f"Warm-up {warmup_number}/{warmup_total} voltooid | pdf={pdf_path.name} | "
        f"wall_time={wall_time_s:.2f}s | ok={ok}"
    )
    return {
        "wall_time_s": wall_time_s,
        "measurement": measurement,
        "ok": ok,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    args = parse_args()

    if sync_playwright is None:
        raise SystemExit(
            "playwright is niet geïnstalleerd. Installeer eerst: "
            "pip install playwright && playwright install chromium"
        )

    pdfs = collect_pdfs(args)
    warmup_plan = build_warmup_plan(pdfs, args.warmup_total, args.shuffle_seed)
    plan = build_plan(pdfs, args.steady_repeats, args.shuffle_seed)

    output_path = Path(args.output).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if args.max_retries < 1:
        raise ValueError("--max-retries moet minstens 1 zijn.")

    existing_summary = load_existing_summary(output_path) if args.resume else None
    if existing_summary:
        summary = ensure_resume_compatibility(existing_summary, args, pdfs, warmup_plan, plan, output_path)
        args.batch_id = summary["batch_id"]
        log_event(
            f"Resume geactiveerd: {len(summary['results'])} bestaande resultaten geladen uit {output_path.name} "
            f"(batch_id={args.batch_id})"
        )
        write_summary(output_path, summary)
    else:
        summary = build_summary_template(args, pdfs, warmup_plan, plan, output_path)
        write_summary(output_path, summary)

    steady_chunks = chunk_runs(plan, args.steady_chunk_size)
    chunk_batch_ids: list[str] = []
    warmup_per_chunk_count = len(warmup_plan)
    total_warmup_runs = warmup_per_chunk_count * len(steady_chunks)
    grand_total = total_warmup_runs + len(plan)
    global_run_counter = max((item.get("run_number", 0) for item in summary["results"]), default=0)

    with sync_playwright() as p:
        try:
            for chunk_index, runs_chunk in enumerate(steady_chunks, start=1):
                chunk_batch_id = build_chunk_batch_id(args.batch_id, chunk_index)
                chunk_batch_ids.append(chunk_batch_id)
                resume_state = get_chunk_resume_state(summary, chunk_index, runs_chunk, warmup_plan)
                if resume_state["chunk_complete"]:
                    log_event(
                        f"Chunk {chunk_index}/{len(steady_chunks)} overslaan: al volledig aanwezig in {output_path.name}."
                    )
                    continue

                browser = context = page = None
                try:
                    log_event(
                        f"Chunk {chunk_index}/{len(steady_chunks)} starten "
                        f"({warmup_per_chunk_count} warm-ups + {len(runs_chunk)} steady runs, "
                        f"batch_id={chunk_batch_id})..."
                    )
                    browser, context, page = open_pwa_session(p, args, chunk_batch_id)

                    for warmup_idx, warmup_run in enumerate(warmup_plan, start=1):
                        if warmup_idx <= resume_state["completed_warmups"]:
                            continue

                        global_run_counter += 1
                        pdf_path = warmup_run["pdf"]
                        attempt = 0
                        warmup_result = None
                        warmup_succeeded = False
                        while attempt < args.max_retries:
                            attempt += 1
                            try:
                                if page is None:
                                    browser, context, page = open_pwa_session(p, args, chunk_batch_id)
                                log_event(
                                    f"[{global_run_counter}/{grand_total}] warmup "
                                    f"(chunk {chunk_index}/{len(steady_chunks)}, {warmup_idx}/{warmup_per_chunk_count}, "
                                    f"poging {attempt}/{args.max_retries}) -> {pdf_path.name}"
                                )
                                warmup_result = run_pwa_warmup(page, pdf_path, warmup_idx, warmup_per_chunk_count, args.timeout_ms)
                                warmup_succeeded = True
                                break
                            except Exception as exc:
                                error_message = f"Warm-up {warmup_idx} fout op poging {attempt}/{args.max_retries}: {exc}"
                                log_event(error_message)
                                log_event(traceback.format_exc())
                                close_pwa_session_safe(context, browser)
                                browser = context = page = None
                                if attempt >= args.max_retries:
                                    append_failure_result(
                                        summary,
                                        output_path,
                                        global_run_counter,
                                        "warmup",
                                        warmup_idx,
                                        warmup_per_chunk_count,
                                        chunk_batch_id,
                                        chunk_index,
                                        warmup_run["repeat_index_for_pdf"],
                                        warmup_run["pdf_order"],
                                        pdf_path,
                                        error_message,
                                    )
                                    log_event(f"Warm-up {warmup_idx} definitief mislukt; benchmark gaat verder.")

                        if not warmup_succeeded:
                            continue

                        result = {
                            "run_number": global_run_counter,
                            "phase": "warmup",
                            "phase_index": warmup_idx,
                            "phase_total": warmup_per_chunk_count,
                            "batch_id": chunk_batch_id,
                            "chunk_index": chunk_index,
                            "repeat_index_for_pdf": warmup_run["repeat_index_for_pdf"],
                            "pdf_order": warmup_run["pdf_order"],
                            "pdf": str(pdf_path),
                            "requested_at": datetime.now(timezone.utc).isoformat(),
                            "wall_time_s": warmup_result["wall_time_s"],
                            "ok": warmup_result["ok"],
                            "energy_joules_net": 0.0,
                            "energy_joules_gross": 0.0,
                            "energy_joules_baseline_correction": 0.0,
                            "energy_source": "warmup-no-db",
                            "lhm_samples_count": 0,
                            "lhm_mean_power_w": 0.0,
                            "db_id": None,
                            "pending_info": None,
                            "sync_status_before": None,
                            "sync_status_after": "Warm-up run voltooid (niet opgeslagen in DB)",
                            "json_output": json.dumps(warmup_result["measurement"], indent=2) if warmup_result["measurement"] else None,
                            "measurement_payload": warmup_result["measurement"],
                            "hardware_context": (warmup_result["measurement"] or {}).get("hardware_context") if warmup_result["measurement"] else None,
                            "model_size": (warmup_result["measurement"] or {}).get("model_size") if warmup_result["measurement"] else None,
                            "document_status": (warmup_result["measurement"] or {}).get("document_status") if warmup_result["measurement"] else None,
                            "response_time": (warmup_result["measurement"] or {}).get("response_time") if warmup_result["measurement"] else None,
                            "setup_time_s": (warmup_result["measurement"] or {}).get("setup_time_s") if warmup_result["measurement"] else None,
                            "setup_energy_joules": (warmup_result["measurement"] or {}).get("setup_energy_joules") if warmup_result["measurement"] else None,
                            "gpu_joules": (warmup_result["measurement"] or {}).get("gpu_joules") if warmup_result["measurement"] else None,
                            "gpu_nvidia_joules": (warmup_result["measurement"] or {}).get("gpu_nvidia_joules") if warmup_result["measurement"] else None,
                            "gpu_amd_joules": (warmup_result["measurement"] or {}).get("gpu_amd_joules") if warmup_result["measurement"] else None,
                            "gpu_amd_core_joules": (warmup_result["measurement"] or {}).get("gpu_amd_core_joules") if warmup_result["measurement"] else None,
                            "gpu_amd_soc_joules": (warmup_result["measurement"] or {}).get("gpu_amd_soc_joules") if warmup_result["measurement"] else None,
                            "cpu_joules": (warmup_result["measurement"] or {}).get("cpu_joules") if warmup_result["measurement"] else None,
                            "dram_joules": (warmup_result["measurement"] or {}).get("dram_joules") if warmup_result["measurement"] else None,
                            "network_joules": (warmup_result["measurement"] or {}).get("network_joules") if warmup_result["measurement"] else None,
                            "gpu_avg_watts": (warmup_result["measurement"] or {}).get("gpu_avg_watts") if warmup_result["measurement"] else None,
                            "pue_factor": (warmup_result["measurement"] or {}).get("pue_factor") if warmup_result["measurement"] else None,
                            "carbon_intensity_gco2_kwh": (warmup_result["measurement"] or {}).get("carbon_intensity_gco2_kwh") if warmup_result["measurement"] else None,
                            "supplier": (warmup_result["measurement"] or {}).get("supplier") if warmup_result["measurement"] else None,
                            "start_date": (warmup_result["measurement"] or {}).get("start_date") if warmup_result["measurement"] else None,
                            "end_date": (warmup_result["measurement"] or {}).get("end_date") if warmup_result["measurement"] else None,
                            "kwh_quantity": (warmup_result["measurement"] or {}).get("kwh_quantity") if warmup_result["measurement"] else None,
                            "co2eq_quantity": (warmup_result["measurement"] or {}).get("co2eq_quantity") if warmup_result["measurement"] else None,
                        }
                        record_result(summary, output_path, result)

                    log_event(f"Chunk {chunk_index} warm-ups voltooid; steady runs starten...")

                    for run in runs_chunk:
                        if run["phase_index"] in resume_state["completed_steady_indexes"]:
                            continue

                        global_run_counter += 1
                        pdf_path = run["pdf"]
                        run_number = global_run_counter
                        attempt = 0
                        baseline_energy_j = 0.0
                        samples_count = 0
                        mean_power_w = 0.0
                        pending_info = None
                        json_output = None
                        sync_text_before = None
                        sync_text = None
                        measurement_snapshot = None
                        measurement_payload = None
                        db_id = None
                        ok = False
                        energy_j = 0.0
                        gross_energy_j = 0.0
                        wall_time_s = None
                        run_succeeded = False

                        while attempt < args.max_retries:
                            attempt += 1
                            try:
                                if page is None:
                                    browser, context, page = open_pwa_session(p, args, chunk_batch_id)
                                log_event(
                                    f"[{run_number}/{grand_total}] steady "
                                    f"(chunk {chunk_index}/{len(steady_chunks)}, repeat {run['repeat_index_for_pdf']}, "
                                    f"pdf {run['pdf_order']}/{len(pdfs)}, poging {attempt}/{args.max_retries}) "
                                    f"-> {pdf_path.name}"
                                )

                                if page.evaluate("() => !!window.__gpuDeviceLost"):
                                    log_event(
                                        f"WebGPU device lost vlag aanwezig vóór run {run_number} — "
                                        f"vlag resetten (model herlaadt automatisch bij volgende run)."
                                    )
                                    page.evaluate("() => { window.__gpuDeviceLost = false; }")

                                page.set_input_files("#pdfFile", str(pdf_path))
                                steady_token = f"steady-{run['phase_index']}-{uuid.uuid4().hex[:8]}"
                                page.evaluate("(meta) => window.__setBenchmarkMode('steady', meta)", {"token": steady_token})

                                started = time.time()
                                page.locator("#submitBtn").click()
                                energy_panel = page.locator("#energyPanel")
                                energy_panel_appeared = False
                                try:
                                    energy_panel.wait_for(state="visible", timeout=900_000)
                                    energy_panel_appeared = True
                                except PlaywrightTimeoutError:
                                    log_event(
                                        f"Run {run_number}: energie-paneel verscheen niet binnen 900s — "
                                        f"wachten op completion token."
                                    )
                                wall_time_s = time.time() - started

                                pending_info = page.locator("#pendingInfo").inner_text()
                                json_output = page.locator("#jsonOutput").inner_text()
                                sync_text_before = page.locator("#syncStatus").inner_text()

                                if energy_panel_appeared:
                                    if args.energy_mode == "zero":
                                        page.locator("#energyInput").fill("0")
                                    elif args.energy_mode == "prompt":
                                        energy_j = prompt_energy(run_number, pdf_path.name)
                                        page.locator("#energyInput").fill(str(energy_j))
                                    page.locator("#saveBtn").click()
                                    energy_panel.wait_for(state="hidden")
                                token_timeout = args.timeout_ms if energy_panel_appeared else 300_000
                                page.wait_for_function(
                                    "(expectedToken) => window.__lastCompletedToken === expectedToken",
                                    arg=steady_token,
                                    timeout=token_timeout,
                                )

                                sync_text = page.locator("#syncStatus").inner_text()
                                measurement_snapshot = ensure_measurement_co2eq(page.evaluate("() => window.__lastMeasurement"))
                                energy_j = (measurement_snapshot or {}).get("energy_joules", 0.0) if measurement_snapshot else 0.0
                                gross_energy_j = energy_j
                                ok, db_id = extract_sync_result(sync_text)
                                measurement_payload = build_pwa_measurement_payload(
                                    measurement_snapshot,
                                    energy_j,
                                    gross_energy_j,
                                    baseline_energy_j,
                                )
                                log_event(
                                    f"Run {run_number} opgeslagen | ok={ok} | db_id={db_id} | "
                                    f"wall_time={wall_time_s:.2f}s | energy={energy_j:.2f}J"
                                )
                                run_succeeded = True
                                break
                            except Exception as exc:
                                error_message = (
                                    f"Steady run phase_index={run['phase_index']} fout op poging "
                                    f"{attempt}/{args.max_retries}: {exc}"
                                )
                                log_event(error_message)
                                log_event(traceback.format_exc())
                                close_pwa_session_safe(context, browser)
                                browser = context = page = None
                                if attempt >= args.max_retries:
                                    append_failure_result(
                                        summary,
                                        output_path,
                                        run_number,
                                        "steady",
                                        run["phase_index"],
                                        run["phase_total"],
                                        chunk_batch_id,
                                        chunk_index,
                                        run["repeat_index_for_pdf"],
                                        run["pdf_order"],
                                        pdf_path,
                                        error_message,
                                    )
                                    log_event(
                                        f"Steady run phase_index={run['phase_index']} definitief mislukt; "
                                        f"benchmark gaat verder met de volgende run."
                                    )

                        if not run_succeeded:
                            continue

                        result = {
                            "run_number": run_number,
                            "phase": "steady",
                            "phase_index": run["phase_index"],
                            "phase_total": run["phase_total"],
                            "batch_id": chunk_batch_id,
                            "chunk_index": chunk_index,
                            "repeat_index_for_pdf": run["repeat_index_for_pdf"],
                            "pdf_order": run["pdf_order"],
                            "pdf": str(pdf_path),
                            "requested_at": datetime.now(timezone.utc).isoformat(),
                            "wall_time_s": wall_time_s,
                            "ok": ok,
                            "energy_joules_net": energy_j,
                            "energy_joules_gross": gross_energy_j,
                            "energy_joules_baseline_correction": baseline_energy_j,
                            "energy_source": (measurement_snapshot or {}).get("energy_source") if measurement_snapshot else args.energy_mode,
                            "lhm_samples_count": samples_count,
                            "lhm_mean_power_w": mean_power_w,
                            "db_id": db_id,
                            "pending_info": pending_info,
                            "sync_status_before": sync_text_before,
                            "sync_status_after": sync_text,
                            "json_output": json_output,
                            "measurement_payload": measurement_payload,
                            "hardware_context": (measurement_snapshot or {}).get("hardware_context") if measurement_snapshot else None,
                            "model_size": (measurement_snapshot or {}).get("model_size") if measurement_snapshot else None,
                            "document_status": (measurement_snapshot or {}).get("document_status") if measurement_snapshot else None,
                            "response_time": (measurement_snapshot or {}).get("response_time") if measurement_snapshot else None,
                            "setup_time_s": (measurement_snapshot or {}).get("setup_time_s") if measurement_snapshot else None,
                            "setup_energy_joules": (measurement_snapshot or {}).get("setup_energy_joules") if measurement_snapshot else None,
                            "gpu_joules": (measurement_snapshot or {}).get("gpu_joules") if measurement_snapshot else None,
                            "gpu_nvidia_joules": (measurement_snapshot or {}).get("gpu_nvidia_joules") if measurement_snapshot else None,
                            "gpu_amd_joules": (measurement_snapshot or {}).get("gpu_amd_joules") if measurement_snapshot else None,
                            "gpu_amd_core_joules": (measurement_snapshot or {}).get("gpu_amd_core_joules") if measurement_snapshot else None,
                            "gpu_amd_soc_joules": (measurement_snapshot or {}).get("gpu_amd_soc_joules") if measurement_snapshot else None,
                            "cpu_joules": (measurement_snapshot or {}).get("cpu_joules") if measurement_snapshot else None,
                            "dram_joules": (measurement_snapshot or {}).get("dram_joules") if measurement_snapshot else None,
                            "network_joules": (measurement_snapshot or {}).get("network_joules") if measurement_snapshot else None,
                            "other_system_joules": (measurement_snapshot or {}).get("other_system_joules") if measurement_snapshot else None,
                            "network_bytes_estimate": (measurement_snapshot or {}).get("network_bytes_estimate") if measurement_snapshot else None,
                            "gpu_avg_watts": (measurement_snapshot or {}).get("gpu_avg_watts") if measurement_snapshot else None,
                            "pue_factor": (measurement_snapshot or {}).get("pue_factor") if measurement_snapshot else None,
                            "carbon_intensity_gco2_kwh": (measurement_snapshot or {}).get("carbon_intensity_gco2_kwh") if measurement_snapshot else None,
                            "supplier": (measurement_snapshot or {}).get("supplier") if measurement_snapshot else None,
                            "start_date": (measurement_snapshot or {}).get("start_date") if measurement_snapshot else None,
                            "end_date": (measurement_snapshot or {}).get("end_date") if measurement_snapshot else None,
                            "kwh_quantity": (measurement_snapshot or {}).get("kwh_quantity") if measurement_snapshot else None,
                            "co2eq_quantity": (measurement_snapshot or {}).get("co2eq_quantity") if measurement_snapshot else None,
                        }
                        record_result(summary, output_path, result)
                finally:
                    close_pwa_session_safe(context, browser)
                    log_event(
                        f"Steady chunk {chunk_index}/{len(steady_chunks)} voltooid; "
                        f"pages gesloten (browser blijft open voor volgende chunk)."
                    )
        finally:
            shutdown_persistent_browser()
            log_event("Persistent browser gesloten na alle chunks.")

    if args.dashboard_export_url:
        try:
            dashboard_output = Path(args.dashboard_export_output).expanduser().resolve()
            dashboard_output.parent.mkdir(parents=True, exist_ok=True)
            export_entries = []
            measurement_count_total = 0
            for chunk_batch_id in chunk_batch_ids:
                status_code, export_payload = fetch_dashboard_export(args.dashboard_export_url, chunk_batch_id)
                measurement_count = export_payload.get("measurement_count") or 0
                measurement_count_total += measurement_count
                export_entries.append(
                    {
                        "batch_id": chunk_batch_id,
                        "status_code": status_code,
                        "measurement_count": measurement_count,
                        "payload": export_payload,
                    }
                )
            dashboard_output.write_text(
                json.dumps(
                    {
                        "parent_batch_id": args.batch_id,
                        "chunk_batch_ids": chunk_batch_ids,
                        "exports": export_entries,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            summary["dashboard_export"] = {
                "status_codes": [entry["status_code"] for entry in export_entries],
                "output": str(dashboard_output),
                "measurement_count_total": measurement_count_total,
                "chunk_count": len(export_entries),
            }
            output_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
            print(
                f"Dashboard-export opgeslagen: {dashboard_output} "
                f"({measurement_count_total} metingen over {len(export_entries)} chunks)",
                flush=True,
            )
        except Exception as exc:
            summary["dashboard_export"] = {"error": str(exc)}
            output_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
            print(f"Dashboard-export mislukt: {exc}", flush=True)

    ok_count = sum(1 for item in summary["results"] if item["ok"])
    print(
        f"Klaar. batch_id={args.batch_id} | "
        f"{ok_count}/{len(summary['results'])} runs succesvol. "
        f"Resultaatbestand: {output_path}",
        flush=True,
    )

    if not args.skip_graphs:
        if not output_path.exists():
            print(f"Resultaatbestand niet gevonden, grafieken overgeslagen: {output_path}", flush=True)
        elif not GRAPHS_SCRIPT.exists():
            print(f"graphs_results.py niet gevonden op {GRAPHS_SCRIPT}, grafieken overgeslagen.", flush=True)
        else:
            graphs_cmd = [
                sys.executable,
                str(GRAPHS_SCRIPT),
                "--pwa",
                str(output_path),
                "--ground-truth",
                str(RESULTS_ROOT / "ground_truth_expected_fields.json"),
                "--output-dir",
                str(RESULTS_ROOT),
            ]
            print("Grafieken genereren...", flush=True)
            subprocess.run(graphs_cmd)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Afgebroken door gebruiker.", file=sys.stderr)
        sys.exit(130)
