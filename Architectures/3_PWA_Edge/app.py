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
    cpu_joules = db.Column(db.Float, default=0.0)
    dram_joules = db.Column(db.Float, default=0.0)
    network_joules = db.Column(db.Float, default=0.0)
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
    try:
        db.session.execute(text("ALTER TABLE measurement ADD COLUMN batch_id VARCHAR(64)"))
        db.session.commit()
    except Exception:
        db.session.rollback()

# ==========================================
# 2. Hardware Energiemeting (Edge/PWA)
# Alle energiemetingen gebeuren extern: LibreHardwareMonitor (desktop) of ADB (Android)
# ==========================================

# ==========================================
# 3. Applicatie Routes (UI & API)
# ==========================================



@app.route('/')
def index():
    return render_template('index.html')

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
        run_index=data.get('run_index'),
        pue_factor=data.get('pue_factor', 1.0),
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



if __name__ == '__main__':
    port = int(os.getenv("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
