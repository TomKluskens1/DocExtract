import os
import time
import uuid
import json
import threading
from pathlib import Path
from flask import Flask, request, jsonify, render_template
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text
import urllib.request

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import sys
current_dir = os.path.dirname(os.path.abspath(__file__))
shared_core_dir = os.path.abspath(os.path.join(current_dir, '..', '..', 'SharedCore'))
sys.path.append(shared_core_dir)
sys.path.append(current_dir)

# Client-side logica voor extracties, backend dient enkel voor resultaatopslag


# PWA variant gebruikt geen Python energiemetingen
EmissionsTracker = None

LHM_DEFAULT_URL = os.getenv("LHM_URL", "http://localhost:8085/data.json")
LHM_POLL_INTERVAL_S = 0.5
NETWORK_J_PER_GB = 36000.0
# PUE voor edge/laptop = 1.0: geen datacenter overhead (koeling, UPS, distributie).
# LHM meet rechtstreeks de componentenergie; infrastructuurverliezen zijn niet van toepassing.
PUE_PWA = 1.0
# Optionele LHM sensor-ID voor totaal platformvermogen (incl. DRAM, chipset, ...).
# Voorbeeld AMD: /amdcpu/0/power/1  |  Intel Package: /intelcpu/0/power/0
# Laat leeg om alleen CPU Package + GPU te meten (other_joules via PUE-verschil).
LHM_SYSTEM_SENSOR_ID = os.getenv("LHM_SYSTEM_SENSOR_ID", "")

LHM_SENSOR_DEFS = {
    "cpu_package_w": {"sensor_ids": {"/amdcpu/0/power/0"}},
    "gpu_nvidia_w": {"sensor_ids": {"/gpu-nvidia/0/power/0"}},
    "gpu_amd_core_w": {"sensor_ids": {"/gpu-amd/0/power/0"}},
    "gpu_amd_soc_w": {"sensor_ids": {"/gpu-amd/0/power/2"}},
    # Optioneel: totaal platformvermogen via LHM_SYSTEM_SENSOR_ID
    "system_total_w": {"sensor_ids": {LHM_SYSTEM_SENSOR_ID} if LHM_SYSTEM_SENSOR_ID else set()},
}

ENERGY_SESSIONS = {}
ENERGY_SESSIONS_LOCK = threading.Lock()
SESSION_MAX_AGE_S = 1800  # 30 minuten


def _cleanup_energy_sessions():
    """Fix 4: verwijder energy sessions die ouder zijn dan SESSION_MAX_AGE_S (browser crash etc.)"""
    while True:
        time.sleep(300)
        now = time.time()
        with ENERGY_SESSIONS_LOCK:
            expired = [sid for sid, sess in ENERGY_SESSIONS.items()
                       if now - sess["started_at"] > SESSION_MAX_AGE_S]
            for sid in expired:
                sess = ENERGY_SESSIONS.pop(sid)
                try:
                    sess["sampler"].stop()
                except Exception:
                    pass


threading.Thread(target=_cleanup_energy_sessions, daemon=True, name="energy-session-cleanup").start()


app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Database configuratie voor de Centrale Measurement API in Flask
# Gebruik DB_DIR env var voor Cloud Storage FUSE mount, of fall back op lokale map
db_dir = os.getenv("DB_DIR", os.path.join(current_dir, "instance"))
os.makedirs(db_dir, exist_ok=True)
db_path = os.path.abspath(os.path.join(db_dir, 'measurements.db'))

app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# ==========================================
# 1. Centrale Measurement API (Database Model)
# ==========================================
class Measurement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    batch_id = db.Column(db.String(64), nullable=True)
    architecture = db.Column(db.String(20)) # HOGENT, CLOUD_RUN, PWA
    hardware_context = db.Column(db.String(100))
    model_size = db.Column(db.String(200))
    response_time = db.Column(db.Float)
    setup_time_s = db.Column(db.Float, default=0.0)
    run_index = db.Column(db.Integer, nullable=True)
    energy_joules = db.Column(db.Float)
    setup_energy_joules = db.Column(db.Float, default=0.0)
    gpu_joules = db.Column(db.Float, default=0.0)
    gpu_nvidia_joules = db.Column(db.Float, default=0.0)
    gpu_amd_joules = db.Column(db.Float, default=0.0)
    gpu_amd_core_joules = db.Column(db.Float, default=0.0)
    gpu_amd_soc_joules = db.Column(db.Float, default=0.0)
    cpu_joules = db.Column(db.Float, default=0.0)
    dram_joules = db.Column(db.Float, default=0.0)
    network_joules = db.Column(db.Float, default=0.0)
    other_joules = db.Column(db.Float, default=0.0)
    gpu_avg_watts = db.Column(db.Float, default=0.0)
    pue_factor = db.Column(db.Float, default=1.0)
    carbon_intensity_gco2_kwh = db.Column(db.Float, default=0.0)
    document_status = db.Column(db.String(10)) # NATIVE of SCAN
    
    # 5 Target velden
    supplier = db.Column(db.String(255), nullable=True)
    start_date = db.Column(db.String(50), nullable=True)
    end_date = db.Column(db.String(50), nullable=True)
    kwh_quantity = db.Column(db.Float, nullable=True)
    co2eq_quantity = db.Column(db.Float, nullable=True)
    
    timestamp = db.Column(db.DateTime, default=db.func.current_timestamp())

with app.app_context():
    db.create_all()
    for ddl in (
        "ALTER TABLE measurement ADD COLUMN batch_id VARCHAR(64)",
        "ALTER TABLE measurement ADD COLUMN gpu_nvidia_joules FLOAT DEFAULT 0.0",
        "ALTER TABLE measurement ADD COLUMN gpu_amd_joules FLOAT DEFAULT 0.0",
        "ALTER TABLE measurement ADD COLUMN gpu_amd_core_joules FLOAT DEFAULT 0.0",
        "ALTER TABLE measurement ADD COLUMN gpu_amd_soc_joules FLOAT DEFAULT 0.0",
        "ALTER TABLE measurement ADD COLUMN other_joules FLOAT DEFAULT 0.0",
    ):
        try:
            db.session.execute(text(ddl))
            db.session.commit()
        except Exception:
            db.session.rollback()

# ==========================================
# 2. Hardware Energiemeting (Edge/PWA)
# Alle energiemetingen gebeuren extern: LibreHardwareMonitor (desktop) of ADB (Android)
# ==========================================
def _load_lhm_json(lhm_url: str):
    try:
        with urllib.request.urlopen(lhm_url, timeout=2) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:
        return None


def _parse_watt_value(raw_value):
    if not raw_value:
        return None
    try:
        return float(str(raw_value).replace(",", ".").split()[0])
    except (TypeError, ValueError, AttributeError):
        return None


def _walk_lhm_nodes(node):
    yield node
    for child in node.get("Children", []):
        yield from _walk_lhm_nodes(child)


def fetch_lhm_power_snapshot(lhm_url: str = LHM_DEFAULT_URL):
    data = _load_lhm_json(lhm_url)
    snapshot = {key: None for key in LHM_SENSOR_DEFS}
    if data is None:
        return snapshot

    for node in _walk_lhm_nodes(data):
        sensor_id = node.get("SensorId")
        if not sensor_id:
            continue
        for key, sensor_def in LHM_SENSOR_DEFS.items():
            if sensor_id in sensor_def["sensor_ids"]:
                snapshot[key] = _parse_watt_value(node.get("Value"))
    return snapshot


def summarize_lhm_power_snapshot(snapshot):
    cpu_package_w = snapshot.get("cpu_package_w") or 0.0
    gpu_nvidia_w = snapshot.get("gpu_nvidia_w") or 0.0
    gpu_amd_core_w = snapshot.get("gpu_amd_core_w") or 0.0
    gpu_amd_soc_w = snapshot.get("gpu_amd_soc_w") or 0.0
    system_total_w = snapshot.get("system_total_w") or 0.0
    gpu_total_w = gpu_nvidia_w + gpu_amd_core_w + gpu_amd_soc_w
    # total_selected = basis voor de sampler-check; gebruik system_total als beschikbaar
    total_selected_w = system_total_w if system_total_w > 0 else (cpu_package_w + gpu_total_w)
    return {
        "cpu_package_w": cpu_package_w,
        "gpu_nvidia_w": gpu_nvidia_w,
        "gpu_amd_core_w": gpu_amd_core_w,
        "gpu_amd_soc_w": gpu_amd_soc_w,
        "gpu_total_w": gpu_total_w,
        "system_total_w": system_total_w,
        "total_selected_w": total_selected_w,
    }


class LhmPowerSampler:
    def __init__(self, lhm_url: str):
        self._url = lhm_url
        self._samples = []
        self._running = False
        self._thread = None

    def start(self):
        self._samples = []
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        while self._running:
            timestamp = time.monotonic()
            snapshot = summarize_lhm_power_snapshot(fetch_lhm_power_snapshot(self._url))
            if snapshot["total_selected_w"] > 0:
                self._samples.append((timestamp, snapshot))
            time.sleep(LHM_POLL_INTERVAL_S)

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)

        if len(self._samples) < 2:
            energy = {
                "cpu_package_j": 0.0,
                "gpu_nvidia_j": 0.0,
                "gpu_amd_core_j": 0.0,
                "gpu_amd_soc_j": 0.0,
                "gpu_total_j": 0.0,
                "total_selected_j": 0.0,
            }
            if self._samples:
                _, sample = self._samples[0]
                dt = LHM_POLL_INTERVAL_S
                energy["cpu_package_j"] = sample["cpu_package_w"] * dt
                energy["gpu_nvidia_j"] = sample["gpu_nvidia_w"] * dt
                energy["gpu_amd_core_j"] = sample["gpu_amd_core_w"] * dt
                energy["gpu_amd_soc_j"] = sample["gpu_amd_soc_w"] * dt
                energy["gpu_total_j"] = sample["gpu_total_w"] * dt
                energy["total_selected_j"] = sample["total_selected_w"] * dt
            return energy

        energy = {
            "cpu_package_j": 0.0,
            "gpu_nvidia_j": 0.0,
            "gpu_amd_core_j": 0.0,
            "gpu_amd_soc_j": 0.0,
            "gpu_total_j": 0.0,
            "system_total_j": 0.0,
            "total_selected_j": 0.0,
        }
        for i in range(1, len(self._samples)):
            dt = self._samples[i][0] - self._samples[i - 1][0]
            prev = self._samples[i - 1][1]
            curr = self._samples[i][1]
            energy["cpu_package_j"] += ((prev["cpu_package_w"] + curr["cpu_package_w"]) / 2.0) * dt
            energy["gpu_nvidia_j"] += ((prev["gpu_nvidia_w"] + curr["gpu_nvidia_w"]) / 2.0) * dt
            energy["gpu_amd_core_j"] += ((prev["gpu_amd_core_w"] + curr["gpu_amd_core_w"]) / 2.0) * dt
            energy["gpu_amd_soc_j"] += ((prev["gpu_amd_soc_w"] + curr["gpu_amd_soc_w"]) / 2.0) * dt
            energy["gpu_total_j"] += ((prev["gpu_total_w"] + curr["gpu_total_w"]) / 2.0) * dt
            energy["system_total_j"] += ((prev["system_total_w"] + curr["system_total_w"]) / 2.0) * dt
            energy["total_selected_j"] += ((prev["total_selected_w"] + curr["total_selected_w"]) / 2.0) * dt
        return energy


def _estimate_network_joules(network_bytes_estimate):
    try:
        network_bytes = float(network_bytes_estimate or 0.0)
    except (TypeError, ValueError):
        return 0.0
    if network_bytes <= 0:
        return 0.0
    return (network_bytes / (1024 ** 3)) * NETWORK_J_PER_GB

# ==========================================
# 3. Applicatie Routes (UI & API)
# ==========================================



@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/energy/status', methods=['GET'])
def energy_status():
    snapshot = summarize_lhm_power_snapshot(fetch_lhm_power_snapshot(LHM_DEFAULT_URL))
    available = snapshot["cpu_package_w"] > 0 or snapshot["gpu_total_w"] > 0
    return jsonify({
        "available": available,
        "lhm_url": LHM_DEFAULT_URL,
        "snapshot": snapshot,
    })


@app.route('/api/energy/start', methods=['POST'])
def energy_start():
    payload = request.get_json(silent=True) or {}
    lhm_url = payload.get("lhm_url") or LHM_DEFAULT_URL
    sampler = LhmPowerSampler(lhm_url)
    sampler.start()
    session_id = uuid.uuid4().hex
    with ENERGY_SESSIONS_LOCK:
        ENERGY_SESSIONS[session_id] = {
            "sampler": sampler,
            "lhm_url": lhm_url,
            "started_at": time.time(),
        }
    return jsonify({
        "status": "started",
        "session_id": session_id,
        "lhm_url": lhm_url,
    })


@app.route('/api/energy/stop', methods=['POST'])
def energy_stop():
    payload = request.get_json(silent=True) or {}
    session_id = payload.get("session_id")
    if not session_id:
        return jsonify({"error": "session_id ontbreekt"}), 400

    with ENERGY_SESSIONS_LOCK:
        session = ENERGY_SESSIONS.pop(session_id, None)

    if not session:
        return jsonify({"error": "onbekende of reeds gestopte sessie"}), 404

    components = session["sampler"].stop()
    wall_time_s = float(payload.get("wall_time_s") or 0.0)
    network_bytes_estimate = float(payload.get("network_bytes_estimate") or 0.0)
    network_joules = _estimate_network_joules(network_bytes_estimate)
    cpu_joules = components["cpu_package_j"]
    gpu_joules = components["gpu_total_j"]
    gpu_nvidia_joules = components["gpu_nvidia_j"]
    gpu_amd_core_joules = components["gpu_amd_core_j"]
    gpu_amd_soc_joules = components["gpu_amd_soc_j"]
    gpu_amd_joules = gpu_amd_core_joules + gpu_amd_soc_joules
    system_total_j = components.get("system_total_j", 0.0)

    # Totale energie: gebruik system_total sensor als beschikbaar (meer volledig),
    # anders som van componenten × PUE (efficiëntieverliezen voedingsadapter).
    if system_total_j > 0:
        base_energy_joules = system_total_j
        energy_source_detail = "LibreHardwareMonitor (systeem totaal) + afgeleide netwerkenergie"
    else:
        base_energy_joules = cpu_joules + gpu_joules
        energy_source_detail = "LibreHardwareMonitor (CPU+GPU) + afgeleide netwerkenergie"

    total_energy_joules = (base_energy_joules * PUE_PWA) + network_joules
    other_joules = max(total_energy_joules - cpu_joules - gpu_joules - network_joules, 0.0)
    gpu_avg_watts = (gpu_joules / wall_time_s) if wall_time_s > 0 else 0.0

    return jsonify({
        "status": "stopped",
        "session_id": session_id,
        "energy_joules": total_energy_joules,
        "cpu_joules": cpu_joules,
        "gpu_joules": gpu_joules,
        "gpu_nvidia_joules": gpu_nvidia_joules,
        "gpu_amd_joules": gpu_amd_joules,
        "gpu_amd_core_joules": gpu_amd_core_joules,
        "gpu_amd_soc_joules": gpu_amd_soc_joules,
        "dram_joules": None,
        "network_joules": network_joules,
        "other_system_joules": other_joules,
        "pue_factor": PUE_PWA,
        "system_sensor_used": system_total_j > 0,
        "gpu_avg_watts": gpu_avg_watts,
        "energy_source": energy_source_detail,
        "network_bytes_estimate": network_bytes_estimate,
        "components": components,
    })

@app.route('/api/measurements', methods=['GET'])
def get_measurements():
    measurements = Measurement.query.order_by(Measurement.timestamp.desc()).all()
    data = [{
        "id": m.id, "batch_id": m.batch_id, "architecture": m.architecture, "hardware_context": m.hardware_context,
        "model_size": m.model_size, "response_time": m.response_time, "setup_time_s": m.setup_time_s,
        "energy_joules": m.energy_joules, "setup_energy_joules": m.setup_energy_joules,
        "gpu_joules": m.gpu_joules, "cpu_joules": m.cpu_joules,
        "gpu_nvidia_joules": m.gpu_nvidia_joules, "gpu_amd_joules": m.gpu_amd_joules,
        "gpu_amd_core_joules": m.gpu_amd_core_joules, "gpu_amd_soc_joules": m.gpu_amd_soc_joules,
        "dram_joules": m.dram_joules, "network_joules": m.network_joules,
        "other_joules": m.other_joules,
        "gpu_avg_watts": m.gpu_avg_watts, "pue_factor": m.pue_factor,
        "carbon_intensity_gco2_kwh": m.carbon_intensity_gco2_kwh,
        "document_status": m.document_status, 
        "supplier": m.supplier, "start_date": m.start_date,
        "end_date": m.end_date, "kwh_quantity": m.kwh_quantity,
        "co2eq_quantity": m.co2eq_quantity, "timestamp": m.timestamp
    } for m in measurements]
    return jsonify(data)

@app.route('/api/upload/', methods=['POST'])
def api_upload():
    """Centrale Measurement API voor externe PWA synchronisatie"""
    data = request.json
    
    # Gemma 3 1B kan niet altijd alle velden extraheren; incomplete runs worden toch opgeslagen
    # zodat ook mislukte extracties als meetresultaat bijdragen aan de thesisanalyse.

    m = Measurement(
        batch_id=data.get('batch_id'),
        architecture=data.get('architecture', 'UNKNOWN'),
        hardware_context=data.get('hardware_context', 'UNKNOWN'),
        model_size=data.get('model_size', 'UNKNOWN'),
        response_time=data.get('response_time', 0.0),
        setup_time_s=data.get('setup_time_s', 0.0),
        energy_joules=data.get('energy_joules', 0.0),
        setup_energy_joules=data.get('setup_energy_joules', 0.0),
        gpu_joules=data.get('gpu_joules', 0.0),
        gpu_nvidia_joules=data.get('gpu_nvidia_joules', 0.0),
        gpu_amd_joules=data.get('gpu_amd_joules', 0.0),
        gpu_amd_core_joules=data.get('gpu_amd_core_joules', 0.0),
        gpu_amd_soc_joules=data.get('gpu_amd_soc_joules', 0.0),
        cpu_joules=data.get('cpu_joules', 0.0),
        dram_joules=data.get('dram_joules', 0.0),
        network_joules=data.get('network_joules', 0.0),
        other_joules=data.get('other_system_joules', 0.0),
        gpu_avg_watts=data.get('gpu_avg_watts', 0.0),
        run_index=data.get('run_index'),
        pue_factor=data.get('pue_factor', 1.0),
        carbon_intensity_gco2_kwh=data.get('carbon_intensity_gco2_kwh', 200.5),
        document_status=data.get('document_status', 'UNKNOWN'),
        supplier=data.get('supplier'),
        start_date=data.get('start_date'),
        end_date=data.get('end_date'),
        kwh_quantity=data.get('kwh_quantity'),
        co2eq_quantity=data.get('co2eq_quantity')
    )
    db.session.add(m)
    db.session.commit()
    return jsonify({"status": "success", "id": m.id}), 201



if __name__ == '__main__':
    port = int(os.getenv("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
