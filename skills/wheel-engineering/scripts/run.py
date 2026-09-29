"""Portable, stdlib-only adapter to an explicitly configured WheelCAM checkout."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--runtime', default=os.getenv('WHEEL_ENGINEERING_RUNTIME'))
    p.add_argument('--python', dest='python', default=os.getenv('WHEEL_ENGINEERING_PYTHON'))
    p.add_argument('command', choices=['doctor', 'photo', 'text', 'chain', 'report'])
    p.add_argument('arguments', nargs=argparse.REMAINDER)
    a = p.parse_args(argv)
    if not a.runtime:
        p.error('Set WHEEL_ENGINEERING_RUNTIME or --runtime to the runtime checkout')
    root = Path(a.runtime).expanduser().resolve()
    required = ['services/wheelcam/wheel_skill.py', 'services/wheelcam/text_wheel.py', 'scripts/demo_chain.py']
    if not all((root / f).is_file() for f in required):
        p.error('Runtime checkout is incomplete or incompatible')
    executable = Path(a.python).expanduser().absolute() if a.python else root / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not executable.is_file():
        p.error('Runtime Python is missing; install runtime dependencies or set --python')
    env = os.environ.copy()
    env['PYTHONPATH'] = str(root / 'services') + (os.pathsep + env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
    args = a.arguments[1:] if a.arguments[:1] == ['--'] else a.arguments
    if a.command == 'doctor':
        probe = "import sys,json; import wheelcam.wheel_skill, wheelcam.text_wheel, cadquery, manifold3d; print(json.dumps({'runtime_imports':'ok','python':sys.version.split()[0]}))"
        return subprocess.run([str(executable), '-c', probe], env=env).returncode
    if a.command == 'report':                    # rewrite REPORT.md for an existing chain run; creates no run
        return subprocess.run([str(executable), '-m', 'wheelcam.delivery_report', *args], env=env).returncode
    # Resolve input/output paths in the caller's cwd, never relative to the installed skill.
    if '--help' not in args and '-h' not in args:
        output = next((v.split('=', 1)[1] for v in args if v.startswith('--out=')), None)
        if '--out' in args:
            i = args.index('--out')
            if i + 1 < len(args):
                output = args[i + 1]
        if not output:
            p.error('Provide --out with a new or empty output directory')
        dest = Path(output).expanduser()
        if dest.exists() and (not dest.is_dir() or any(dest.iterdir())):
            p.error('Output must be a new or empty directory; existing results are preserved')
    target = {'photo': ['-m', 'wheelcam.wheel_skill'], 'text': ['-m', 'wheelcam.text_wheel'],
              'chain': [str(root / 'scripts/demo_chain.py')]}[a.command]
    return subprocess.run([str(executable), *target, *args], env=env).returncode


if __name__ == '__main__':
    sys.exit(main())
