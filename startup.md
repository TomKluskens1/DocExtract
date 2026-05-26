# Startup

Praktische opstartgids voor de lokale benchmarkopstelling van `DocExtract`, inclusief alle drie de architecturen:

- `1_Server_OnPrem`
- `2_Cloud_Run`
- `3_PWA_Edge`

## Poorten

- `5000`: SSH-tunnel naar on-prem HOGENT backend
- `5001`: PWA API in `MeasurementDashboard`
- `5002`: lokale PWA backend (`Architectures/3_PWA_Edge`)
- `8080`: `MeasurementDashboard`
- `8085`: LibreHardwareMonitor webserver

## Architectuuroverzicht

### 1. On-Premises

Wat draait waar:

- backend draait op de HOGENT server
- lokaal gebruik je een SSH-tunnel naar `localhost:5000`
- `MeasurementDashboard` leest on-prem via die tunnel

### 2. Cloud Run

Wat draait waar:

- backend draait remote op Cloud Run
- lokaal hoef je hiervoor normaal niets extra te starten
- benchmark gebruikt standaard:
  - `https://extest-web-191306170452.europe-west1.run.app`

### 3. PWA Edge

Wat draait waar:

- lokale Flask backend op je laptop
- browser voert de inferentie uit
- energie komt via LibreHardwareMonitor
- dashboard leest PWA-metingen via de PWA API

## Volledige volgorde voor alle 3 architecturen

1. Start `MeasurementDashboard`
2. Start de SSH-tunnel voor on-prem
3. Controleer dat de on-prem service op de server zelf draait
4. Start de lokale PWA backend
5. Start LibreHardwareMonitor
6. Activeer de `scripts\venv`
7. Start de benchmark runner

## Terminal 1: MeasurementDashboard

```powershell
cd D:\Projects\Bachelorproef\MeasurementDashboard
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:PWA_API_URL="http://127.0.0.1:5002/api/measurements"
python app.py
```

Controle:

- dashboard op `http://127.0.0.1:8080/`
- startup-log moet tonen:
  - `HOGENT tunnel: http://localhost:5000/api/measurements`
  - `PWA API: http://127.0.0.1:5002/api/measurements`

## Terminal 2: SSH-tunnel naar HOGENT

```powershell
ssh -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -L 5000:localhost:5000 -p 41163 vicuser@vichogent.be
```

Doel:

- lokale `http://127.0.0.1:5000` wijst naar de on-prem backend op de server

Opmerking:

- als je `client_loop: send disconnect: Connection reset` ziet, is de tunnel weggevallen
- dan zullen on-prem requests lokaal timeouts geven, ook als de backend op de server zelf nog doorwerkt

## Terminal 3: PWA backend

```powershell
cd D:\Projects\Bachelorproef\DocExtract\Architectures\3_PWA_Edge
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install playwright
playwright install chromium
$env:ARCHITECTURE="PWA"
$env:PORT="5002"
python app.py
```

Controle:

- backend op `http://127.0.0.1:5002/`
- non-headless wordt nu standaard gebruikt door de PWA benchmarkrunner

## Voorwaarde: LibreHardwareMonitor

Start `LibreHardwareMonitor.exe` als administrator en zet de remote webserver aan op poort `8085`.

Controle:

- `http://localhost:8085/data.json`

## On-prem server zelf

Op de HOGENT server moet de on-prem backend al draaien. Als je die handmatig moet opstarten:

```bash
cd /pad/naar/DocExtract/Architectures/1_Server_OnPrem
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
sudo scaphandre prometheus -p 8080
OLLAMA_HOST=0.0.0.0:11434 ollama serve
export ARCHITECTURE="HOGENT"
python app.py
```

Als de app als `systemd` service draait:

```bash
systemctl status docextract-onprem.service
journalctl -u docextract-onprem.service -n 100 --no-pager
journalctl -u docextract-onprem.service -f
```

Extra checks op de server:

```bash
curl http://127.0.0.1:5000/api/measurements
systemctl status ollama
journalctl -u ollama -n 100 --no-pager
```

## Cloud Run

Voor Cloud Run hoef je lokaal geen aparte backend te starten zolang de gedeployde service live staat.

Snelle check:

```powershell
curl https://extest-web-191306170452.europe-west1.run.app/api/measurements
```

Als je Cloud Run opnieuw moet deployen:

```powershell
cd D:\Projects\Bachelorproef\DocExtract
docker build -f Architectures\2_Cloud_Run\Dockerfile -t extest-unified .
docker tag extest-unified:latest europe-west4-docker.pkg.dev/zinc-wares-488311-a0/thesis-repo/extest-unified:latest
docker push europe-west4-docker.pkg.dev/zinc-wares-488311-a0/thesis-repo/extest-unified:latest
gcloud run services replace Architectures\2_Cloud_Run\extest-web.yaml --region=europe-west1
```

## Terminal 4: benchmark runnen

De benchmarkscripts gebruiken de Python-omgeving in `D:\Projects\Bachelorproef\DocExtract\scripts\venv`.

```powershell
cd D:\Projects\Bachelorproef\DocExtract\scripts
.\venv\Scripts\Activate.ps1
python .\run_benchmark_parallel.py --pwa-base-url http://127.0.0.1:5002 --dashboard-export-url http://127.0.0.1:8080/api/measurements/export
```

Belangrijk:

- als je al in `D:\Projects\Bachelorproef\DocExtract\scripts` staat, gebruik dan `python .\run_benchmark_parallel.py`
- niet `python .\scripts\run_benchmark_parallel.py`

## Architectuur-specifieke benchmarkcommando's

### On-prem benchmark

```powershell
cd D:\Projects\Bachelorproef\DocExtract\scripts
.\venv\Scripts\Activate.ps1
python .\run_benchmark_onprem.py
```

Met minimale test:

```powershell
python .\run_benchmark_onprem.py --warmup-total 1 --steady-repeats 1 --cold-total 0 --max-retries 0
```

### Cloud Run benchmark

```powershell
cd D:\Projects\Bachelorproef\DocExtract\scripts
.\venv\Scripts\Activate.ps1
python .\run_benchmark.py --base-url https://extest-web-191306170452.europe-west1.run.app --architecture "Cloud Run"
```

Met minimale test:

```powershell
python .\run_benchmark.py --base-url https://extest-web-191306170452.europe-west1.run.app --architecture "Cloud Run" --warmup-total 1 --steady-repeats 1 --cold-total 0 --max-retries 0
```

### PWA benchmark

```powershell
cd D:\Projects\Bachelorproef\DocExtract\scripts
.\venv\Scripts\Activate.ps1
python .\run_benchmark_pwa_laptop.py --base-url http://127.0.0.1:5002 --dashboard-export-url http://127.0.0.1:8080/api/measurements/export
```

Met minimale test:

```powershell
python .\run_benchmark_pwa_laptop.py --base-url http://127.0.0.1:5002 --warmup-total 1 --steady-repeats 1 --dashboard-export-url http://127.0.0.1:8080/api/measurements/export
```

## Losse tests

### On-prem apart

```powershell
cd D:\Projects\Bachelorproef\DocExtract\scripts
.\venv\Scripts\Activate.ps1
python .\run_benchmark_onprem.py --warmup-total 1 --steady-repeats 1 --cold-total 0 --max-retries 0
```

### Cloud apart

```powershell
cd D:\Projects\Bachelorproef\DocExtract\scripts
.\venv\Scripts\Activate.ps1
python .\run_benchmark.py --base-url https://extest-web-191306170452.europe-west1.run.app --architecture "Cloud Run" --warmup-total 1 --steady-repeats 1 --cold-total 0 --max-retries 0
```

### PWA apart

```powershell
cd D:\Projects\Bachelorproef\DocExtract\scripts
.\venv\Scripts\Activate.ps1
python .\run_benchmark_pwa_laptop.py --base-url http://127.0.0.1:5002 --warmup-total 1 --steady-repeats 1 --dashboard-export-url http://127.0.0.1:8080/api/measurements/export
```

## Netwerkdiagnose

Een continue ping blijft lopen tot je zelf stopt met `Ctrl+C`.

```powershell
ping vichogent.be -t
```

Eventueel ook:

```powershell
ping 8.8.8.8 -t
```

Gebruik dit tijdens on-prem benchmarks om wifi- of tunnelproblemen te zien samenvallen met timeouts.

## Bekende observaties

- on-prem backend op de server gaf in `journalctl` wel `POST /extract ... 200` terug terwijl de laptop lokaal timeout zag
- dat wijst erop dat de backend zelf kan slagen, terwijl de SSH-tunnel of wifi onderweg wegvalt
- `client_loop: send disconnect: Connection reset` in de SSH-terminal is een sterke indicatie dat de tunnel de bottleneck was
- Cloud Run gaf server-side soms wel `200` terug terwijl de lokale client toch een timeout zag; ook daar blijft netwerkstabiliteit relevant
- PWA kan gedeeltelijk resultaatbestanden produceren, zelfs als de volledige orchestratie later met exit code `1` eindigt
- PWA uploads mogen incomplete extracties bevatten; `"null"`-stringwaarden in numerieke velden worden nu in de backend genormaliseerd naar `None`
- grafiekgeneratie kan apart falen van de benchmarkruns

## Resultaatbestanden

- cloud logs: `D:\Projects\Bachelorproef\DocExtract\scripts\results\cloud\json\benchmark_cloud.log`
- on-prem logs: `D:\Projects\Bachelorproef\DocExtract\scripts\results\server\json\benchmark_onprem.log`
- PWA logs: `D:\Projects\Bachelorproef\DocExtract\scripts\results\pwa\json\benchmark_pwa.log`
- parallel summary: `D:\Projects\Bachelorproef\DocExtract\scripts\results\benchmark_parallel_summary.json`
