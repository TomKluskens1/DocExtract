# Modelkeuze PWA/Edge Architectuur — Gemma 3 1B vs Gemma 3n E2B

Voor de PWA/edge architectuur werd Gemma 3 1B (q4 kwantisatie) geselecteerd in plaats van
Gemma 3n E2B. Hoewel E2B hogere extractienauwkeurigheid biedt, overschrijdt het de praktische
geheugenlimieten van mobiele apparaten — de primaire doelomgeving voor edge inferentie.
Op smartphones wordt GPU-geheugen gedeeld met het systeem, waardoor 0.5–1 GB (1B q4)
haalbaar is maar 2–3 GB (E2B q4) niet.
