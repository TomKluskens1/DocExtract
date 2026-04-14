# Document Extraction Tester Framework

Framework leggero e flessibile per testare strategie di estrazione dati da PDF con LLM configurabili.

## 🚀 Funzionalità

- **Estrattori PDF diversificati**: Testo semplice, XML strutturato, dizionario blocchi, OCR standard, OCR con tabelle
- **Provider LLM configurabili**: OpenAI, Gemini, Azure OpenAI, endpoint custom OpenAI-compatibili
- **Modelli dinamici**: Carica modelli Pydantic da file Python (modello.py, modello1.py, etc.)
- **Scoring automatico**: Confronta risultati e identifica combinazioni più accurate
- **Ground Truth**: Salva JSON validati direttamente nella cartella Test/
- **Web UI**: Interfaccia per configurare test e visualizzare risultati
- **Configurazione .env**: Tutte le API key e URL in un unico file

## 📁 Struttura

```
extraction_framework/
├── extractors/           # Estrattori PDF (PyPDF2, pdfplumber, PyMuPDF, pypdfium2)
├── llm_providers/        # Provider LLM (OpenAI, Anthropic, Gemini, Ollama)
├── web_ui/              # Interfaccia web Flask
├── results/             # Risultati test (generati automaticamente)
├── ground_truth/        # Dati validati di riferimento
├── preselection.py      # Sistema pre-selezione con LLM piccolo
├── scoring.py           # Sistema scoring e comparazione
├── ground_truth.py      # Gestione ground truth
└── test_runner.py       # Runner principale dei test
```

## 🔧 Installazione

### 1. Installa dipendenze

```powershell
cd extraction_framework
pip install -r requirements.txt
```

### 2. Configura API Keys (opzionale)

Crea un file `.env` nella root del progetto.

#### Configurazione JSON

Configura tutti i provider in una sola variabile `LLM_PROVIDERS` (JSON su singola riga):

```env
# Esempio con più provider (su singola riga nel file .env)
LLM_PROVIDERS={"openai": {"api_key": "sk-...", "models": ["gpt-4o", "gpt-4o-mini"]}, "gemini": {"api_key": "AIza...", "models": ["gemini-1.5-pro", "gemini-1.5-flash"]}, "azure": {"api_key": "your-azure-key", "base_url": "https://your-resource.openai.azure.com/", "models": ["gpt-4", "gpt-35-turbo"]}, "lmstudio": {"base_url": "http://localhost:1234/v1", "models": ["local-model"]}, "ollama": {"base_url": "http://localhost:11434/v1", "models": ["llama3", "mistral"]}}
```

**Vantaggi della configurazione JSON:**
- ✅ Configurazione centralizzata in una sola variabile
- ✅ Supporto illimitato per provider e modelli
- ✅ Web UI popola automaticamente i dropdown
- ✅ Facile condivisione tra ambienti

### 3. (Opzionale) Installa Ollama per modelli locali

Per usare modelli locali gratuitamente:

1. Installa Ollama: https://ollama.ai
2. Scarica un modello: `ollama pull llama3`
3. Aggiungi alla configurazione JSON:
```env
"ollama": {
  "base_url": "http://localhost:11434/v1",
  "models": ["llama3", "mistral", "gemma2:9b"]
}
```

## 🎯 Utilizzo

### Metodo 1: Interfaccia Web (Consigliato)

```powershell
cd extraction_framework\web_ui
python app.py
```

Apri il browser su `http://localhost:5000`

#### Funzionalità Web UI:

1. **⚙️ Configurazione**: Seleziona PDF, estrattori, LLM e opzioni
2. **📊 Risultati**: Visualizza risultati di estrazione
3. **🔍 Confronto**: Compara risultati tra diverse combinazioni
4. **✅ Validazione**: Visualizza PDF e valida dati estratti

### Metodo 2: Script Python

```python
from pathlib import Path
from extraction_framework.test_runner import TestRunner

# Inizializza runner
runner = TestRunner()

# Esegui test singolo
result = runner.run_extraction(
    pdf_path=Path("Test/bolletta_ee_cenpi/24-09-grandi.pdf"),
    extractor_name="PyMuPDF",
    llm_provider="openai",
    llm_model="gpt-4o",
    use_preselection=True
)

print(f"Successo: {result.success}")
print(f"Tempo: {result.extraction_time:.2f}s")
print(f"Dati estratti: {result.extracted_data}")
```

### Metodo 3: Test Suite Completa

```python
from pathlib import Path
from extraction_framework.test_runner import TestRunner

runner = TestRunner()

# Trova tutti i PDF
test_dir = Path("Test/bolletta_ee_cenpi")
pdf_files = list(test_dir.glob("*.pdf"))

# Configura LLM da testare
llm_configs = [
    {"provider": "openai", "model": "gpt-4o"},
    {"provider": "anthropic", "model": "claude-3-5-sonnet-20241022"},
    {"provider": "gemini", "model": "gemini-1.5-pro"},
]

# Esegui suite completa
results = runner.run_test_suite(
    pdf_files=pdf_files,
    extractors=["PyMuPDF", "pdfplumber"],
    llm_configs=llm_configs,
    use_preselection=True
)
```

## 📊 Sistema di Scoring

Il framework confronta automaticamente i risultati:

- **Agreement Score**: Conta quanti modelli hanno estratto lo stesso valore
- **Confidence**: Percentuale di accordo (agreement_count / total_models)
- **Best Extraction**: Identifica la combinazione con più alta confidenza media
- **Consensus Data**: Genera dati consensuali da tutte le estrazioni

### Interpretazione Confidence:

- 🟢 **≥80%**: Alta confidenza, dato molto probabilmente corretto
- 🟡 **50-79%**: Media confidenza, verificare manualmente
- 🔴 **<50%**: Bassa confidenza, probabile errore o ambiguità

## ✅ Ground Truth e Validazione

### Salvare Ground Truth:

1. Via Web UI: Tab "Validazione" → Seleziona risultato → "Salva come Ground Truth"
2. Via codice:

```python
from extraction_framework.ground_truth import GroundTruthManager

gt_manager = GroundTruthManager(Path("extraction_framework/ground_truth"))

# Salva ground truth
gt_manager.save_ground_truth(
    pdf_file="Test/bolletta_ee_cenpi/24-09-grandi.pdf",
    data={"consumi": [...]},
    validated_by="Riccardo",
    notes="Verificato manualmente"
)
```

### Validare Estrazione:

```python
# Valida contro ground truth
report = gt_manager.validate_extraction(
    extracted_data=result.extracted_data,
    pdf_file="Test/bolletta_ee_cenpi/24-09-grandi.pdf"
)

print(f"Accuracy: {report['accuracy']:.1%}")
print(f"Matches: {len(report['matches'])}")
print(f"Mismatches: {len(report['mismatches'])}")
```

## 🔍 Estrattori PDF Disponibili

| Estrattore | Pro | Contro | Raccomandato per |
|-----------|-----|--------|------------------|
| **PyMuPDF** | Veloce, accurato | Dipendenze pesanti | Uso generale |
| **pdfplumber** | Ottimo per tabelle | Più lento | PDF con tabelle |
| **PyPDF2** | Leggero, semplice | Meno accurato | PDF semplici |
| **pypdfium2** | Basato su PDFium | Meno testato | Alternative |

## 🤖 Provider LLM Supportati

| Provider | Modelli | Costo | Note |
|----------|---------|-------|------|
| **OpenAI** | gpt-4o, gpt-4o-mini | $$$ | Più accurato |
| **Anthropic** | claude-3-5-sonnet | $$$ | Ottimo per documenti |
| **Gemini** | gemini-1.5-pro | $$ | Buon rapporto qualità/prezzo |
| **Ollama** | gemma2:9b, llama3.2 | Gratis | Locale, per pre-selezione |

## 💡 Best Practices

1. **Pre-selezione**: Attivala per documenti lunghi (>10 pagine) per ridurre costi e tempo
2. **Test multipli**: Esegui sempre più combinazioni per avere consenso
3. **Ground Truth**: Valida manualmente almeno 1-2 documenti per tipo
4. **PyMuPDF**: Usa come default, è il più bilanciato
5. **GPT-4o**: Usa per massima accuratezza quando il costo non è problema

## 🐛 Troubleshooting

### Errore "extractor not available"
```powershell
pip install PyPDF2 pdfplumber pymupdf pypdfium2
```

### Errore "provider not available"
```powershell
pip install openai anthropic google-generativeai ollama
```

### Ollama non funziona
1. Verifica che Ollama sia in esecuzione: `ollama list`
2. Scarica il modello: `ollama pull gemma2:9b`

### Web UI non si apre
```powershell
pip install flask flask-cors
cd extraction_framework\web_ui
python app.py
```

## 📝 Esempio Completo

```python
from pathlib import Path
from extraction_framework.test_runner import TestRunner
from extraction_framework.scoring import ResultScorer

# Setup
runner = TestRunner()
scorer = ResultScorer(Path("extraction_framework/results"))

# Test PDF
pdf = Path("Test/bolletta_ee_cenpi/24-09-grandi.pdf")

# Configurazioni da testare
configs = [
    ("PyMuPDF", "openai", "gpt-4o"),
    ("pdfplumber", "openai", "gpt-4o"),
    ("PyMuPDF", "gemini", "gemini-1.5-pro"),
]

# Esegui test
results = []
for extractor, provider, model in configs:
    result = runner.run_extraction(pdf, extractor, provider, model)
    results.append(result)
    scorer.save_result(result)

# Confronta
comparison = scorer.compare_results(results)
print(f"\nMigliore combinazione: {comparison.best_extraction.extractor_name} + "
      f"{comparison.best_extraction.llm_provider}")
print(f"Accuracy media: {comparison.avg_extraction_time:.2f}s")
```

## 📦 Output

I risultati vengono salvati in:
- `extraction_framework/results/`: JSON con tutti i risultati
- `extraction_framework/ground_truth/`: Dati validati di riferimento

## 🤝 Contribuire

Per aggiungere nuovi estrattori o provider:

1. **Nuovo estrattore**: Crea classe in `extractors/` estendendo `BaseExtractor`
2. **Nuovo provider**: Crea classe in `llm_providers/` estendendo `BaseLLMProvider`
3. Aggiungi al registry in `__init__.py`

## 📄 Licenza

MIT License
