"""Tests du serveur MCP avec un FAUX Early : aucun appel réseau, aucune vraie clé."""

import json

import httpx
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from serveur_early import serveur
from serveur_early.early_client import EarlyClient

ACTIVITES = {"activities": [{"id": "1", "name": "Conseil", "folderId": "9"}],
             "inactiveActivities": [],
             "archivedActivities": [{"id": "2", "name": "Ancien", "folderId": "9"}]}
ENTREES = {"timeEntries": [
    {"id": "1", "activity": {"id": "1", "name": "Conseil"},
     "duration": {"startedAt": "2026-01-05T09:00:00.000", "stoppedAt": "2026-01-05T12:00:00.000"},
     "note": {"text": "x", "tags": [{"id": 7, "label": "Dupont"}], "mentions": []}}]}


TAGS = {"tags": [{"id": 7, "label": "Dupont"}, {"id": 8, "label": "Martin"}], "mentions": []}
envois: list[dict] = []  # ce que le serveur a envoyé à Early pour créer une entrée


def faux_early(suivi_en_cours=True, appels=None):
    def repondre(requete: httpx.Request) -> httpx.Response:
        if appels is not None:
            appels.append((requete.method, requete.url.path))
        chemin = requete.url.path
        if chemin.endswith("/developer/sign-in"):
            return httpx.Response(200, json={"token": "faux-jeton"})
        assert requete.headers["authorization"] == "Bearer faux-jeton"
        if chemin.endswith("/tags-and-mentions"):
            return httpx.Response(200, json=TAGS)
        if requete.method == "POST" and chemin.endswith("/time-entries"):
            envois.append(json.loads(requete.content))
            return httpx.Response(200, json={"id": "42"})
        if chemin.endswith("/activities"):
            return httpx.Response(200, json=ACTIVITES)
        if chemin.endswith("/tracking"):
            if suivi_en_cours:
                return httpx.Response(200, json={"id": 1, "activity": {"name": "Conseil"},
                                                 "startedAt": "2026-01-05T14:00:00.000"})
            return httpx.Response(404, json={"error": {"message": "no tracking"}})
        if "/time-entries/" in chemin:
            return httpx.Response(200, json=ENTREES)
        return httpx.Response(404)
    return httpx.MockTransport(repondre)


@pytest.fixture
async def client(monkeypatch):
    envois.clear()
    monkeypatch.setattr(serveur, "_client", EarlyClient("k", "s", transport=faux_early()))
    async with Client(serveur.mcp) as c:
        yield c


async def test_seul_creer_entree_temps_ecrit(client):
    outils = {t.name: t for t in await client.list_tools()}
    assert set(outils) == {"lister_activites", "lister_tags_clients", "suivi_en_cours",
                           "temps_de_la_semaine", "trous_a_remplir", "creer_entree_temps"}
    ecrivains = {n for n, t in outils.items() if not t.annotations.read_only_hint}
    assert ecrivains == {"creer_entree_temps"}


async def test_lister_activites(client):
    res = (await client.call_tool("lister_activites")).data
    assert res["actives"] == [{"id": "1", "nom": "Conseil", "dossier_id": "9"}]
    assert res["archivees"][0]["nom"] == "Ancien"


async def test_suivi_en_cours(client):
    res = (await client.call_tool("suivi_en_cours")).data
    assert res["en_cours"] is True and res["activite"] == "Conseil"


async def test_pas_de_suivi_en_cours(monkeypatch):
    monkeypatch.setattr(serveur, "_client", EarlyClient("k", "s", transport=faux_early(suivi_en_cours=False)))
    async with Client(serveur.mcp) as c:
        assert (await c.call_tool("suivi_en_cours")).data == {"en_cours": False}


async def test_trous_a_remplir(client):
    res = (await client.call_tool("trous_a_remplir", {"date_debut": "2026-01-05"})).data
    assert res["jours_avec_trous"] == [{"jour": "2026-01-05",
                                        "trous": [{"de": "12:00", "a": "18:00", "minutes": 360}]}]


async def test_trous_dates_invalides(client):
    with pytest.raises(ToolError, match="Formats attendus"):
        await client.call_tool("trous_a_remplir", {"date_debut": "5 janvier"})


async def test_connexion_faite_une_seule_fois(monkeypatch):
    appels = []
    monkeypatch.setattr(serveur, "_client", EarlyClient("k", "s", transport=faux_early(appels=appels)))
    async with Client(serveur.mcp) as c:
        await c.call_tool("lister_activites")
        await c.call_tool("lister_activites")
    assert [a for a in appels if a[1].endswith("sign-in")] == [("POST", "/api/v4/developer/sign-in")]


async def test_sans_cles_message_clair(monkeypatch):
    monkeypatch.setattr(serveur, "_client", None)
    monkeypatch.delenv("EARLY_API_KEY", raising=False)
    monkeypatch.delenv("EARLY_API_SECRET", raising=False)
    async with Client(serveur.mcp) as c:
        with pytest.raises(ToolError, match="EARLY_API_KEY"):
            await c.call_tool("lister_activites")


async def test_jeton_expire_on_se_reconnecte_une_fois(monkeypatch):
    etat = {"connexions": 0}

    def repondre(requete: httpx.Request) -> httpx.Response:
        if requete.url.path.endswith("/developer/sign-in"):
            etat["connexions"] += 1
            return httpx.Response(200, json={"token": f"jeton-{etat['connexions']}"})
        if requete.headers["authorization"] == "Bearer jeton-1":   # le 1er jeton a « expiré »
            return httpx.Response(401, json={"error": {"message": "expired"}})
        return httpx.Response(200, json=ACTIVITES)

    monkeypatch.setattr(serveur, "_client", EarlyClient("k", "s", transport=httpx.MockTransport(repondre)))
    async with Client(serveur.mcp) as c:
        assert (await c.call_tool("lister_activites")).data["actives"]
    assert etat["connexions"] == 2


# --- creer_entree_temps ---------------------------------------------------------------

LIBRE = {"activite": "conseil", "debut": "2026-01-05T14:00", "fin": "2026-01-05T15:30"}  # après 12:00 : libre


async def test_creation_resout_les_noms_et_construit_la_note(client):
    res = (await client.call_tool("creer_entree_temps", {**LIBRE, "clients": ["dupont"], "note": "Atelier"})).data
    assert res["cree"]["id"] == "42" and res["cree"]["activite"] == "Conseil"
    assert envois == [{"activityId": "1", "startedAt": "2026-01-05T14:00:00.000",
                       "stoppedAt": "2026-01-05T15:30:00.000", "note": {"text": "Atelier <{{|t|7|}}>"}}]


async def test_simulation_n_ecrit_rien(client):
    res = (await client.call_tool("creer_entree_temps", {**LIBRE, "clients": ["Martin"], "simuler": True})).data
    assert res["simulation"] is True and res["serait_cree"]["clients"] == ["Martin"]
    assert envois == []


async def test_client_inconnu_refuse_avec_suggestion(client):
    with pytest.raises(ToolError, match=r"Aucun tag client nommé 'Dupon'.*Dupont"):
        await client.call_tool("creer_entree_temps", {**LIBRE, "clients": ["Dupon"]})
    assert envois == []


async def test_activite_archivee_refusee(client):
    with pytest.raises(ToolError, match="Aucun activité nommé 'Ancien'"):
        await client.call_tool("creer_entree_temps", {**LIBRE, "activite": "Ancien"})


async def test_chevauchement_refuse_par_defaut(client, monkeypatch):
    monkeypatch.setenv("MCP_AUTORISER_ECRASEMENT", "1")  # l'option n'est utilisable qu'ainsi
    chevauche = {**LIBRE, "debut": "2026-01-05T11:00", "fin": "2026-01-05T13:00"}  # touche 09:00-12:00
    with pytest.raises(ToolError, match="chevauche"):
        await client.call_tool("creer_entree_temps", chevauche)
    assert envois == []
    await client.call_tool("creer_entree_temps", {**chevauche, "ecraser_chevauchement": True})
    assert len(envois) == 1


@pytest.mark.parametrize("debut,fin,message", [
    ("2026-01-05T14:00", "2026-01-05T14:00", "au moins 1 minute"),
    ("2026-01-05T14:00", "2026-01-04T14:00", "au moins 1 minute"),
    ("2026-01-05T06:00", "2026-01-06T06:00", "16 h"),
    ("hier", "2026-01-05T14:00", "format attendu"),
])
async def test_durees_et_formats_invalides(client, debut, fin, message):
    with pytest.raises(ToolError, match=message):
        await client.call_tool("creer_entree_temps", {**LIBRE, "debut": debut, "fin": fin})
    assert envois == []


async def test_note_avec_balise_de_tag_refusee(client):
    with pytest.raises(ToolError, match="balise"):
        await client.call_tool("creer_entree_temps", {**LIBRE, "note": "ok <{{|t|8|}}>"})
    assert envois == []


async def test_ecrasement_desactive_par_defaut(client, monkeypatch):
    monkeypatch.delenv("MCP_AUTORISER_ECRASEMENT", raising=False)
    chevauche = {**LIBRE, "debut": "2026-01-05T11:00", "fin": "2026-01-05T13:00", "ecraser_chevauchement": True}
    with pytest.raises(ToolError, match="désactivé"):
        await client.call_tool("creer_entree_temps", chevauche)
    assert envois == []
    monkeypatch.setenv("MCP_AUTORISER_ECRASEMENT", "1")
    await client.call_tool("creer_entree_temps", chevauche)
    assert len(envois) == 1


def test_http_refuse_de_demarrer_sans_hote_public(monkeypatch):
    monkeypatch.delenv("MCP_PUBLIC_HOST", raising=False)
    monkeypatch.setattr(serveur.mcp, "auth", object())  # simule « jeton présent »
    with pytest.raises(SystemExit, match="MCP_PUBLIC_HOST"):
        serveur.lancer_http()


async def test_temps_de_la_semaine_remonte_les_tags(client, monkeypatch):
    monkeypatch.setattr(serveur, "_aujourdhui", lambda: __import__("datetime").date(2026, 1, 7))
    res = (await client.call_tool("temps_de_la_semaine")).data
    assert res["du"] == "2026-01-05" and res["total_heures"] == 3.0
    assert res["par_tag"] == [{"tag": "Dupont", "heures": 3.0, "par_activite": [{"activite": "Conseil", "heures": 3.0}]}]
    assert res["sans_tag"]["heures"] == 0.0
