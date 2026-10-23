"""
Validatore formale dell'infrastruttura sperimentale della tesi.
Tutti i controlli formali sono modularizzati nel package validation/.
"""
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

try:
    import jsonschema
    import referencing
except ImportError as e:
    print(f"\n[ERRORE AMBIENTE] Dipendenza mancante: {e}")
    print("Assicurati di eseguire il validatore nell'ambiente virtuale dedicato (.venv):")
    print("  .\\.venv\\Scripts\\python.exe artifacts\\experiments\\validate_experiments.py")
    print("Oppure installa le dipendenze richieste:")
    print("  pip install jsonschema referencing pytest\n")
    sys.exit(1)

from validation.cli import main

if __name__ == "__main__":
    sys.exit(main())
