"""Calculs sur les entrées de temps : fonctions pures (pas de réseau, pas de MCP).

Early renvoie des dates « locales » sans fuseau, ex. 2026-01-05T09:00:00.000.
On les manipule donc comme des datetime « naïfs » (sans fuseau).
"""

from collections import defaultdict
from datetime import date, datetime, time, timedelta

FORMAT_EARLY = "%Y-%m-%dT%H:%M:%S.000"


def vers_early(dt: datetime) -> str:
    """datetime -> texte attendu par l'API Early."""
    return dt.strftime(FORMAT_EARLY)


def lundi_de_la_semaine(jour: date, decalage_semaines: int = 0) -> date:
    return jour - timedelta(days=jour.weekday()) + timedelta(weeks=decalage_semaines)


def _intervalle(entree: dict) -> tuple[datetime, datetime]:
    d = entree["duration"]
    return datetime.fromisoformat(d["startedAt"]), datetime.fromisoformat(d["stoppedAt"])


def heures_par_activite(entrees: list[dict]) -> list[dict]:
    """Total d'heures par activité, de la plus grosse à la plus petite."""
    secondes: dict[str, float] = defaultdict(float)
    for e in entrees:
        debut, fin = _intervalle(e)
        secondes[e["activity"]["name"]] += (fin - debut).total_seconds()
    lignes = [{"activite": nom, "heures": round(s / 3600, 2)} for nom, s in secondes.items()]
    return sorted(lignes, key=lambda x: x["heures"], reverse=True)


def trouver_trous(entrees: list[dict], jour: date, debut_journee: time, fin_journee: time,
                  minutes_minimum: int) -> list[tuple[datetime, datetime]]:
    """Créneaux sans aucune entrée dans la fenêtre de travail d'un jour donné."""
    fenetre_debut = datetime.combine(jour, debut_journee)
    fenetre_fin = datetime.combine(jour, fin_journee)

    # 1. On garde les entrées qui touchent la fenêtre, rognées à ses bords, triées.
    occupe = []
    for e in entrees:
        d, f = _intervalle(e)
        d, f = max(d, fenetre_debut), min(f, fenetre_fin)
        if d < f:
            occupe.append((d, f))
    occupe.sort()

    # 2. On parcourt la fenêtre de gauche à droite : tout ce qui n'est pas couvert est un trou.
    trous, curseur = [], fenetre_debut
    for d, f in occupe:
        if d - curseur >= timedelta(minutes=minutes_minimum):
            trous.append((curseur, d))
        curseur = max(curseur, f)
    if fenetre_fin - curseur >= timedelta(minutes=minutes_minimum):
        trous.append((curseur, fenetre_fin))
    return trous


def chevauchements(entrees: list[dict], debut: datetime, fin: datetime) -> list[dict]:
    """Entrées existantes qui touchent [debut, fin[ : Early les découperait ou les supprimerait."""
    return [e for e in entrees if _intervalle(e)[0] < fin and _intervalle(e)[1] > debut]
