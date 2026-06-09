"""Script autonome pour initialiser la base SQLite."""
import asyncio
import sys
from pathlib import Path

# Permet l'exécution directe (`python db/init_db.py`) en ajoutant la racine du
# projet au chemin d'import, en plus de `python -m db.init_db`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.cache import init_db  # noqa: E402

if __name__ == "__main__":
    asyncio.run(init_db())
    print("Base de données initialisée : db/scouting.db")
