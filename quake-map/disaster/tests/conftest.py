import sys, pathlib
root = pathlib.Path(__file__).resolve().parent.parent
for d in (root, root / "backend", root / "tests"):
    sys.path.insert(0, str(d))
sys.path.insert(0, str(root / "tools"))
