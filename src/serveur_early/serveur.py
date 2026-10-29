"""Serveur MCP pour Early (suivi du temps) : outils en LECTURE SEULE.

Chaque outil est une fine couche : il récupère les données (early_client),
appelle un calcul (analyse), et renvoie un résultat clair au LLM.
"""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

import os

from serveur_early import analyse, resolution
from serveur_early.early_client import EarlyClient, EarlyError
from serveur_early.securite import verificateur_depuis_env

load_dotenv()  # lit .env s'il existe (ignoré par git) ; sinon variables d'environnement
FUSEAU = ZoneInfo("Europe/Paris")
LECTURE_SEULE = {"readOnlyHint": True}  # dit au client : cet outil ne modifie rien

ECRITURE = {"readOnlyHint": False, "destructiveHint": True}  # peut écraser des entrées si on le force
DUREE_MAX = timedelta(hours=16)  # garde-fou contre une faute de frappe sur la date

mcp = FastMCP(
    "early",
    auth=verificateur_depuis_env(),  # None en local (stdio) ; jeton obligatoire si MCP_AUTH_TOKEN est défini
    instructions="Consulte le suivi du temps Early : activités, tags/clients, suivi en cours, "
                 "heures de la semaine et créneaux de travail sans entrée.",
)

_client: EarlyClient | None = None


def get_client() -> EarlyClient:
    global _client
    if _client is None:
        try:
            _client = EarlyClient.depuis_env()
        except EarlyError as exc:
            raise ToolError(str(exc)) from exc
    return _client


async def _appel(coroutine):
    """Transforme les erreurs Early en ToolError : le LLM reçoit un message exploitable."""
    try:
        return await coroutine
    except EarlyError as exc:
        raise ToolError(str(exc)) from exc


def _aujourdhui() -> date:
    return datetime.now(FUSEAU).date()


@mcp.tool(annotations=LECTURE_SEULE)
async def lister_activites() -> dict:
    """Liste les activités Early (nom, id) pour savoir quoi choisir en saisissant un temps.

    Renvoie les activités actives et archivées. Les noms sont à utiliser tels quels.
    """
    donnees = await _appel(get_client().activites())

    def simplifier(liste):
        return [{"id": a["id"], "nom": a["name"], "dossier_id": a.get("folderId")} for a in liste]

    return {"actives": simplifier(donnees["activities"]),
            "archivees": simplifier(donnees.get("archivedActivities", []))}


@mcp.tool(annotations=LECTURE_SEULE)
async def lister_tags_clients() -> dict:
    """Liste les tags et mentions Early (c'est là que sont rangés les clients, par ex.)."""
    donnees = await _appel(get_client().tags_et_mentions())
    return {"tags": [{"id": t["id"], "nom": t["label"]} for t in donnees["tags"]],
            "mentions": [{"id": m["id"], "nom": m["label"]} for m in donnees["mentions"]]}


@mcp.tool(annotations=LECTURE_SEULE)
async def suivi_en_cours() -> dict:
    """Dit si un chronomètre Early tourne en ce moment, et sur quelle activité."""
    suivi = await _appel(get_client().suivi_en_cours())
    if suivi is None:
        return {"en_cours": False}
    return {"en_cours": True, "activite": suivi["activity"]["name"],
            "depuis": suivi["startedAt"], "note": (suivi.get("note") or {}).get("text")}


@mcp.tool(annotations=LECTURE_SEULE)
async def temps_de_la_semaine(decalage_semaines: int = 0) -> dict:
    """Heures saisies sur une semaine (lundi à dimanche), par activité ET par tag (client).

    `par_tag` donne, pour chaque tag, ses heures et le détail par activité. Une entrée portant
    plusieurs tags est comptée pour chacun : la somme des tags peut dépasser `total_heures`.
    `sans_tag` regroupe les heures saisies sans aucun tag (souvent un oubli à corriger).

    Args:
        decalage_semaines: 0 = cette semaine, -1 = la semaine dernière, etc.
    """
    lundi = analyse.lundi_de_la_semaine(_aujourdhui(), decalage_semaines)
    dimanche = lundi + timedelta(days=6)
    entrees = await _appel(get_client().entrees(
        analyse.vers_early(datetime.combine(lundi, time(0, 0))),
        analyse.vers_early(datetime.combine(dimanche, time(23, 59, 59)))))
    lignes = analyse.heures_par_activite(entrees)
    par_tag, sans_tag = analyse.heures_par_tag(entrees)
    return {"du": lundi.isoformat(), "au": dimanche.isoformat(),
            "total_heures": round(sum(l["heures"] for l in lignes), 2),
            "par_activite": lignes, "par_tag": par_tag, "sans_tag": sans_tag}


@mcp.tool(annotations=LECTURE_SEULE)
async def trous_a_remplir(
    date_debut: str,
    date_fin: str | None = None,
    debut_journee: str = "09:00",
    fin_journee: str = "18:00",
    minutes_minimum: int = 15,
    jours_ouvres_seulement: bool = True,
) -> dict:
    """Repère les créneaux de la journée de travail SANS entrée de temps (à remplir).

    Args:
        date_debut: premier jour, format AAAA-MM-JJ
        date_fin: dernier jour inclus (par défaut, le même jour que date_debut). 31 jours max.
        debut_journee: début de la journée de travail, HH:MM
        fin_journee: fin de la journée de travail, HH:MM
        minutes_minimum: les trous plus courts sont ignorés
        jours_ouvres_seulement: ignore samedi et dimanche
    """
    try:
        premier = date.fromisoformat(date_debut)
        dernier = date.fromisoformat(date_fin) if date_fin else premier
        h_debut, h_fin = time.fromisoformat(debut_journee), time.fromisoformat(fin_journee)
    except ValueError as exc:
        raise ToolError("Formats attendus : dates AAAA-MM-JJ, heures HH:MM.") from exc
    if dernier < premier or (dernier - premier).days > 30:
        raise ToolError("Période invalide : date_fin doit suivre date_debut, 31 jours maximum.")
    if h_fin <= h_debut:
        raise ToolError("fin_journee doit être après debut_journee.")

    entrees = await _appel(get_client().entrees(
        analyse.vers_early(datetime.combine(premier, time(0, 0))),
        analyse.vers_early(datetime.combine(dernier, time(23, 59, 59)))))

    jours = []
    for i in range((dernier - premier).days + 1):
        jour = premier + timedelta(days=i)
        if jours_ouvres_seulement and jour.weekday() >= 5:
            continue
        trous = analyse.trouver_trous(entrees, jour, h_debut, h_fin, minutes_minimum)
        if trous:
            jours.append({"jour": jour.isoformat(),
                          "trous": [{"de": d.strftime("%H:%M"), "a": f.strftime("%H:%M"),
                                     "minutes": int((f - d).total_seconds() // 60)}
                                    for d, f in trous]})
    return {"jours_avec_trous": jours}


def _lire_date_heure(texte: str, champ: str) -> datetime:
    try:
        return datetime.fromisoformat(texte)
    except ValueError as exc:
        raise ToolError(f"{champ} : format attendu AAAA-MM-JJTHH:MM (ex. 2026-01-05T09:00).") from exc


@mcp.tool(annotations=ECRITURE)
async def creer_entree_temps(
    activite: str,
    debut: str,
    fin: str,
    clients: list[str] | None = None,
    note: str | None = None,
    simuler: bool = False,
    ecraser_chevauchement: bool = False,
) -> dict:
    """Crée une entrée de temps dans Early : activité et clients donnés par leur NOM.

    Les noms sont résolus par le serveur (jamais d'identifiant à fournir). Un nom inconnu ou
    ambigu est refusé avec des suggestions. Si le créneau chevauche des entrées existantes,
    l'outil refuse : Early les découperait ou les supprimerait.
    Conseil : appeler d'abord lister_activites et lister_tags_clients pour voir les noms exacts.

    Args:
        activite: nom exact de l'activité (voir lister_activites)
        debut: début, format AAAA-MM-JJTHH:MM (heure locale)
        fin: fin, même format (durée de 1 minute à 16 heures)
        clients: noms des tags clients à associer (voir lister_tags_clients)
        note: texte libre de l'entrée
        simuler: si vrai, vérifie tout et décrit ce qui serait créé, sans rien écrire
        ecraser_chevauchement: laisser Early découper/supprimer les entrées qui se chevauchent
    """
    if note and "<{{" in note:
        # Early lit ces balises pour rattacher des tags/mentions : on ne laisse pas le texte libre en injecter.
        raise ToolError("La note ne peut pas contenir de balise <{{...}}> : utilisez le paramètre clients.")
    if ecraser_chevauchement and os.environ.get("MCP_AUTORISER_ECRASEMENT") != "1":
        raise ToolError("ecraser_chevauchement est désactivé sur ce serveur (MCP_AUTORISER_ECRASEMENT=1 pour l'activer).")
    d, f = _lire_date_heure(debut, "debut"), _lire_date_heure(fin, "fin")
    if f - d < timedelta(minutes=1):
        raise ToolError("La fin doit être au moins 1 minute après le début.")
    if f - d > DUREE_MAX:
        raise ToolError("Durée de plus de 16 h refusée (faute de frappe sur la date ?).")

    client = get_client()
    # 1. Résoudre les noms -> identifiants (refus si inconnu ou ambigu)
    try:
        donnees = await _appel(client.activites())
        trouvee = resolution.trouver(activite, donnees["activities"] + donnees.get("inactiveActivities", []),
                                     "name", "activité")
        tags = (await _appel(client.tags_et_mentions()))["tags"]
        tags_clients = [resolution.trouver(nom, tags, "label", "tag client") for nom in (clients or [])]
    except resolution.ResolutionError as exc:
        raise ToolError(str(exc)) from exc

    # 2. Refuser si le créneau écraserait du temps déjà saisi
    existantes = analyse.chevauchements(
        await _appel(client.entrees(analyse.vers_early(d), analyse.vers_early(f))), d, f)
    if existantes and not ecraser_chevauchement:
        lignes = "; ".join(f"{e['activity']['name']} {e['duration']['startedAt'][11:16]}-"
                           f"{e['duration']['stoppedAt'][11:16]}" for e in existantes)
        raise ToolError(f"Ce créneau chevauche des entrées existantes ({lignes}). Early les découperait "
                        "ou les supprimerait. Choisissez un autre créneau, ou ecraser_chevauchement=true.")

    # 3. Construire la note : Early référence les tags par id, au format <{{|t|ID|}}>
    texte = " ".join(filter(None, [note] + ["<{{|t|" + str(t["id"]) + "|}}>" for t in tags_clients]))
    resume = {"activite": trouvee["name"], "de": analyse.vers_early(d), "a": analyse.vers_early(f),
              "clients": [t["label"] for t in tags_clients], "note": note,
              "entrees_ecrasees": len(existantes)}
    if simuler:
        return {"simulation": True, "serait_cree": resume}

    cree = await _appel(client.creer_entree(trouvee["id"], analyse.vers_early(d), analyse.vers_early(f),
                                            texte or None))
    return {"simulation": False, "cree": {"id": cree["id"], **resume}}


def lancer_http() -> None:
    """Mode hébergé (Docker/Traefik). Refuse de démarrer sans jeton : jamais de serveur ouvert."""
    if mcp.auth is None:
        raise SystemExit("MCP_AUTH_TOKEN absent : refus de démarrer en HTTP sans authentification.")
    hote_public = os.environ.get("MCP_PUBLIC_HOST")  # ex. mcp.example.com
    if not hote_public:
        raise SystemExit("MCP_PUBLIC_HOST absent : refus de démarrer en HTTP sans protection Host/Origin.")
    # 0.0.0.0 : on écoute dans le conteneur ; le port n'est PAS publié, seul Traefik peut l'atteindre.
    mcp.run(transport="http", host="0.0.0.0", port=int(os.environ.get("PORT", "8000")),  # nosec B104
            host_origin_protection=True, allowed_hosts=[hote_public])


if __name__ == "__main__":
    mcp.run()  # transport stdio : Claude Code lance ce programme et lui parle via stdin/stdout
