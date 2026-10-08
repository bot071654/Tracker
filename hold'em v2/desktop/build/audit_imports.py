"""Static import audit of the packaged entry point (standard library only; no packager needed).

    python desktop/build/audit_imports.py

Follows the project's own modules from desktop/entry.py through every `import` / `from ... import` (including the lazy ones
inside functions, such as `import app` in entry.main), and lists the third-party top-level packages reachable from them.
Each is labelled by how it is imported: PLAIN (module level or inside a function: the build needs it) or GUARDED (inside a
try/except that tolerates it being absent: the build may leave it out).

Exit status 1 if a forbidden package (automation, server stack, dev tools) is reachable through a PLAIN import.
Imports made by name at run time (importlib) cannot be seen statically; the spec lists those as hiddenimports.
"""

import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
ENTRY = os.path.join(ROOT, "desktop", "entry.py")

FORBIDDEN = {"pyautogui", "pygetwindow", "pyscreeze", "pymsgbox", "pytweening", "mouseinfo", "pyperclip", "pynput", "keyboard",
             "fastapi", "starlette", "uvicorn", "pydantic", "pytest", "_pytest", "PIL", "psycopg", "psycopg2"}
# Never followed: not part of the packaged application (dev tools, tests, the server).
NOT_PACKAGED = {"tools", "tests", "backend", "mac", "scripts", "extension", "node_modules"}
RUNTIME_BY_NAME = {
    "pyttsx3.drivers.sapi5 (Windows)": "loaded with importlib by pyttsx3 -> hiddenimports in the spec",
    "pyttsx3.drivers.nsss (macOS, needs pyobjc)": "likewise, added only when pyobjc is installed",
}


def project_module_path(name):
    """File for a project module name like 'recognition.card_recognizer', or None if it is not ours."""
    parts = name.split(".")
    if parts[0] in NOT_PACKAGED:
        return None
    base = os.path.join(ROOT, *parts)
    for candidate in (base + ".py", os.path.join(base, "__init__.py")):
        if os.path.isfile(candidate):
            return candidate
    return None


def guarded_nodes(tree):
    """ids of Import nodes sitting inside a try whose handlers tolerate a missing module."""
    guarded = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            tolerant = any(h.type is None or any(n in ast.dump(h.type) for n in ("ImportError", "ModuleNotFoundError", "Exception"))
                           for h in node.handlers)
            if tolerant:
                for child in node.body:
                    for inner in ast.walk(child):
                        if isinstance(inner, (ast.Import, ast.ImportFrom)):
                            guarded.add(id(inner))
    return guarded


def walk():
    seen, queue = {}, [ENTRY]
    external = {}                                                  # name -> {"plain": bool, "where": set}
    while queue:
        path = queue.pop()
        if path in seen:
            continue
        seen[path] = True
        with open(path, "r", encoding="utf-8") as handle:
            tree = ast.parse(handle.read(), filename=path)
        guarded = guarded_nodes(tree)
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module] + [node.module + "." + alias.name for alias in node.names]
            for name in names:
                top = name.split(".")[0]
                found = project_module_path(name)
                if found:
                    queue.append(found)
                elif top in sys.stdlib_module_names or top in ("desktop", "__future__"):
                    continue
                elif project_module_path(top) is None and top not in NOT_PACKAGED:
                    entry = external.setdefault(top, {"plain": False, "where": set()})
                    entry["plain"] = entry["plain"] or id(node) not in guarded
                    entry["where"].add(os.path.relpath(path, ROOT).replace("\\", "/"))
    return seen, external


def main():
    modules, external = walk()
    print("Project modules reachable from desktop/entry.py: %d" % len(modules))
    print("\nThird-party packages:")
    for name in sorted(external):
        info = external[name]
        print("  %-12s %-8s %s" % (name, "PLAIN" if info["plain"] else "GUARDED", ", ".join(sorted(info["where"]))[:90]))
    print("\nLoaded by name at run time (cannot be seen statically):")
    for item, note in RUNTIME_BY_NAME.items():
        print("  %s: %s" % (item, note))
    bad = sorted(name for name, info in external.items() if name in FORBIDDEN and info["plain"])
    if bad:
        print("\nFAIL: forbidden packages are reachable through plain imports: %s" % ", ".join(bad))
        return 1
    guarded_forbidden = sorted(name for name, info in external.items() if name in FORBIDDEN and not info["plain"])
    print("\nOK: no automation library, server stack or dev tool is imported by the packaged application." +
          (" Guarded and excluded from the build: %s." % ", ".join(guarded_forbidden) if guarded_forbidden else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
