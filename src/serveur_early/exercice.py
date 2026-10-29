"""Serveur MCP d'exercice : premiers pas avec FastMCP (pas encore d'API externe)."""

from datetime import datetime
from zoneinfo import ZoneInfo

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

mcp = FastMCP("exercice")


@mcp.tool
def additionner(a: float, b: float) -> float:
    """Additionne deux nombres.

    Args:
        a: premier nombre
        b: second nombre
    """
    return a + b


@mcp.tool
def heure_actuelle(fuseau: str = "Europe/Paris") -> str:
    """Donne la date et l'heure actuelles dans un fuseau horaire IANA (ex. Europe/Paris)."""
    try:
        return datetime.now(ZoneInfo(fuseau)).isoformat(timespec="seconds")
    except Exception as exc:
        # ToolError : message clair renvoyé au LLM pour qu'il corrige son appel
        raise ToolError(f"Fuseau inconnu : {fuseau!r}. Exemple valide : 'Europe/Paris'.") from exc


if __name__ == "__main__":
    mcp.run()  # transport stdio par défaut
