from pathlib import Path
import os
import subprocess
import sys
root=Path(__file__).resolve().parent.parent
os.chdir(root)
for test in sorted((root/'tests').glob('test_*.py')):
    print('Running',test.name,flush=True)
    subprocess.run([sys.executable,str(test)],check=True)
