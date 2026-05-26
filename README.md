# DocExtract: Energie-efficiënte Factuurextractie Architecturen

Praktische implementatie voor het bachelorproefonderzoek naar de energie-efficiëntie van AI-gedreven factuurextractie. Het doel is om gestructureerde data uit energiefacturen te extraheren en de ecologische voetafdruk van drie verschillende cloud/edge architecturen te meten en te vergelijken.

**Geëxtraheerde velden:** `supplier`, `start_date`, `end_date`, `kwh_quantity`

## De Drie Architectuurvarianten

| #   | Variant                  | Locatie              | Hardware       | AI-Model                 | Energiemeting                        |
| --- | ------------------------ | -------------------- | -------------- | ------------------------ | ------------------------------------ |
| 1   | **Server On-Premises**   | Leafcloud Server     | NVIDIA A30 GPU | Gemma 3 12B via Ollama   | PyNVML @ 100ms + CodeCarbon          |
| 2   | **Serverless Cloud Run** | Google Cloud Run     | NVIDIA L4 GPU  | Gemma 3 12B via Ollama   | CodeCarbon × 1.25 Fischer correction |
| 3   | **PWA Edge Computing**   | Browser / Smartphone | Lokale CPU/GPU | Gemma 3 1B/4B via WebGPU | Firefox Profiler / Android ADB       |

### 1. On-Premises (`Architectures/1_Server_OnPrem`)

Volledig lokale uitvoering op de Leafcloud-server. Het model blijft tussen opeenvolgende requests warm in VRAM, zoals gebruikelijk is voor een klassieke serveropstelling. Energiemeting gebeurt hybride: GPU direct via PyNVML-polling en CPU/DRAM via CodeCarbon, omdat RAPL-passthrough in de VM niet betrouwbaar beschikbaar is.

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
│       │   ├── manifest.json
│       │   └── js/            # pdf.mjs, transformers.js
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

### Geautomatiseerde benchmark-runs

Voor herhaalbare testreeksen kun je `scripts/run_benchmark.py` gebruiken. Het script gebruikt standaard de PDF's in `../Dataset` en voert per PDF `1` warm-up run, `8` steady-state runs en `4` cold-start candidate runs uit. Met de huidige dataset van `4` PDF's komt dat neer op `4` warm-up runs, `32` steady-state runs en `16` cold-start candidate runs. Tussen de cold-start candidate runs wacht het standaard `600` seconden. Elke benchmarkbatch krijgt automatisch een unieke `batch_id` die mee opgeslagen wordt in de metingen.

```bash
cd DocExtract
python scripts/run_benchmark.py \
  --base-url https://extest-web-191306170452.europe-west1.run.app \
  --output benchmark_results.json
```

Gebruik `--pdf` meerdere keren als je expliciet een lijst bestanden wilt opgeven, of overschrijf `--pdf-dir` als je een andere datasetmap wilt gebruiken. Het script logt per run o.a. `batch_id`, `measurement_id`, HTTP-status, `response_time`, `setup_time_s`, fase (`warmup`, `steady`, `cold_candidate`) en de herhaling per PDF, zodat je achteraf kunt zien welke cold-start candidate runs effectief hoge setup-tijden hadden.

Als `MeasurementDashboard` draait, kan het script na afloop ook exact zijn eigen batch downloaden via de dashboard-export:

```bash
python scripts/run_benchmark.py \
  --base-url https://extest-web-191306170452.europe-west1.run.app \
  --dashboard-export-url http://127.0.0.1:8080/api/measurements/export \
  --output benchmark_results.json \
  --dashboard-export-output benchmark_dashboard_export.json
```

Voor de on-premises backend is er ook een aparte wrapper. Die gaat uit van een SSH-tunnel naar de HOGENT-server en gebruikt lokaal `http://127.0.0.1:5000` als endpoint.

Start eerst de tunnel naar de VM. Op basis van [hogent-vm-config.json](D:\Projects\Bachelorproef\DocExtract\Architectures\1_Server_OnPrem\hogent-vm-config.json) is dat de subdomain `tomkluskens.vichogent.be` met SSH forward op externe poort `41163`:

```bash
ssh -p 41163 -L 5000:localhost:5000 <jouw-gebruiker>@tomkluskens.vichogent.be
```

Daarna kun je de on-prem benchmarkrunner starten:

```bash
cd DocExtract
python scripts/run_benchmark_onprem.py \
  --dashboard-export-url http://127.0.0.1:8080/api/measurements/export
```

Die wrapper gebruikt standaard:

- `--base-url http://127.0.0.1:5000`
- `--architecture HOGENT`
- `--output benchmark_onprem_results.json`
- `--dashboard-export-output benchmark_onprem_dashboard_export.json`

Voor een volledige nacht-run van cloud en server tegelijk is er ook een parallelle orchestrator. Die start beide reeksen met een eigen `batch_id`, houdt aparte logbestanden bij en volgt het protocol uit `../meetprotocol_overzicht.md`: server krijgt `4` warm-up runs en `32` warm runs, cloud krijgt daarnaast nog `16` cold-start candidate runs. Met `--shuffle` wordt de volgorde per fase aselect uitgevoerd.

```bash
cd DocExtract
python scripts/run_benchmark_parallel.py \
  --dashboard-export-url http://127.0.0.1:8080/api/measurements/export \
  --output-dir parallel_benchmark
```

Voorwaarde:

- `MeasurementDashboard` draait lokaal
- de SSH-tunnel naar HOGENT staat open op `127.0.0.1:5000`
- de cloudservice is live bereikbaar

### 3. PWA Edge

```bash
cd Architectures/3_PWA_Edge
pip install -r requirements.txt
export ARCHITECTURE=PWA
python app.py   # → http://localhost:5000 (inferentie draait in de browser)
```

## Extractie-pipeline

Hoe een PDF zijn weg vindt naar de geëxtraheerde velden en hoe `modello.py` daarin past:

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         POST /extract  (PDF upload)                     │
└────────────────────────────────┬────────────────────────────────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │  PyMuPDF (fitz)          │
                    │  • Detecteer type:       │
                    │    NATIVE (tekst > 100)  │
                    │    SCAN  (image-only)    │
                    │  • Render @ 150 DPI      │
                    │  • Pagina's → PNG bytes  │
                    └────────────┬────────────┘
                                 │  List[{ page_number, image_bytes, ... }]
                    ┌────────────▼────────────┐
                    │  ImageExtractor          │
                    │  SharedCore/extraction_  │
                    │  framework/extractors/   │
                    │                          │
                    │  base64-encode elke PNG  │
                    │  → inline data-URL       │
                    └────────────┬────────────┘
                                 │  content = [image_url, image_url, ...]
          ┌──────────────────────▼──────────────────────────┐
          │               modello.py                         │
          │                                                  │
          │  BachelorProefModel.__doc__  →  system_prompt   │
          │  ┌──────────────────────────────────────────┐   │
          │  │ "You are an expert at extracting         │   │
          │  │  structured data from Italian utility    │   │
          │  │  bills. CRITICAL RULES:                  │   │
          │  │  1. Only extract the INVOICED period     │   │
          │  │  2. Ignore historical tables             │   │
          │  │  3. kwh_quantity must match the period   │   │
          │  │  ..."                                    │   │
          │  └──────────────────────────────────────────┘   │
          │                                                  │
          │  BachelorProefModel  →  response_format (JSON)  │
          │  ┌─────────────────────────────────────────┐    │
          │  │ class BachelorProefModel(BaseModel):     │    │
          │  │   periodes: List[Periode]                │    │
          │  │                                          │    │
          │  │ class Periode(BaseModel):                │    │
          │  │   supplier:     Optional[str]            │    │
          │  │   start_date:   Optional[str]  YYYY-MM-DD│    │
          │  │   end_date:     Optional[str]  YYYY-MM-DD│    │
          │  │   kwh_quantity: Optional[float]          │    │
          │  └─────────────────────────────────────────┘    │
          └──────────────────────┬──────────────────────────┘
                                 │  system_prompt + images + response_format
                    ┌────────────▼────────────┐
                    │  OpenAIProvider          │
                    │  SharedCore/extraction_  │
                    │  framework/llm_providers/│
                    │                          │
                    │  client.beta.chat        │
                    │  .completions.parse()    │
                    │  temperature=0.0         │
                    └────────────┬────────────┘
                                 │  Ollama /v1/chat/completions
                    ┌────────────▼────────────┐
                    │  Gemma 3  (via Ollama)   │
                    │  12B  — server/cloud     │
                    │   1B/4B — PWA (WebGPU)  │
                    └────────────┬────────────┘
                                 │  BachelorProefModel (Pydantic-validated)
                    ┌────────────▼────────────┐
                    │  app.py  — veldextractie │
                    │                          │
                    │  periode = periodes[0]   │
                    │  supplier      ──────────┼──► opgeslagen in DB
                    │  start_date    ──────────┼──► opgeslagen in DB
                    │  end_date      ──────────┼──► opgeslagen in DB
                    │  kwh_quantity  ──────────┼──► opgeslagen in DB
                    │                          │
                    │  compute_co2eq(kwh)      │
                    │  = kwh × 200,5 / 1000    │
                    │  co2eq_quantity ─────────┼──► opgeslagen in DB
                    │                          │
                    │  Alle 4 velden aanwezig? │
                    │  (supplier, start_date,  │
                    │   end_date, kwh_quantity)│
                    │  Nee → retry met strenger│
                    │  prompt; daarna nog null?│
                    │  → HTTP 422, niet in DB  │
                    └──────────────────────────┘
```

> **Hoe de prompt werkt:** de docstring van `BachelorProefModel` in `modello.py` is letterlijk de `system_prompt` die aan het model wordt meegegeven. Het Pydantic-schema van diezelfde klasse wordt als `response_format` doorgegeven aan de OpenAI-compatibele API, waardoor Ollama/Gemma 3 zijn output dwingend als geldig JSON in dat schema retourneert.

## Meetprotocol

### Measurement API

Elke succesvolle extractie stuurt een uniform JSON-meetobject naar de SQLite-database (`instance/measurements.db`). Alle drie architecturen schrijven naar hetzelfde `Measurement`-schema voor cross-architectuur vergelijking.

**Gelogde parameters:**

| Categorie | Velden                                                                              |
| --------- | ----------------------------------------------------------------------------------- |
| Context   | `architecture`, `hardware_context`, `model_size`, `document_status` (NATIVE / SCAN) |
| Timing    | `response_time`, `setup_time_s`                                                     |
| Energie   | `energy_joules`, `dram_joules`, `network_joules`, `setup_energy_joules`             |
| Resultaat | `supplier`, `start_date`, `end_date`, `kwh_quantity`, `co2eq_quantity`              |

De `GET /api/measurements`-endpoint van elke architectuur geeft diezelfde 5 extractievelden ook expliciet mee in de JSON-respons. Daardoor kan `MeasurementDashboard` ze rechtstreeks ophalen, exporteren en tussen runs/architecturen vergelijken zonder extra parsing uit de database.

### Vaste Constanten

| Constante          | Waarde                    | Bron                      |
| ------------------ | ------------------------- | ------------------------- |
| Netwerkkost        | 36.000 J/GB (0,01 kWh/GB) | Literatuur                |
| Carbon-intensiteit | 200,5 g CO₂/kWh           | Italiaans gemiddelde 2024 |
| PDF-resolutie      | 150 DPI → PNG → base64    | PyMuPDF                   |

### PUE-factoren per Architectuur

| Architectuur    | PUE | Motivatie                             |
| --------------- | --- | ------------------------------------- |
| 1_Server_OnPrem | 1,5 | HOGENT datacenter overhead            |
| 2_Cloud_Run     | 1,1 | Google datacenter (hoge efficiëntie)  |
| 3_PWA_Edge      | 1,0 | Edge device, geen datacenter overhead |

## Omgevingsvariabelen

Kopieer `.env.example` en stel in:

```
OLLAMA_BASE_URL=<ollama endpoint>/v1
OLLAMA_API_KEY=<token of "ollama">
```

De actieve `.env` wijst naar de Cloud Run Ollama-deployment. Cloud Run service-to-service authenticatie verloopt via automatisch gegenereerde Google ID-tokens (zie `SharedCore/extraction_framework/llm_providers/__init__.py`).

## Licentie

Ontwikkeld door Tom Kluskens in samenwerking met Turtle Srl voor academisch onderzoek in het kader van de bachelorproef _"AI en energieverbruik in webarchitectuur: Een vergelijking tussen serverless, klassieke infrastructuur en Progressive Web Apps"_ aan HOGENT. De basis in `SharedCore` is sterk geherstructureerd naar een service-georiënteerde 3-weg architectuur om de onderzoeksvragen te toetsen.
