"""
Gedeelde helper voor de testsuite: haalt losse functies/constanten uit
SchilderTool1.py via AST-extractie en exec't ze in een geïsoleerd namespace.

SchilderTool1.py is een Streamlit-script dat bij een gewone 'import' meteen
UI-rendercode zou uitvoeren (de 'if selected == "Dashboard":'-achtige pagina-
blokken staan los op moduleniveau, niet achter 'if __name__'). AST-extractie
laat de rekenkern/persistentielogica testbaar zijn zonder een draaiende
Streamlit-app of browser nodig te hebben — hetzelfde patroon dat handmatig is
gebruikt om eerdere fixes in deze codebase te verifiëren, nu vastgelegd als
herbruikbare testinfrastructuur.

Elke aanroeper geeft zelf op welke namen hij nodig heeft; dit bestand kent geen
vaste lijst, zodat het zelf geen bron van drift wordt.
"""
import ast
from pathlib import Path

SCHILDERTOOL_PAD = Path(__file__).resolve().parent.parent / "SchilderTool1.py"


def extract(*names, extra_globals=None):
    """Compileer + exec de opgegeven top-level functies/constanten uit
    SchilderTool1.py (ongewijzigd overgenomen uit de echte bron) in een vers
    namespace. Retourneert dat namespace-dict — test-code haalt er bv.
    ns["bereken_onderdeel"] uit."""
    src = SCHILDERTOOL_PAD.read_text(encoding="utf-8")
    tree = ast.parse(src)
    wanted = set(names)

    nodes = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in wanted:
            nodes.append(node)
        elif isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if any(t in wanted for t in targets):
                nodes.append(node)

    gevonden = set()
    for n in nodes:
        if isinstance(n, ast.FunctionDef):
            gevonden.add(n.name)
        else:
            gevonden.update(t.id for t in n.targets if isinstance(t, ast.Name))
    ontbreekt = wanted - gevonden
    if ontbreekt:
        raise AssertionError(
            "Niet gevonden in SchilderTool1.py (hernoemd/verwijderd? test moet "
            f"dan mee-updaten): {sorted(ontbreekt)}"
        )

    module_ast = ast.Module(body=nodes, type_ignores=[])
    code = compile(module_ast, "<SchilderTool1-extract>", "exec")
    ns = {"__file__": str(SCHILDERTOOL_PAD)}
    ns.update(extra_globals or {})
    exec(code, ns)
    return ns


class FakeSessionState(dict):
    """Minimale st.session_state-vervanger: attribuut- én dict-toegang, zoals
    de echte Streamlit SessionStateProxy."""
    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError:
            raise AttributeError(k)

    def __setattr__(self, k, v):
        self[k] = v


class FakeSt:
    """Minimale 'streamlit'-vervanger — alleen wat de rekenkern nodig heeft."""
    def __init__(self, **session_state_kwargs):
        self.session_state = FakeSessionState(**session_state_kwargs)
