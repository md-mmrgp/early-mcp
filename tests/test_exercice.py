import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from serveur_early.exercice import mcp


@pytest.fixture
async def client():
    # Client en mémoire : pas de processus ni de réseau
    async with Client(mcp) as c:
        yield c


async def test_outils_exposes(client):
    noms = {t.name for t in await client.list_tools()}
    assert noms == {"additionner", "heure_actuelle"}


async def test_additionner(client):
    res = await client.call_tool("additionner", {"a": 2, "b": 3})
    assert res.data == 5


async def test_fuseau_invalide(client):
    with pytest.raises(ToolError, match="Fuseau inconnu"):
        await client.call_tool("heure_actuelle", {"fuseau": "Mars/Olympus"})
