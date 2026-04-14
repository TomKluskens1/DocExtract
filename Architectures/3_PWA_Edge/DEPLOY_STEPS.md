# Deployment Instructies - PWA Edge (Client-side)

Dit document beschrijft de stappen voor het lokaal opzetten van de backend voor de PWA Edge-architectuur. Omdat in deze architectuur de verwerking op de frontend/client gebeurt, is deze Python API puur een static file server en een node die meetgegevens ontvangt. Er hangt hier **geen** energiemeetapparatuur vast aan de Python server.

Zorg dat je in de map `DocExtract/Architectures/3_PWA_Edge` zit in je terminal.

## 1. Installeer Python Afhankelijkheden

Zet een lichte Virtual Environment (`venv`) op om de Flask webserver te hosten.

```bash
# Maak een virtual environment aan
python -m venv venv

# Activeer de environment
# Windows:
venv\Scripts\activate
# Linux/Mac:
source venv/bin/activate

# Installeer de vereisten (geen ML of CodeCarbon libraries nodig)
pip install -r requirements.txt
```

## 2. Backend Starten

De environment variabele `ARCHITECTURE` staat in voor de context in de Measurement DB.

```bash
# Linux/Mac:
export ARCHITECTURE="PWA"
python app.py

# Windows (PowerShell):
$env:ARCHITECTURE="PWA"
python app.py
```

De backend draait nu op `http://localhost:5000`.

Beschikbare routes:

- `http://localhost:5000/` -> PWA frontend voor lokale browser-extractie
- `http://localhost:5000/api/measurements` -> uitlezen van meetresultaten
- `http://localhost:5000/api/upload/` -> endpoint voor synchronisatie van de PWA-resultaten

## 3. Frontend WebGPU / Energiemeting instellen

1. Navigeer naar de PWA via de webserver (`http://localhost:5000/`).
2. Omdat er vanuit PWA op de server geen energiemeting is, dient alle energiemeting manueel of via profileringtools te gebeuren conform het meetprotocol.
   * **Voor PC/Laptop:** Open in de browser de ingebouwde Profiler (zoals **Firefox Profiler**), selecteer de "Power" presets en start een meting voor je de iteratie opstart via de webpagina.
   * **Voor Smartphone (Android):** Sideload of gebruik USB Debugging via **ADB** (Android Debug Bridge), reset de battery stats via `adb shell dumpsys batterystats --reset` en verzamel de resultaten na de run via `adb bugreport`.

Resultaten stuur je vervolgens via JSON pakketjes naar het Measurement API endpoint `/api/upload/`.

## 4. PWA Installatie Testen

1. Open `http://localhost:5000/` in een compatibele browser.
2. Controleer in DevTools dat `manifest.json` en `sw.js` zonder fouten laden.
3. Installeer de webapp indien de browser een install-prompt toont.
4. Controleer na een run in de pagina zelf of de extractiedata en sync-output zichtbaar zijn.
