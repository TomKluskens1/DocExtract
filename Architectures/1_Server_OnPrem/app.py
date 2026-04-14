import os
import time
import uuid
import json
import threading
from pathlib import Path
from flask import Flask, request, jsonify, render_template
from flask_sqlalchemy import SQLAlchemy
import urllib.request

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    from google.cloud import storage as gcs
    GCS_AVAILABLE = True
except ImportError:
    GCS_AVAILABLE = False

GCS_BUCKET = os.getenv("GCS_BUCKET", "thesis-measurements-bucket")
GCS_BLOB = os.getenv("GCS_BLOB", "measurements_hogent.db")

def sync_db_to_gcs(db_path: str):
    """Upload lokale SQLite database naar GCS na elke meting."""
    if not GCS_AVAILABLE:
        return
    try:
        client = gcs.Client()
        bucket = client.bucket(GCS_BUCKET)
        blob = bucket.blob(GCS_BLOB)
        blob.upload_from_filename(db_path)
        print(f"[GCS] Synced {db_path} → gs://{GCS_BUCKET}/{GCS_BLOB}")
    except Exception as e:
        print(f"[GCS] Sync failed: {e}")

import sys
current_dir = os.path.dirname(os.path.abspath(__file__))
shared_core_dir = os.path.abspath(os.path.join(current_dir, '..', '..', 'SharedCore'))
sys.path.append(shared_core_dir)
sys.path.append(current_dir)

from extraction_framework.extractors.image_extractor import ImageExtractor
from extraction_framework.llm_providers import get_provider
from modello import BachelorProefModel

# Server variant gebruikt geen codecarbon
EmissionsTracker = None

try:
    import fitz
except ImportError:
    fitz = None

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Database configuratie voor de Centrale Measurement API in Flask
# Gebruik DB_DIR env var voor Cloud Storage FUSE mount, of fall back op lokale map
db_dir = os.getenv("DB_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "instance"))
os.makedirs(db_dir, exist_ok=True)
db_path = os.path.join(db_dir, 'measurements.db')

app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# ==========================================
# 1. Centrale Measurement API (Database Model)
# ==========================================
class Measurement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    architecture = db.Column(db.String(20)) # HOGENT, CLOUD_RUN, PWA
    hardware_context = db.Column(db.String(100))
    model_size = db.Column(db.String(10))
    response_time = db.Column(db.Float)
    setup_time_s = db.Column(db.Float, default=0.0)
    energy_joules = db.Column(db.Float)
    setup_energy_joules = db.Column(db.Float, default=0.0)
    gpu_joules = db.Column(db.Float, default=0.0)
    cpu_joules = db.Column(db.Float, default=0.0)
    dram_joules = db.Column(db.Float, default=0.0)
    network_joules = db.Column(db.Float, default=0.0)
    gpu_avg_watts = db.Column(db.Float, default=0.0)
    pue_factor = db.Column(db.Float, default=1.5)
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

# ==========================================
# 2. Hardware Energiemeting (On-Premises)
# ==========================================

# GPU meting via NVML Python-binding (pynvml) - 100ms polling
try:
    import pynvml
    NVML_AVAILABLE = True
except ImportError:
    NVML_AVAILABLE = False

def query_prometheus_metrics(url: str, metric_prefix: str, tag_filter: str = None) -> float:
    """Query Scaphandre Prometheus exporter voor CPU/DRAM vermogen via RAPL."""
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=1) as response:
            data = response.read().decode('utf-8')
            for line in data.splitlines():
                if line.startswith(metric_prefix):
                    if tag_filter and tag_filter not in line:
                        continue
                    # Value is at the end, separated by space
                    parts = line.split(' ')
                    if len(parts) >= 2:
                        return float(parts[1])
    except Exception:
        pass
    return 0.0

def query_gpu_power_nvml() -> float:
    """Lees GPU-vermogen direct via NVML Python-binding (milliwatt → watt)."""
    if not NVML_AVAILABLE:
        return 0.0
    try:
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        power_mw = pynvml.nvmlDeviceGetPowerUsage(handle)
        return power_mw / 1000.0
    except Exception:
        return 0.0

class PowerSampler:
    def __init__(self):
        self.samples = []
        self.running = False
        self.thread = None

    def _sample_loop(self):
        """Sample CPU (Scaphandre/RAPL) en GPU (NVML) elke 100ms."""
        while self.running:
            # CPU via Scaphandre Prometheus exporter (RAPL)
            cpu_microwatts = query_prometheus_metrics(
                'http://localhost:8080/metrics', 'scaph_host_power_microwatts'
            )
            cpu_watts = cpu_microwatts / 1_000_000.0 if cpu_microwatts > 0 else 0.0
            
            # DRAM via Scaphandre (RAPL dram domain)
            dram_microwatts = query_prometheus_metrics(
                'http://localhost:8080/metrics', 'scaph_domain_power_microwatts', 'domain="dram"'
            )
            dram_watts = dram_microwatts / 1_000_000.0 if dram_microwatts > 0 else 0.0

            # GPU via NVML Python-binding (directe hardware-uitlezing)
            gpu_watts = query_gpu_power_nvml()

            self.samples.append({
                'time': time.time(),
                'cpu_watts': cpu_watts,
                'dram_watts': dram_watts,
                'gpu_watts': gpu_watts,
                'total_watts': cpu_watts + gpu_watts + dram_watts
            })
            time.sleep(0.1)  # 100ms polling-interval

    def start(self):
        self.samples = []
        self.running = True
        if NVML_AVAILABLE:
            try:
                pynvml.nvmlInit()
            except Exception:
                pass  # Geen NVIDIA driver beschikbaar (bv. Cloud Run zonder GPU)
        self.thread = threading.Thread(target=self._sample_loop, daemon=True)
        self.thread.start()

    def stop(self) -> dict:
        self.running = False
        if self.thread:
            self.thread.join(timeout=1.0)
        if NVML_AVAILABLE:
            try:
                pynvml.nvmlShutdown()
            except Exception:
                pass

        if not self.samples:
            return {"duration_s": 0.0, "total_joules": 0.0, "avg_total_watts": 0.0, "dram_joules": 0.0}

        duration = self.samples[-1]['time'] - self.samples[0]['time']
        if duration <= 0: duration = 0.1

        avg_total = sum(s['total_watts'] for s in self.samples) / len(self.samples)
        avg_gpu = sum(s['gpu_watts'] for s in self.samples) / len(self.samples)
        avg_cpu = sum(s['cpu_watts'] for s in self.samples) / len(self.samples)
        avg_dram = sum(s['dram_watts'] for s in self.samples) / len(self.samples)
        
        return {
            "duration_s": duration, 
            "total_joules": avg_total * duration, 
            "gpu_joules": avg_gpu * duration,
            "cpu_joules": avg_cpu * duration,
            "dram_joules": avg_dram * duration,
            "avg_total_watts": avg_total,
            "avg_gpu_watts": avg_gpu
        }

# ==========================================
# 3. Applicatie Routes (UI & API)
# ==========================================

@app.route('/')
def index():
    # Detecteer omgeving via Env Var, standaard Server/On-Prem
    architecture = os.getenv("ARCHITECTURE", "HOGENT")
    return render_template('index.html', architecture=architecture)

@app.route('/api/measurements', methods=['GET'])
def get_measurements():
    measurements = Measurement.query.order_by(Measurement.timestamp.desc()).all()
    data = [{
        "id": m.id, "architecture": m.architecture, "hardware_context": m.hardware_context,
        "model_size": m.model_size, "response_time": m.response_time, "setup_time_s": m.setup_time_s,
        "energy_joules": m.energy_joules, "setup_energy_joules": m.setup_energy_joules,
        "gpu_joules": m.gpu_joules, "cpu_joules": m.cpu_joules,
        "dram_joules": m.dram_joules, "network_joules": m.network_joules,
        "gpu_avg_watts": m.gpu_avg_watts, "pue_factor": m.pue_factor,
        "carbon_intensity_gco2_kwh": m.carbon_intensity_gco2_kwh,
        "document_status": m.document_status, 
        "supplier": m.supplier, "timestamp": m.timestamp
    } for m in measurements]
    return jsonify(data)

@app.route('/api/upload/', methods=['POST'])
def api_upload():
    """Centrale Measurement API voor externe PWA synchronisatie"""
    data = request.json
    
    # Validation constraint from thesis: run is disqualified if fields are missing
    required_fields = ['supplier', 'start_date', 'end_date', 'kwh_quantity', 'co2eq_quantity']
    if any(data.get(f) in [None, ""] for f in required_fields):
        return jsonify({"error": "Extractie onvolledig: missende doelvelden. Measurement gediskwalificeerd."}), 422

    m = Measurement(
        architecture=data.get('architecture', 'UNKNOWN'),
        hardware_context=data.get('hardware_context', 'UNKNOWN'),
        model_size=data.get('model_size', 'UNKNOWN'),
        response_time=data.get('response_time', 0.0),
        setup_time_s=data.get('setup_time_s', 0.0),
        energy_joules=data.get('energy_joules', 0.0),
        setup_energy_joules=data.get('setup_energy_joules', 0.0),
        gpu_joules=data.get('gpu_joules', 0.0),
        cpu_joules=data.get('cpu_joules', 0.0),
        dram_joules=data.get('dram_joules', 0.0),
        network_joules=data.get('network_joules', 0.0),
        gpu_avg_watts=data.get('gpu_avg_watts', 0.0),
        pue_factor=data.get('pue_factor', 1.5),
        carbon_intensity_gco2_kwh=data.get('carbon_intensity_gco2_kwh', 167.0),
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

@app.route('/extract', methods=['POST'])
def extract():
    """Backend extractie route voor Client-Server & Serverless varianten"""
    architecture = request.form.get("architecture") or os.getenv("ARCHITECTURE", "HOGENT")
    
    if 'file' not in request.files:
        return jsonify({"error": "No file part"}), 400
        
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400

    file.seek(0, os.SEEK_END)
    file_size_bytes = file.tell()
    file.seek(0)
    # 0.01 kWh/GB = 36,000 Joules/GB
    network_joules = (file_size_bytes / (1024 * 1024 * 1024)) * 36000

    filename = f"{uuid.uuid4()}.pdf"
    filepath = Path(app.config['UPLOAD_FOLDER']) / filename
    file.save(filepath)
    
    # Bepaal PDF Complexiteit (Native vs Scan) voor de scriptie metadata
    document_status = "UNKNOWN"
    if fitz:
        doc = fitz.open(filepath)
        text_length = sum(len(page.get_text()) for page in doc)
        document_status = "NATIVE" if text_length > 100 else "SCAN"
        doc.close()

    tracker = None
    sampler = None

    # PUE-correctie HOGENT datacenter (Shehabi2024: typisch 1.4-1.6 on-premises)
    PUE_HOGENT = 1.5  # Middelpunt van het bereik
    CARBON_INTENSITY = 167.0  # g CO₂/kWh (ENTSO-E jaargemiddelde België)

    start_time = time.time()

    try:
        # 1. Start specifieke energiemeting obv architectuur
        sampler = PowerSampler()
        sampler.start()

        # 2. Extractie (Native Image Processing via Gemma)
        extractor = ImageExtractor(dpi=150)
        provider = get_provider("ollama")
        
        images = extractor.get_page_images_for_llm(filepath)
        parsed_data, tokens = provider.extract_structured_data(
            schema=BachelorProefModel,
            system_prompt=BachelorProefModel.__doc__,
            image_data_list=images
        )
        
        exec_time = time.time() - start_time
        
        # 3. Stop energiemeting en extraheer component-energie
        energy_metrics = sampler.stop()
        energy_joules_raw = energy_metrics["total_joules"]
        gpu_joules = energy_metrics["gpu_joules"]
        cpu_joules = energy_metrics["cpu_joules"]
        dram_joules = energy_metrics["dram_joules"]
        gpu_avg_watts = energy_metrics["avg_gpu_watts"]
        energy_source = "Scaphandre+NVML"
        
        # PUE-correctie toepassen (datacenter overhead: koeling, voeding, UPS)
        energy_joules = energy_joules_raw * PUE_HOGENT

        # 4. JSON Formatteren
        extracted_dict = parsed_data.model_dump(mode='json')
        periodes = extracted_dict.get('periodes', [])
        first_periode = periodes[0] if periodes else {}
        
        # Validation constraint from thesis: run is disqualified if fields are missing
        required_fields = ['supplier', 'start_date', 'end_date', 'kwh_quantity', 'co2eq_quantity']
        if not periodes or any(first_periode.get(f) in [None, ""] for f in required_fields):
            raise ValueError("Extractie onvolledig: missende doelvelden. Measurement gediskwalificeerd.")
        
        # 5. Sla direct op in de lokale Measurement API Database
        m = Measurement(
            architecture=architecture,
            hardware_context="NVIDIA A30",
            model_size=provider.model,
            response_time=exec_time,
            setup_time_s=0.0,
            energy_joules=energy_joules,
            setup_energy_joules=0.0,
            gpu_joules=gpu_joules,
            cpu_joules=cpu_joules,
            dram_joules=dram_joules,
            network_joules=network_joules,
            gpu_avg_watts=gpu_avg_watts,
            pue_factor=PUE_HOGENT,
            carbon_intensity_gco2_kwh=CARBON_INTENSITY,
            document_status=document_status,
            supplier=first_periode.get('supplier'),
            start_date=first_periode.get('start_date'),
            end_date=first_periode.get('end_date'),
            kwh_quantity=first_periode.get('kwh_quantity'),
            co2eq_quantity=first_periode.get('co2eq_quantity')
        )
        db.session.add(m)
        db.session.commit()
        sync_db_to_gcs(db_path)

        os.remove(filepath)
        
        return jsonify({
            "extracted_data": extracted_dict,
            "metrics": {
                "execution_time_s": exec_time,
                "setup_time_s": 0.0,
                "energy_joules": energy_joules,
                "energy_joules_raw": energy_joules_raw,
                "setup_energy_joules": 0.0,
                "gpu_joules": gpu_joules,
                "cpu_joules": cpu_joules,
                "dram_joules": dram_joules,
                "network_joules": network_joules,
                "gpu_avg_watts": gpu_avg_watts,
                "pue_factor": PUE_HOGENT,
                "carbon_intensity_gco2_kwh": CARBON_INTENSITY,
                "energy_source": energy_source,
                "model_name": provider.model,
                "document_status": document_status,
                "measurement_id": m.id
            }
        })
        
    except Exception as e:
        if sampler: sampler.stop()
        if os.path.exists(filepath): os.remove(filepath)
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    port = int(os.getenv("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
