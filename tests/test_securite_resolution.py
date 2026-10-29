import pytest

from serveur_early.resolution import ResolutionError, normaliser, trouver
from serveur_early.securite import JetonUnique, verificateur_depuis_env

TAGS = [{"id": 1, "label": "Dupont"}, {"id": 2, "label": "Société Générale"}, {"id": 3, "label": "Dupond SA"}]


def test_normaliser_casse_et_accents():
    assert normaliser("  SOCIÉTÉ   générale ") == "societe generale"


def test_trouver_exact_ignorant_casse_et_accents():
    assert trouver("societe generale", TAGS, "label", "tag")["id"] == 2


def test_trouver_inconnu_propose_des_suggestions():
    with pytest.raises(ResolutionError, match=r"Aucun tag nommé 'Dupon'.*Dupont"):
        trouver("Dupon", TAGS, "label", "tag")


def test_trouver_ambigu_ne_choisit_pas():
    doublons = TAGS + [{"id": 9, "label": "dupont"}]
    with pytest.raises(ResolutionError, match="Plusieurs tags"):
        trouver("Dupont", doublons, "label", "tag")


async def test_jeton_bon_et_mauvais():
    v = JetonUnique("x" * 40)
    assert (await v.verify_token("x" * 40)).client_id == "utilisateur"
    assert await v.verify_token("y" * 40) is None
    assert await v.verify_token("") is None


def test_verificateur_depuis_env(monkeypatch):
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    assert verificateur_depuis_env() is None
    monkeypatch.setenv("MCP_AUTH_TOKEN", "court")
    with pytest.raises(RuntimeError, match="trop court"):
        verificateur_depuis_env()
    monkeypatch.setenv("MCP_AUTH_TOKEN", "z" * 40)
    assert verificateur_depuis_env() is not None
