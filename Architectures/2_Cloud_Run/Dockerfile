FROM europe-west4-docker.pkg.dev/zinc-wares-488311-a0/thesis-repo/extest-base:latest

WORKDIR /app

# Laag 4: Python dependencies (wijzigt zelden)
COPY Architectures/2_Cloud_Run/requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# Laag 5: app code — enige laag die bij elke deploy wijzigt
ARG CACHEBUST=1
COPY Architectures/2_Cloud_Run/app.py Architectures/2_Cloud_Run/app.py
COPY Architectures/2_Cloud_Run/templates Architectures/2_Cloud_Run/templates
COPY SharedCore SharedCore

EXPOSE 8080

CMD ["/bin/bash", "-c", "OLLAMA_HOST=0.0.0.0:11434 ollama serve & until curl -sf http://localhost:11434/api/tags > /dev/null; do echo 'Waiting...'; sleep 2; done && cd Architectures/2_Cloud_Run && python3 app.py"]
