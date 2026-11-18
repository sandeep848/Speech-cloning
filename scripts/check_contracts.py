"""Check published Python signatures and request schemas without loading models.

Run from the repository root. To approve an intentional breaking change, update
its versioned baseline with --write and obtain contract-owner review.
"""
import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "backend/app/contracts/python.json"
FILES = ["backend/backend.py", "backend/models/__init__.py",
         "backend/models/pacing.py", "backend/evaluator.py",
         *[f"backend/models/{name}/model.py" for name in ("qwen3", "cosyvoice3", "xtts_v2")],
         "backend/app/security/session.py", "backend/app/db/connection.py"]

def snapshot():
    result = {}
    for path in FILES:
        tree = ast.parse((ROOT / path).read_text())
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
                result[f"{path}:{node.name}"] = {
                    "arguments": ast.dump(node.args, include_attributes=False),
                    "returns": ast.dump(node.returns) if node.returns else None,
                    "async": isinstance(node, ast.AsyncFunctionDef),
                    "decorators": [ast.dump(x) for x in node.decorator_list],
                }
            elif isinstance(node, ast.ClassDef):
                fields = [ast.dump(x) for x in node.body if isinstance(x, ast.AnnAssign)]
                methods = {x.name: ast.dump(x.args) for x in node.body
                           if isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef)) and not x.name.startswith("_")}
                result[f"{path}:{node.name}"] = {"fields": fields, "methods": methods}
    return result

def main():
    current = snapshot()
    if sys.argv[1:] == ["--write"]:
        BASELINE.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n")
        return
    expected = json.loads(BASELINE.read_text())
    changed = [key for key, value in expected.items() if current.get(key) != value]
    if changed:
        raise SystemExit("Breaking public contracts: " + ", ".join(changed))
    print(f"Validated {len(expected)} protected signatures, routes and request classes")

if __name__ == "__main__":
    main()
