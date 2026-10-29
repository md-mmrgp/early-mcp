"""Serveur MCP pour Early (suivi du temps) : lecture, et écriture encadrée par des garde-fous.

Chaque outil est une fine couche : il récupère les données (early_client),
appelle un calcul (analyse), et renvoie un résultat clair au LLM.
"""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

import os

import re

from serveur_early import analyse, notes, resolution
from serveur_early.early_client import EarlyClient, EarlyError
from serveur_early.securite import verificateur_depuis_env

load_dotenv()  # lit .env s'il existe (ignoré par git) ; sinon variables d'environnement
FUSEAU = ZoneInfo("Europe/Paris")
LECTURE_SEULE = {"readOnlyHint": True}  # dit au client : cet outil ne modifie rien

ECRITURE = {"readOnlyHint": False, "destructiveHint": True}  # peut écraser des entrées si on le force
ECRITURE_SIMPLE = {"readOnlyHint": False, "destructiveHint": False}  # crée ou corrige, sans rien supprimer
MAX_ENTREES_LISTEES = 200
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


def _verifier_note(note: str | None) -> None:
    if note and "<{{" in note:
        # Early lit ces balises pour rattacher des tags/mentions : on ne laisse pas le texte libre en injecter.
        raise ToolError("La note ne peut pas contenir de balise <{{...}}> : utilisez le paramètre clients.")


def _verifier_ecrasement(ecraser: bool) -> None:
    if ecraser and os.environ.get("MCP_AUTORISER_ECRASEMENT") != "1":
        raise ToolError("ecraser_chevauchement est désactivé sur ce serveur (MCP_AUTORISER_ECRASEMENT=1 pour l'activer).")


def _verifier_duree(d: datetime, f: datetime) -> None:
    if f - d < timedelta(minutes=1):
        raise ToolError("La fin doit être au moins 1 minute après le début.")
    if f - d > DUREE_MAX:
        raise ToolError("Durée de plus de 16 h refusée (faute de frappe sur la date ?).")


async def _resoudre_noms(client: EarlyClient, activite: str | None, clients: list[str] | None):
    """Noms -> éléments Early (refus si inconnu ou ambigu). Renvoie (activité ou None, tags ou None)."""
    try:
        trouvee = None
        if activite is not None:
            donnees = await _appel(client.activites())
            trouvee = resolution.trouver(activite, donnees["activities"] + donnees.get("inactiveActivities", []),
                                         "name", "activité")
        tags = None
        if clients is not None:
            tous = (await _appel(client.tags_et_mentions()))["tags"]
            tags = [resolution.trouver(nom, tous, "label", "tag client") for nom in clients]
        return trouvee, tags
    except resolution.ResolutionError as exc:
        raise ToolError(str(exc)) from exc


def _resume_entree(e: dict) -> dict:
    note = e.get("note") or {}
    return {"id": e["id"], "activite": e["activity"]["name"],
            "de": e["duration"]["startedAt"], "a": e["duration"]["stoppedAt"],
            "clients": [t["label"] for t in note.get("tags") or []],
            "note": notes.texte_brut(note.get("text") or "") or None}


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
    _verifier_note(note)
    _verifier_ecrasement(ecraser_chevauchement)
    d, f = _lire_date_heure(debut, "debut"), _lire_date_heure(fin, "fin")
    _verifier_duree(d, f)

    client = get_client()
    # 1. Résoudre les noms -> identifiants (refus si inconnu ou ambigu)
    trouvee, tags_clients = await _resoudre_noms(client, activite, clients or [])

    # 2. Refuser si le créneau écraserait du temps déjà saisi
    existantes = analyse.chevauchements(
        await _appel(client.entrees(analyse.vers_early(d), analyse.vers_early(f))), d, f)
    if existantes and not ecraser_chevauchement:
        lignes = "; ".join(f"{e['activity']['name']} {e['duration']['startedAt'][11:16]}-"
                           f"{e['duration']['stoppedAt'][11:16]}" for e in existantes)
        raise ToolError(f"Ce créneau chevauche des entrées existantes ({lignes}). Early les découperait "
                        "ou les supprimerait. Choisissez un autre créneau, ou ecraser_chevauchement=true.")

    # 3. Construire la note : Early référence les tags par id, au format <{{|t|ID|}}>
    texte = notes.assembler(note or "", [t["id"] for t in tags_clients], [])
    resume = {"activite": trouvee["name"], "de": analyse.vers_early(d), "a": analyse.vers_early(f),
              "clients": [t["label"] for t in tags_clients], "note": note,
              "entrees_ecrasees": len(existantes)}
    if simuler:
        return {"simulation": True, "serait_cree": resume}

    cree = await _appel(client.creer_entree(trouvee["id"], analyse.vers_early(d), analyse.vers_early(f),
                                            texte or None))
    return {"simulation": False, "cree": {"id": cree["id"], **resume}}


@mcp.tool(annotations=LECTURE_SEULE)
async def lister_entrees(date_debut: str, date_fin: str | None = None) -> dict:
    """Liste les entrées de temps d'une période, AVEC leur identifiant (nécessaire pour en modifier une).

    Args:
        date_debut: premier jour, format AAAA-MM-JJ
        date_fin: dernier jour inclus (par défaut le même jour). 31 jours maximum.
    """
    try:
        premier = date.fromisoformat(date_debut)
        dernier = date.fromisoformat(date_fin) if date_fin else premier
    except ValueError as exc:
        raise ToolError("Format attendu : AAAA-MM-JJ.") from exc
    if dernier < premier or (dernier - premier).days > 30:
        raise ToolError("Période invalide : date_fin doit suivre date_debut, 31 jours maximum.")
    entrees = await _appel(get_client().entrees(
        analyse.vers_early(datetime.combine(premier, time(0, 0))),
        analyse.vers_early(datetime.combine(dernier, time(23, 59, 59)))))
    resumes = [_resume_entree(e) for e in entrees[:MAX_ENTREES_LISTEES]]
    return {"entrees": resumes, "tronque": len(entrees) > MAX_ENTREES_LISTEES}


async def _dossier_pour_tag(client: EarlyClient, dossier: str | None) -> dict:
    actifs = [d for d in await _appel(client.dossiers()) if d.get("status", "active") == "active"]
    if dossier is not None:
        try:
            return resolution.trouver(dossier, actifs, "name", "dossier")
        except resolution.ResolutionError as exc:
            raise ToolError(str(exc)) from exc
    if len(actifs) == 1:
        return actifs[0]
    noms = ", ".join(repr(d["name"]) for d in actifs)
    raise ToolError(f"Plusieurs dossiers existent ({noms}) : précisez le paramètre dossier.")


def _verifier_nom_de_tag(nom: str) -> str:
    nom = " ".join(nom.split())
    if not nom or len(nom) > 60:
        raise ToolError("Le nom d'un tag doit faire entre 1 et 60 caractères.")
    if any(c in nom for c in "<>{}|#@"):
        raise ToolError("Le nom d'un tag ne peut pas contenir < > { } | # @.")
    return nom


@mcp.tool(annotations=ECRITURE_SIMPLE)
async def creer_tag(nom: str, dossier: str | None = None, simuler: bool = False) -> dict:
    """Crée un tag (par ex. un nouveau client) dans un dossier Early.

    Refuse un tag déjà existant ou quasi identique (casse, accents, espaces et tirets ignorés :
    « nomadrp » est refusé si « nomad RP » existe) pour ne pas recréer de doublons.

    Args:
        nom: nom du nouveau tag
        dossier: nom du dossier (facultatif s'il n'y en a qu'un)
        simuler: si vrai, vérifie tout et décrit ce qui serait créé, sans rien écrire
    """
    nom = _verifier_nom_de_tag(nom)
    client = get_client()
    existants = (await _appel(client.tags_et_mentions()))["tags"]
    proches = resolution.homonymes(nom, existants, "label")
    if proches:
        raise ToolError("Un tag existe déjà avec ce nom ou un nom quasi identique : "
                        + ", ".join(repr(t["label"]) for t in proches)
                        + ". Utilisez-le plutôt que d'en créer un nouveau.")
    cible = await _dossier_pour_tag(client, dossier)
    if simuler:
        return {"simulation": True, "serait_cree": {"nom": nom, "dossier": cible["name"]}}
    cree = await _appel(client.creer_tag(nom, cible["id"]))
    return {"simulation": False, "cree": {"id": cree["id"], "nom": cree["label"], "dossier": cible["name"]}}


@mcp.tool(annotations=ECRITURE_SIMPLE)
async def renommer_tag(tag: str, nouveau_nom: str, simuler: bool = False) -> dict:
    """Corrige le nom d'un tag existant. Le changement se voit sur TOUTES les entrées qui le portent.

    Refuse un nouveau nom qui ferait doublon avec un autre tag. Ne fusionne pas deux tags.

    Args:
        tag: nom exact du tag actuel (voir lister_tags_clients)
        nouveau_nom: nouveau nom
        simuler: si vrai, vérifie tout et décrit le changement, sans rien écrire
    """
    nouveau_nom = _verifier_nom_de_tag(nouveau_nom)
    client = get_client()
    existants = (await _appel(client.tags_et_mentions()))["tags"]
    try:
        cible = resolution.trouver(tag, existants, "label", "tag")
    except resolution.ResolutionError as exc:
        raise ToolError(str(exc)) from exc
    if cible["label"] == nouveau_nom:
        raise ToolError(f"Le tag s'appelle déjà {nouveau_nom!r}.")
    autres = resolution.homonymes(nouveau_nom, existants, "label", ignorer_id=cible["id"])
    if autres:
        raise ToolError("Ce nom ferait doublon avec : " + ", ".join(repr(t["label"]) for t in autres)
                        + ". Si le tag actuel n'est plus utilisé, supprimez-le directement dans Early (aucun outil "
                        "de suppression ici). S'il porte encore des entrées, il faut d'abord les re-tagger vers "
                        "l'autre tag (pas encore possible ici) : ne le supprimez pas avant.")
    if simuler:
        return {"simulation": True, "serait_renomme": {"de": cible["label"], "en": nouveau_nom}}
    await _appel(client.renommer_tag(cible["id"], nouveau_nom))
    return {"simulation": False, "renomme": {"de": cible["label"], "en": nouveau_nom}}


@mcp.tool(annotations=ECRITURE)
async def modifier_entree_temps(
    entree_id: str,
    activite: str | None = None,
    debut: str | None = None,
    fin: str | None = None,
    clients: list[str] | None = None,
    note: str | None = None,
    simuler: bool = False,
    ecraser_chevauchement: bool = False,
) -> dict:
    """Modifie une entrée de temps existante. Seuls les champs donnés changent ; le reste est conservé.

    Trouver l'identifiant avec lister_entrees. Activité et clients sont donnés par leur NOM.
    `clients` REMPLACE les tags clients de l'entrée (liste vide = les retirer tous) ; les mentions
    déjà présentes sont conservées. Si le nouveau créneau chevauche une AUTRE entrée, l'outil refuse.
    La réponse montre l'état avant et après.

    Args:
        entree_id: identifiant de l'entrée (chiffres, voir lister_entrees)
        activite: nouveau nom d'activité
        debut: nouveau début, AAAA-MM-JJTHH:MM
        fin: nouvelle fin, même format
        clients: nouveaux tags clients (remplace les actuels)
        note: nouveau texte libre (sans balises)
        simuler: si vrai, vérifie tout et montre avant/après, sans rien écrire
        ecraser_chevauchement: laisser Early découper/supprimer les entrées qui se chevauchent
    """
    # L'identifiant est inséré dans l'adresse envoyée à Early : chiffres seulement.
    if not re.fullmatch(r"\d{1,12}", entree_id):
        raise ToolError("entree_id : identifiant numérique attendu (voir lister_entrees).")
    if all(v is None for v in (activite, debut, fin, clients, note)):
        raise ToolError("Rien à modifier : donnez au moins un champ (activite, debut, fin, clients, note).")
    _verifier_note(note)
    _verifier_ecrasement(ecraser_chevauchement)

    client = get_client()
    actuelle = await _appel(client.entree(entree_id))
    d0, f0 = analyse.intervalle(actuelle)
    d = _lire_date_heure(debut, "debut") if debut else d0
    f = _lire_date_heure(fin, "fin") if fin else f0
    _verifier_duree(d, f)
    trouvee, tags = await _resoudre_noms(client, activite, clients)

    corps: dict = {}
    if trouvee is not None:
        corps["activityId"] = trouvee["id"]
    if (d, f) != (d0, f0):
        corps["startedAt"], corps["stoppedAt"] = analyse.vers_early(d), analyse.vers_early(f)
        autres = [e for e in analyse.chevauchements(
            await _appel(client.entrees(analyse.vers_early(d), analyse.vers_early(f))), d, f)
            if str(e["id"]) != entree_id]  # l'entrée elle-même ne compte pas
        if autres and not ecraser_chevauchement:
            lignes = "; ".join(f"{e['activity']['name']} {e['duration']['startedAt'][11:16]}-"
                               f"{e['duration']['stoppedAt'][11:16]}" for e in autres)
            raise ToolError(f"Le nouveau créneau chevauche d'autres entrées ({lignes}). Early les découperait "
                            "ou les supprimerait. Choisissez un autre créneau, ou ecraser_chevauchement=true.")

    avant = _resume_entree(actuelle)
    texte_actuel = (actuelle.get("note") or {}).get("text") or ""
    note_apres = avant["note"]
    if note is not None or clients is not None:
        brut = notes.texte_brut(texte_actuel) if note is None else " ".join(note.split())
        ids = [t["id"] for t in tags] if tags is not None else notes.ids_tags(texte_actuel)
        nouveau_texte = notes.assembler(brut, ids, notes.marqueurs_mentions(texte_actuel))
        corps["note"] = {"text": nouveau_texte} if nouveau_texte else None  # null = supprimer la note
        note_apres = brut or None

    apres = {**avant, "activite": trouvee["name"] if trouvee else avant["activite"],
             "de": analyse.vers_early(d), "a": analyse.vers_early(f),
             "clients": [t["label"] for t in tags] if tags is not None else avant["clients"],
             "note": note_apres}
    if simuler:
        return {"simulation": True, "avant": avant, "apres": apres}
    resultat = await _appel(client.modifier_entree(entree_id, corps))
    return {"simulation": False, "avant": avant, "apres": _resume_entree(resultat)}


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
