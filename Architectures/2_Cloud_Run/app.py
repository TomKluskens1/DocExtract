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

from extraction_framework.extractors.image_extractor import ImageExtractor
from extraction_framework.llm_providers import get_provider
from modello import BachelorProefModel

# Optionele import afhankelijk van omgeving
try:
    from codecarbon import EmissionsTracker
except ImportError:
    EmissionsTracker = None

try:
    import fitz
except ImportError:
    fitz = None

REQUIRED_FIELDS = ['supplier', 'start_date', 'end_date', 'kwh_quantity', 'co2eq_quantity']
RETRY_SYSTEM_PROMPT = """Extract exactly one invoiced billing period from this utility invoice.
Return JSON only.

Rules:
- Use the supplier from the first page.
- Use only the invoiced period, never yearly or historical summaries.
- start_date and end_date must be YYYY-MM-DD.
- kwh_quantity must belong to the same invoiced period.
- If co2eq_quantity is not found, use 0.0.
- If supplier, start_date, end_date or kwh_quantity are missing, set them to null.
"""

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Database configuratie voor de Centrale Measurement API in Flask
# Gebruik DB_DIR env var voor Cloud Storage FUSE mount, of fall back op /tmp voor Cloud Run
db_dir = os.environ.get("DB_DIR", "/tmp/instance")
db_dir = os.path.abspath(db_dir)

if not os.path.exists(db_dir):
    try:
        os.makedirs(db_dir, exist_ok=True)
    except Exception:
        db_dir = "/tmp/instance"
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
    batch_id = db.Column(db.String(64), nullable=True)
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
    pue_factor = db.Column(db.Float, default=1.1)
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
    try:
        db.session.execute(text("ALTER TABLE measurement ADD COLUMN batch_id VARCHAR(64)"))
        db.session.commit()
    except Exception:
        db.session.rollback()


def has_required_fields(extracted_dict: dict) -> bool:
    periodes = extracted_dict.get('periodes', []) if isinstance(extracted_dict, dict) else []
    first_periode = periodes[0] if periodes else {}
    return bool(periodes) and not any(first_periode.get(f) in [None, ""] for f in REQUIRED_FIELDS)


def extract_with_retry(provider, images):
    parsed_data, tokens = provider.extract_structured_data(
        schema=BachelorProefModel,
        system_prompt=BachelorProefModel.__doc__,
        image_data_list=images
    )
    extracted_dict = parsed_data.model_dump(mode='json')
    if has_required_fields(extracted_dict):
        return parsed_data, tokens, extracted_dict, False

    app.logger.warning("Primary extraction incomplete: %s", extracted_dict)

    retry_data, retry_tokens = provider.extract_structured_data(
        schema=BachelorProefModel,
        system_prompt=RETRY_SYSTEM_PROMPT,
        image_data_list=images
    )
    retry_dict = retry_data.model_dump(mode='json')
    merged_tokens = {
        "input": tokens.get("input", 0) + retry_tokens.get("input", 0),
        "output": tokens.get("output", 0) + retry_tokens.get("output", 0),
        "total": tokens.get("total", 0) + retry_tokens.get("total", 0),
    }
    if has_required_fields(retry_dict):
        app.logger.info("Retry extraction succeeded after incomplete primary output.")
        return retry_data, merged_tokens, retry_dict, True

    app.logger.error("Retry extraction still incomplete: %s", retry_dict)
    return retry_data, merged_tokens, retry_dict, True

# ==========================================
# 2. Hardware Energiemeting (Cloud Run)
# Cloud Run gebruikt CodeCarbon, hardware polling is verwijderd
# ==========================================

# ==========================================
# 3. Applicatie Routes (UI & API)
# ==========================================

@app.route('/')
def index():
    architecture = os.getenv("ARCHITECTURE", "CLOUD_RUN")
    return render_template('index.html', architecture=architecture)

@app.route('/api/measurements', methods=['GET'])
def get_measurements():
    measurements = Measurement.query.order_by(Measurement.timestamp.desc()).all()
    data = [{
        "id": m.id, "batch_id": m.batch_id, "architecture": m.architecture, "hardware_context": m.hardware_context,
        "model_size": m.model_size, "response_time": m.response_time, "setup_time_s": m.setup_time_s,
        "energy_joules": m.energy_joules, "setup_energy_joules": m.setup_energy_joules,
        "gpu_joules": m.gpu_joules, "cpu_joules": m.cpu_joules,
        "dram_joules": m.dram_joules, "network_joules": m.network_joules,
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
    
    # Validation constraint from thesis: run is disqualified if fields are missing
    required_fields = ['supplier', 'start_date', 'end_date', 'kwh_quantity', 'co2eq_quantity']
    if any(data.get(f) in [None, ""] for f in required_fields):
        return jsonify({"error": "Extractie onvolledig: missende doelvelden. Measurement gediskwalificeerd."}), 422

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
        cpu_joules=data.get('cpu_joules', 0.0),
        dram_joules=data.get('dram_joules', 0.0),
        network_joules=data.get('network_joules', 0.0),
        gpu_avg_watts=data.get('gpu_avg_watts', 0.0),
        pue_factor=data.get('pue_factor', 1.1),
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
    architecture = request.form.get("architecture") or os.getenv("ARCHITECTURE", "CLOUD_RUN")
    batch_id = request.form.get("batch_id")
    
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
    setup_energy_joules = 0.0
    setup_time_s = 0.0
    PUE_CLOUD = 1.1  # Google Cloud PUE factor (Google Environmental Report)
    # Fischer2025: CodeCarbon onderschat werkelijk verbruik met 20-30%.
    # Correctiefactor 1.25 = middelpunt van [1.20, 1.30] onderschattingsbereik.
    CODECARBON_CORRECTION = 1.25

    start_time = time.time()

    try:
        provider = get_provider("ollama")
        
        # 1. Setup Stage Meting (Preload model in VRAM)
        if EmissionsTracker:
            tracker = EmissionsTracker(save_to_file=False)
            tracker.start()
            
        try:
            # Force preload of the model to isolate Setup Stage energy
            import requests
            base = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1").replace("/v1", "")
            requests.post(f"{base}/api/generate", json={"model": provider.model}, timeout=120)
        except Exception:
            pass
            
        setup_time_s = time.time() - start_time
        
        if tracker:
            tracker.stop()
            setup_energy_joules = tracker._total_energy.kWh * 3600000 
            
            # Start fresh tracker for Generation Stage
            tracker = EmissionsTracker(save_to_file=False)
            tracker.start()
            
        gen_start_time = time.time()

        # 2. Extractie (Native Image Processing via Gemma)
        extractor = ImageExtractor(dpi=150)
        
        images = extractor.get_page_images_for_llm(filepath)
        parsed_data, tokens, extracted_dict, used_retry = extract_with_retry(provider, images)
        
        gen_duration = time.time() - gen_start_time
        total_time = time.time() - start_time
        
        # 3. Stop energiemeting en extraheer component-energie (GPU/CPU/DRAM)
        energy_joules = 0.0
        gpu_joules = 0.0
        cpu_joules = 0.0
        dram_joules = 0.0
        gpu_avg_watts = 0.0
        energy_source = "CodeCarbon"
        
        if tracker:
            tracker.stop()
            # CodeCarbon geeft energy in kWh. 1 kWh = 3.6 * 10^6 Joules
            # Stap 1: ruwe waarden uit CodeCarbon
            energy_joules_raw = tracker._total_energy.kWh * 3600000
            gpu_joules_raw = tracker._total_gpu_energy.kWh * 3600000
            cpu_joules_raw = tracker._total_cpu_energy.kWh * 3600000
            dram_joules_raw = tracker._total_ram_energy.kWh * 3600000
            
            # Stap 2: Fischer-correctie ×1.25 (CodeCarbon onderschat 20-30%)
            energy_joules = energy_joules_raw * CODECARBON_CORRECTION
            gpu_joules = gpu_joules_raw * CODECARBON_CORRECTION
            cpu_joules = cpu_joules_raw * CODECARBON_CORRECTION
            dram_joules = dram_joules_raw * CODECARBON_CORRECTION
            
            # Bereken gemiddeld GPU-vermogen (Watt) uit gecorrigeerde energie en tijd
            gpu_avg_watts = gpu_joules / gen_duration if gen_duration > 0 else 0.0

        # 4. JSON Formatteren
        periodes = extracted_dict.get('periodes', [])
        first_periode = periodes[0] if periodes else {}
        
        # Validation constraint from thesis: run is disqualified if fields are missing
        if not periodes or any(first_periode.get(f) in [None, ""] for f in REQUIRED_FIELDS):
            raise ValueError("Extractie onvolledig: missende doelvelden. Measurement gediskwalificeerd.")
        
        # 5. PUE-correctie toepassen (Google Cloud PUE = 1.1)
        # Totaalformule: energy_corrected = CodeCarbon_raw × 1.25 (Fischer) × PUE
        energy_joules_pue = energy_joules * PUE_CLOUD
        
        # Carbon intensity: Google Cloud region europe-west1 (België/NL)
        CARBON_INTENSITY = 167.0  # g CO₂/kWh (ENTSO-E jaargemiddelde België)
        
        # 6. Sla direct op in de lokale Measurement API Database
        m = Measurement(
            batch_id=batch_id,
            architecture=architecture,
            hardware_context="Google Cloud L4" if architecture == "CLOUD_RUN" else "NVIDIA A30",
            model_size=provider.model,
            response_time=total_time,
            setup_time_s=setup_time_s,
            energy_joules=energy_joules_pue,
            setup_energy_joules=setup_energy_joules * PUE_CLOUD,
            gpu_joules=gpu_joules,
            cpu_joules=cpu_joules,
            dram_joules=dram_joules,
            network_joules=network_joules,
            gpu_avg_watts=gpu_avg_watts,
            pue_factor=PUE_CLOUD,
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
        
        os.remove(filepath)
        
        return jsonify({
            "extracted_data": extracted_dict,
            "metrics": {
                "execution_time_s": total_time,
                "setup_time_s": setup_time_s,
                "gen_time_s": gen_duration,
                "energy_joules": energy_joules,
                "energy_joules_pue": energy_joules_pue,
                "setup_energy_joules": setup_energy_joules,
                "gpu_joules": gpu_joules,
                "cpu_joules": cpu_joules,
                "dram_joules": dram_joules,
                "network_joules": network_joules,
                "gpu_avg_watts": gpu_avg_watts,
                "pue_factor": PUE_CLOUD,
                "energy_source": energy_source,
                "model_name": provider.model,
                "document_status": document_status,
                "used_retry": used_retry,
                "measurement_id": m.id,
                "batch_id": m.batch_id
            }
        })
        
    except Exception as e:
        app.logger.exception("Cloud Run extract failed")
        if tracker: tracker.stop()
        if os.path.exists(filepath): os.remove(filepath)
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    port = int(os.getenv("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
