#!/bin/bash

# 1. Start de Ollama server op de achtergrond
echo "Starting Ollama server..."
OLLAMA_HOST=0.0.0.0:11434 ollama serve &

# Wacht tot Ollama API beschikbaar is
echo "Waiting for Ollama to initialize..."
sleep 5

# (Optioneel) Zorg dat het model lokaal beschikbaar is
# Omdat Google Cloud Run stateless is, zou je het model idealiter 
# in een Google Cloud Storage volume mounten of in de Docker image bakken.
# Voor deze PoC checken we of we hem moeten pullen:
# ollama pull gemma:latest 

# 2. Start de Flask backend
echo "Starting Flask Application..."
python3 app.py
