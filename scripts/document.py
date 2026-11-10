"""Run the bundled WeCom document tools with this Windows Python environment."""
from pathlib import Path
import runpy

if __name__ == '__main__':
    runpy.run_path(str(Path(__file__).resolve().parent / 'runtime' / 'documents' / 'launch.py'), run_name='__main__')
