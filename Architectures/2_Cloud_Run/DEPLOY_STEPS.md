# Deployment Stappen - Cloud Run (Unified)

Wanneer je wijzigingen hebt aangebracht aan de Python code (`app.py`), de `Dockerfile` of andere bestanden, moet je deze stappen uitvoeren om de update live te zetten op Google Cloud Run.

Zorg dat je terminal zich **in de hoofdmap `DocExtract`** bevindt vóór je deze commando's uitvoert.
⚠️ **Belangrijk:** Voer deze stappen *niet* uit vanuit de `Architectures/2_Cloud_Run` submap, anders ontbreekt de `SharedCore` map in de container.

---

## Eenmalig: Base Image bouwen (alleen bij eerste keer of Ollama/model update)

De base image bevat het OS, Ollama en het Gemma3:12b model. Dit hoef je **niet** bij elke deploy opnieuw te doen.

```powershell
docker build -f Architectures/2_Cloud_Run/Dockerfile.base -t europe-west4-docker.pkg.dev/zinc-wares-488311-a0/thesis-repo/extest-base:latest .
docker push europe-west4-docker.pkg.dev/zinc-wares-488311-a0/thesis-repo/extest-base:latest
```

---

## Bij elke deploy: alleen app code herbouwen (~30 seconden)

### 1. Bouw de nieuwe Docker Image

Alleen de app code wordt herbouwd — model en systeem lagen zijn gecached via de base image.

```powershell
docker build --build-arg CACHEBUST=$(Get-Date -UFormat %s) -f Architectures/2_Cloud_Run/Dockerfile -t extest-unified:latest .
```

### 2. Plaats de Google Cloud tag op de image

```powershell
docker tag extest-unified:latest europe-west4-docker.pkg.dev/zinc-wares-488311-a0/thesis-repo/extest-unified:latest
```

### 3. Authenticeer Docker bij Artifact Registry

Docker CLI (via WSL) en `gcloud` (Windows) delen geen PATH, waardoor de credential helper niet werkt. Log handmatig in met een access token:

```powershell
$token = gcloud auth print-access-token
docker login -u oauth2accesstoken -p $token europe-west4-docker.pkg.dev
```

### 4. Push naar Artifact Registry

```powershell
docker push europe-west4-docker.pkg.dev/zinc-wares-488311-a0/thesis-repo/extest-unified:latest
```

### 5. Deploy naar Cloud Run

```powershell
gcloud run deploy extest-web --image=europe-west4-docker.pkg.dev/zinc-wares-488311-a0/thesis-repo/extest-unified:latest --region=europe-west1
```
