import argparse
import json
import random
import re
import sys
import time
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib import request as urllib_request
from urllib.parse import urlencode

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
except ImportError:  # pragma: no cover - runtime dependency
    sync_playwright = None
    PlaywrightTimeoutError = Exception


LHM_DEFAULT_URL = "http://localhost:8085/data.json"
LHM_POLL_INTERVAL_S = 0.5
LHM_BASELINE_DURATION_S = 30


def log_event(message: str) -> None:
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {message}", flush=True)


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Geautomatiseerde benchmarkrunner voor de PWA-laptopvariant (Windows/AMD). "
            "Automatiseert browser, upload en opslagflow. Energiemeting via "
            "LibreHardwareMonitor (lhm), handmatige invoer (prompt) of nul (zero)."
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
        default=str(Path(__file__).resolve().parents[2] / "Dataset"),
        help="Map met PDF's. Alle .pdf bestanden worden alfabetisch toegevoegd.",
    )
    parser.add_argument(
        "--steady-repeats",
        type=int,
        default=8,
        help="Aantal actieve meetruns per PDF (standaard 8, geeft 32 runs voor 4 PDF's).",
    )
    parser.add_argument(
        "--batch-id",
        default=f"pwa-benchmark-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}",
        help="Unieke batch-id die via de URL wordt meegestuurd naar de PWA.",
    )
    parser.add_argument(
        "--output",
        default="benchmark_pwa_laptop_results.json",
        help="Pad voor het JSON resultaatbestand.",
    )
    parser.add_argument(
        "--dashboard-export-url",
        help="Optionele MeasurementDashboard export-URL.",
    )
    parser.add_argument(
        "--dashboard-export-output",
        default="benchmark_pwa_laptop_dashboard_export.json",
        help="Bestand voor de dashboard-export als --dashboard-export-url gebruikt wordt.",
    )
    parser.add_argument(
        "--energy-mode",
        choices=["lhm", "prompt", "zero"],
        default="lhm",
        help=(
            "lhm    = automatisch via LibreHardwareMonitor HTTP-server (aanbevolen, AMD/Intel). "
            "prompt = handmatige invoer per run. "
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
        help=f"Meet {LHM_BASELINE_DURATION_S}s idle-baseline vóór de meetreeks en trek die af per run.",
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
        default=True,
        help="Headless uitvoeren (standaard aan).",
    )
    parser.add_argument(
        "--no-headless",
        dest="headless",
        action="store_false",
        help="Zichtbaar venster tonen.",
    )
    parser.add_argument(
        "--timeout-ms",
        type=int,
        default=600000,
        help="Timeout per browseractie in milliseconden.",
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


def build_warmup_plan(pdfs: list[Path]):
    runs = []
    for pdf_index, pdf_path in enumerate(pdfs, start=1):
        runs.append(
            {
                "phase": "warmup",
                "repeat_index_for_pdf": 1,
                "pdf_order": pdf_index,
                "pdf": pdf_path,
                "phase_index": pdf_index,
                "phase_total": len(pdfs),
            }
        )
    return runs


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


def measurement_ok(measurement: dict | None) -> bool:
    if not measurement:
        return False
    required = ["supplier", "start_date", "end_date", "kwh_quantity", "co2eq_quantity"]
    for field in required:
        value = measurement.get(field)
        if value is None:
            return False
        if isinstance(value, str) and value.strip() == "":
            return False
    return True


def attach_page_logging(page) -> None:
    def on_console(msg):
        text = msg.text.strip()
        if text:
            log_event(f"[browser:{msg.type}] {text}")

    def on_page_error(exc):
        log_event(f"[browser:error] {exc}")

    def on_request_failed(req):
        failure = req.failure or ""
        log_event(f"[browser:requestfailed] {req.method} {req.url} | {failure}")

    page.on("console", on_console)
    page.on("pageerror", on_page_error)
    page.on("requestfailed", on_request_failed)


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
    measurement = page.evaluate("() => window.__lastMeasurement")
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
# LibreHardwareMonitor energy meter
# ---------------------------------------------------------------------------

def _fetch_lhm_cpu_power_w(lhm_url: str) -> float | None:
    """Haal het totale CPU Package Power (W) op uit de LHM JSON-boom.

    LibreHardwareMonitor levert een geneste boom van hardware-nodes. We zoeken
    naar het eerste sensortype 'Power' met naam die 'CPU Package' bevat.
    Geeft None terug als de sensor niet gevonden wordt.
    """
    try:
        req = urllib_request.Request(lhm_url, headers={"Accept": "application/json"})
        with urllib_request.urlopen(req, timeout=2) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None

    def _search(node) -> float | None:
        # LHM JSON heeft Children-lijsten op elk niveau
        # Sommige LHM-versies leveren lege SensorType; match op naam + waarde eindigt op " W"
        name = node.get("Text", "")
        val_str = node.get("Value", "")
        # Match "CPU Package", "Package" (AMD), of parent "Powers" met kind "Package"
        is_cpu_power = (
            ("CPU Package" in name or name == "Package")
            and "GPU" not in name
            and val_str.endswith(" W")
        )
        if is_cpu_power:
            try:
                return float(val_str.replace(",", ".").split()[0])
            except (ValueError, AttributeError):
                return None
        for child in node.get("Children", []):
            result = _search(child)
            if result is not None:
                return result
        return None

    return _search(data)


class LhmPowerSampler:
    """Achtergrondthread die LHM elke LHM_POLL_INTERVAL_S peilt.

    Gebruik:
        sampler = LhmPowerSampler(lhm_url)
        sampler.start()
        # ... inferentie ...
        joules = sampler.stop()   # integreert watts×tijd → joules
    """

    def __init__(self, lhm_url: str):
        self._url = lhm_url
        self._samples: list[tuple[float, float]] = []  # (timestamp, watts)
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self):
        self._samples = []
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        while self._running:
            t = time.monotonic()
            w = _fetch_lhm_cpu_power_w(self._url)
            if w is not None:
                self._samples.append((t, w))
            time.sleep(LHM_POLL_INTERVAL_S)

    def stop(self) -> float:
        """Stop meting en geef energie in joules terug via trapeziumregel."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
        if len(self._samples) < 2:
            # Onvoldoende datapunten: gebruik gemiddelde × geschatte tijd
            if self._samples:
                return self._samples[0][1] * LHM_POLL_INTERVAL_S
            return 0.0
        energy = 0.0
        for i in range(1, len(self._samples)):
            dt = self._samples[i][0] - self._samples[i - 1][0]
            avg_w = (self._samples[i][1] + self._samples[i - 1][1]) / 2.0
            energy += avg_w * dt
        return energy

    def mean_power_w(self) -> float:
        if not self._samples:
            return 0.0
        return sum(w for _, w in self._samples) / len(self._samples)


def measure_lhm_baseline(lhm_url: str, duration_s: int = LHM_BASELINE_DURATION_S) -> float:
    """Meet het gemiddelde idle CPU Package Power over duration_s seconden.

    Conform meetprotocol sectie 3.2: baseline-metingen tijdens idle worden
    afgetrokken van actieve verbruiksdata.
    """
    print(
        f"Idle-baselinemeting starten ({duration_s}s) — zorg dat de browser gesloten is "
        f"en er geen andere zware processen draaien...",
        flush=True,
    )
    sampler = LhmPowerSampler(lhm_url)
    sampler.start()
    time.sleep(duration_s)
    sampler.stop()
    baseline_w = sampler.mean_power_w()
    print(f"Idle-baseline gemeten: {baseline_w:.2f} W", flush=True)
    return baseline_w


def verify_lhm(lhm_url: str) -> None:
    """Controleer of LHM bereikbaar is en CPU Package Power blootstelt."""
    w = _fetch_lhm_cpu_power_w(lhm_url)
    if w is None:
        raise SystemExit(
            f"LibreHardwareMonitor niet bereikbaar op {lhm_url} of "
            "'CPU Package' Power-sensor niet gevonden.\n"
            "Controleer dat LibreHardwareMonitor draait met de ingebouwde webserver "
            "(Options → Remote Web Server → Run) op poort 8085, "
            "en dat 'CPU Package' zichtbaar is in de sensorlijst.\n"
            "Gebruik --energy-mode zero of --energy-mode prompt als alternatief."
        )
    print(f"LibreHardwareMonitor bereikbaar. Huidig CPU Package Power: {w:.1f} W", flush=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if sync_playwright is None:
        raise SystemExit(
            "playwright is niet geïnstalleerd. Installeer eerst: "
            "pip install playwright && playwright install chromium"
        )

    args = parse_args()

    baseline_w = 0.0
    if args.energy_mode == "lhm":
        verify_lhm(args.lhm_url)
        if args.lhm_baseline:
            baseline_w = measure_lhm_baseline(args.lhm_url)

    pdfs = collect_pdfs(args)
    warmup_plan = build_warmup_plan(pdfs)
    plan = build_plan(pdfs, args.steady_repeats, args.shuffle_seed)

    output_path = Path(args.output).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    summary = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "base_url": args.base_url,
        "architecture": "PWA",
        "batch_id": args.batch_id,
        "pdfs": [str(pdf) for pdf in pdfs],
        "plan": {
            "pdf_count": len(pdfs),
            "warmup_runs_total": len(warmup_plan),
            "steady_repeats_per_pdf": args.steady_repeats,
            "steady_runs_total": len(plan),
            "shuffle_seed": args.shuffle_seed,
            "energy_mode": args.energy_mode,
            "browser": args.browser,
        },
        "energy_measurement": {
            "tool": "LibreHardwareMonitor" if args.energy_mode == "lhm" else args.energy_mode,
            "sensor": "CPU Package Power (W)" if args.energy_mode == "lhm" else "n/a",
            "poll_interval_s": LHM_POLL_INTERVAL_S if args.energy_mode == "lhm" else None,
            "baseline_w": baseline_w,
            "lhm_url": args.lhm_url if args.energy_mode == "lhm" else None,
        },
        "results": [],
    }

    app_url = args.base_url.rstrip("/") + "/?" + urlencode({"batch_id": args.batch_id})

    with sync_playwright() as p:
        browser_factory = getattr(p, args.browser)
        browser = browser_factory.launch(headless=args.headless)
        page = browser.new_page()
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

        log_event(f"Protocol warm-up starten ({len(warmup_plan)} runs, elke PDF één keer)...")
        for idx, warmup_run in enumerate(warmup_plan, start=1):
            pdf_path = warmup_run["pdf"]
            log_event(f"Warm-up {idx}/{len(warmup_plan)} -> {pdf_path.name}")
            warmup_result = run_pwa_warmup(page, pdf_path, idx, len(warmup_plan), args.timeout_ms)
            result = {
                "run_number": idx,
                "phase": "warmup",
                "phase_index": warmup_run["phase_index"],
                "phase_total": warmup_run["phase_total"],
                "batch_id": args.batch_id,
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
                "document_status": (warmup_result["measurement"] or {}).get("document_status") if warmup_result["measurement"] else None,
                "response_time": (warmup_result["measurement"] or {}).get("response_time") if warmup_result["measurement"] else None,
                "setup_time_s": (warmup_result["measurement"] or {}).get("setup_time_s") if warmup_result["measurement"] else None,
                "supplier": (warmup_result["measurement"] or {}).get("supplier") if warmup_result["measurement"] else None,
                "start_date": (warmup_result["measurement"] or {}).get("start_date") if warmup_result["measurement"] else None,
                "end_date": (warmup_result["measurement"] or {}).get("end_date") if warmup_result["measurement"] else None,
                "kwh_quantity": (warmup_result["measurement"] or {}).get("kwh_quantity") if warmup_result["measurement"] else None,
                "co2eq_quantity": (warmup_result["measurement"] or {}).get("co2eq_quantity") if warmup_result["measurement"] else None,
            }
            summary["results"].append(result)
            output_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

        log_event("Warm-up voltooid.")

        for run in plan:
            pdf_path = run["pdf"]
            run_number = len(warmup_plan) + run["run_number"]
            log_event(
                f"[{run_number}/{len(warmup_plan) + len(plan)}] steady (repeat {run['repeat_index_for_pdf']}, "
                f"pdf {run['pdf_order']}/{len(pdfs)}) -> {pdf_path.name}"
            )

            page.set_input_files("#pdfFile", str(pdf_path))

            if args.energy_mode == "lhm":
                sampler = LhmPowerSampler(args.lhm_url)
                sampler.start()
                started = time.time()
                page.locator("#submitBtn").click()
                wait_with_status_logging(page, page.locator("#energyPanel"), f"Run {run_number} inferentie")
                wall_time_s = time.time() - started
                gross_energy_j = sampler.stop()
                # Baseline-aftrek: baseline_w × werkelijke duur
                baseline_energy_j = baseline_w * wall_time_s
                energy_j = max(0.0, gross_energy_j - baseline_energy_j)
                samples_count = len(sampler._samples)
                mean_power_w = sampler.mean_power_w()
            else:
                started = time.time()
                page.locator("#submitBtn").click()
                wait_with_status_logging(page, page.locator("#energyPanel"), f"Run {run_number} inferentie")
                wall_time_s = time.time() - started
                energy_j = 0.0 if args.energy_mode == "zero" else prompt_energy(run_number, pdf_path.name)
                gross_energy_j = energy_j
                baseline_energy_j = 0.0
                samples_count = 0
                mean_power_w = 0.0

            pending_info = page.locator("#pendingInfo").inner_text()
            json_output = page.locator("#jsonOutput").inner_text()
            sync_text_before = page.locator("#syncStatus").inner_text()

            page.locator("#energyInput").fill(str(energy_j))
            page.locator("#saveBtn").click()
            page.locator("#energyPanel").wait_for(state="hidden")

            sync_text = page.locator("#syncStatus").inner_text()
            ok, db_id = extract_sync_result(sync_text)
            log_event(
                f"Run {run_number} opgeslagen | ok={ok} | db_id={db_id} | "
                f"wall_time={wall_time_s:.2f}s | energy={energy_j:.2f}J"
            )

            result = {
                "run_number": run_number,
                "phase": "steady",
                "phase_index": run["phase_index"],
                "phase_total": run["phase_total"],
                "batch_id": args.batch_id,
                "repeat_index_for_pdf": run["repeat_index_for_pdf"],
                "pdf_order": run["pdf_order"],
                "pdf": str(pdf_path),
                "requested_at": datetime.now(timezone.utc).isoformat(),
                "wall_time_s": wall_time_s,
                "ok": ok,
                "energy_joules_net": energy_j,
                "energy_joules_gross": gross_energy_j,
                "energy_joules_baseline_correction": baseline_energy_j,
                "energy_source": "LibreHardwareMonitor" if args.energy_mode == "lhm" else args.energy_mode,
                "lhm_samples_count": samples_count,
                "lhm_mean_power_w": mean_power_w,
                "db_id": db_id,
                "pending_info": pending_info,
                "sync_status_before": sync_text_before,
                "sync_status_after": sync_text,
                "json_output": json_output,
            }
            summary["results"].append(result)
            output_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

        browser.close()

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

    ok_count = sum(1 for item in summary["results"] if item["ok"])
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
