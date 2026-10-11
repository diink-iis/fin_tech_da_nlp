import sys
from pathlib import Path

# тесты запускаются из любого места: корень homework_1 должен быть в sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
