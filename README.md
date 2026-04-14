# DocExtract: Energie-efficiënte Factuurextractie Architecturen

Praktische implementatie voor het bachelorproefonderzoek naar de energie-efficiëntie van AI-gedreven factuurextractie. Het doel is om gestructureerde data uit energiefacturen te extraheren en de ecologische voetafdruk van drie verschillende cloud/edge architecturen te meten en te vergelijken.

**Geëxtraheerde velden:** `supplier`, `start_date`, `end_date`, `kwh_quantity`, `co2eq_quantity`

> Extracties waarbij niet alle 5 velden succesvol worden gevuld, worden automatisch geweigerd (HTTP 422).

## De Drie Architectuurvarianten

| # | Variant | Locatie | Hardware | AI-Model | Energiemeting |
|---|---------|---------|----------|----------|---------------|
| 1 | **Server On-Premises** | HOGENT Datacenter | NVIDIA A30 GPU | Gemma 3 12B via Ollama | Scaphandre (RAPL) + PyNVML @ 100ms |
| 2 | **Serverless Cloud Run** | Google Cloud Run | NVIDIA L4 GPU | Gemma 3 12B via Ollama | CodeCarbon × 1.25 Fischer correction |
| 3 | **PWA Edge Computing** | Browser / Smartphone | Lokale CPU/GPU | Gemma 3 1B/4B via WebGPU | Firefox Profiler / Android ADB |

### 1. On-Premises (`Architectures/1_Server_OnPrem`)

Volledig lokale uitvoering op de HOGENT-server. `keep_alive: 0` scheidt actief inferentieverbruik van idle VRAM-gebruik. Energiemeting via Scaphandre Prometheus-scraping (CPU + DRAM via RAPL) en directe PyNVML GPU-polling.

### 2. Cloud Run (`Architectures/2_Cloud_Run`)

Gecontaineriseerde serverless deployment op Google Cloud Run. Onderscheid tussen een _Setup Stage_ (modelweging laden, cold start) en een _Generation Stage_ (inferentie) om cold-start impact inzichtelijk te maken.

### 3. PWA Edge (`Architectures/3_PWA_Edge`)

Volledige client-side inferentie in de browser via WebGPU/ONNX Runtime Web. Het model (1B of 4B, gekozen op basis van de Web Device Memory API) draait offline dankzij een Service Worker (`sw.js`). Geen netwerkkosten voor factuur-PDF's.

## Project Structuur

```
DocExtract/
├── Architectures/
│   ├── 1_Server_OnPrem/
│   │   ├── app.py
│   │   ├── requirements.txt
│   │   ├── start.sh
│   │   └── DEPLOY_STEPS.md
│   ├── 2_Cloud_Run/
│   │   ├── app.py
│   │   ├── requirements.txt
│   │   ├── Dockerfile
│   │   ├── Dockerfile.base
│   │   ├── extest-web.yaml
│   │   └── DEPLOY_STEPS.md
│   └── 3_PWA_Edge/
│       ├── app.py
│       ├── requirements.txt
│       ├── static/
│       │   ├── sw.js          # Service Worker (offline capability)
│       │   └── manifest.json
│       └── DEPLOY_STEPS.md
├── SharedCore/
│   ├── extraction_framework/  # Herbruikbare Ollama- en image-extractors
│   └── modello.py             # Pydantic schema's en ground truth definities
├── Dockerfile.web
├── .dockerignore
└── .env                       # Ollama endpoint config (niet in git)
```

## Opstarten

Elke architectuur heeft eigen vereisten. Raadpleeg de bijbehorende `DEPLOY_STEPS.md`.

### 1. Server On-Premises

```bash
cd Architectures/1_Server_OnPrem
pip install -r requirements.txt
# Vereisten: Scaphandre op :8080, Ollama (gemma3:12b) op :11434
export ARCHITECTURE=HOGENT
python app.py   # → http://localhost:5000
```

### 2. Cloud Run (lokale Docker-test)

```bash
# Bouwen vanuit de repo-root (SharedCore moet bereikbaar zijn)
docker build -f Architectures/2_Cloud_Run/Dockerfile -t extest-unified .
```

Deployen naar GCP:

```bash
docker tag extest-unified:latest europe-west4-docker.pkg.dev/zinc-wares-488311-a0/thesis-repo/extest-unified:latest
docker push europe-west4-docker.pkg.dev/zinc-wares-488311-a0/thesis-repo/extest-unified:latest
gcloud run services replace Architectures/2_Cloud_Run/extest-web.yaml --region=europe-west1
```

### 3. PWA Edge

```bash
cd Architectures/3_PWA_Edge
pip install -r requirements.txt
export ARCHITECTURE=PWA
python app.py   # → http://localhost:5000 (inferentie draait in de browser)
```

## Meetprotocol

### Measurement API

Elke succesvolle extractie stuurt een uniform JSON-meetobject naar de SQLite-database (`instance/measurements.db`). Alle drie architecturen schrijven naar hetzelfde `Measurement`-schema voor cross-architectuur vergelijking.

**Gelogde parameters:**

| Categorie | Velden |
|-----------|--------|
| Context | `architecture`, `hardware_context`, `model_size`, `document_status` (NATIVE / SCAN) |
| Timing | `response_time`, `setup_time_s` |
| Energie | `energy_joules`, `dram_joules`, `network_joules`, `setup_energy_joules` |
| Resultaat | `supplier`, `start_date`, `end_date`, `kwh_quantity`, `co2eq_quantity` |

### Vaste Constanten

| Constante | Waarde | Bron |
|-----------|--------|------|
| Netwerkkost | 36.000 J/GB (0,01 kWh/GB) | Literatuur |
| Carbon-intensiteit | 167 g CO₂/kWh | Belgisch/EU-gemiddelde |
| PDF-resolutie | 150 DPI → PNG → base64 | PyMuPDF |

### PUE-factoren per Architectuur

| Architectuur | PUE | Motivatie |
|--------------|-----|-----------|
| 1_Server_OnPrem | 1,5 | HOGENT datacenter overhead |
| 2_Cloud_Run | 1,1 | Google datacenter (hoge efficiëntie) |
| 3_PWA_Edge | 1,0 | Edge device, geen datacenter overhead |

## Omgevingsvariabelen

Kopieer `.env.example` en stel in:

```
OLLAMA_BASE_URL=<ollama endpoint>/v1
OLLAMA_API_KEY=<token of "ollama">
```

De actieve `.env` wijst naar de Cloud Run Ollama-deployment. Cloud Run service-to-service authenticatie verloopt via automatisch gegenereerde Google ID-tokens (zie `SharedCore/extraction_framework/llm_providers/__init__.py`).

## Licentie

Ontwikkeld door Tom Kluskens in samenwerking met Turtle Srl voor academisch onderzoek in het kader van de bachelorproef *"AI en energieverbruik in webarchitectuur: Een vergelijking tussen serverless, klassieke infrastructuur en Progressive Web Apps"* aan HOGENT. De basis in `SharedCore` is sterk geherstructureerd naar een service-georiënteerde 3-weg architectuur om de onderzoeksvragen te toetsen.
