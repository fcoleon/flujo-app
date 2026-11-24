"""Ejecuta las pruebas Python y JavaScript desde la raíz del proyecto."""
import shutil
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
node = shutil.which('node')
if not node:
    sys.exit('Se necesita Node.js para ejecutar las pruebas JavaScript.')

subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'], cwd=root, check=True)
for test in sorted((root / 'tests').glob('test_*.js')):
    print(f'JavaScript: {test.name}', flush=True)
    subprocess.run([node, str(test)], cwd=root, check=True)
