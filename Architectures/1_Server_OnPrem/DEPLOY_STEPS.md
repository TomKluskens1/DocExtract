# Deployment Instructies - Server On-Premises (HOGENT)

De provisioning-config voor de toegewezen HoGent-VM staat in `hogent-vm-config.json`.

Voor de lokale Server-architectuur (uitgevoerd op hardware met een NVIDIA A30 GPU), volg je onderstaande stappen om de API correct te starten inclusief de energiemetingen.

Zorg dat je in de map `DocExtract/Architectures/1_Server_OnPrem` zit in je terminal.

## Vereisten

Zorg dat de volgende tools geïnstalleerd zijn op je (Linux/Windows) server:

1. **Ollama**: Om het Gemma3:12b model lokaal te draaien.
2. **Scaphandre**: Voor het uitlezen van de CPU en DRAM power domains via de RAPL-interface.
3. **NVIDIA Drivers**: Nodig voor de nvml python-binding (GPU power polling).

## 1. Start Externe Services

**Scaphandre Prometheus Exporter:**
Voor system-wide CPU/DRAM energiemeting via Scaphandre:

```bash
# Start Scaphandre Prometheus exporter op poort 8080 (standaard poort waar app.py naar pollt)
sudo scaphandre prometheus -p 8080
```

**Ollama Server:**
Zorg dat Ollama actief is op de achtergrond.

```bash
OLLAMA_HOST=0.0.0.0:11434 ollama serve &
```

_(Optioneel) Pull indien nodig het correcte Gemma model:_

```bash
ollama pull gemma3:12b
```

## 2. Installeer Python Afhankelijkheden

Zorg dat je een Python Virtual Environment (`venv`) opzet, dit voorkomt conflicten.

```bash
# Maak een virtual environment aan
python -m venv venv

# Activeer de environment
# Windows:
venv\Scripts\activate
# Linux/Mac:
source venv/bin/activate

# Installeer de vereisten
pip install -r requirements.txt
```

## 3. Applicatie Starten

Je kunt de Flask-app nu opstarten. De app detecteert zelf dat hij in Server-modus zit via de (optionele, maar wenselijke) `ARCHITECTURE` environment variable.

```bash
# Linux/Mac:
export ARCHITECTURE="HOGENT"
python app.py

# Windows (PowerShell):
$env:ARCHITECTURE="HOGENT"
python app.py
```

De api is nu lokaal beschikbaar op `http://localhost:5000`. De metingen (CPU + DRAM + GPU) lopen automatisch 100ms synchroon op de achtergrond telkens er een pdf geüpload wordt naar `/extract`.
