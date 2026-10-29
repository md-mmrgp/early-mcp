from datetime import date, datetime, time

from serveur_early.analyse import heures_par_activite, lundi_de_la_semaine, trouver_trous


def entree(nom, debut, fin):
    return {"activity": {"name": nom}, "duration": {"startedAt": debut, "stoppedAt": fin}}


def test_heures_par_activite_regroupe_et_trie():
    es = [entree("Conseil", "2026-01-05T09:00:00.000", "2026-01-05T10:30:00.000"),
          entree("Admin", "2026-01-05T11:00:00.000", "2026-01-05T11:30:00.000"),
          entree("Conseil", "2026-01-05T14:00:00.000", "2026-01-05T15:00:00.000")]
    assert heures_par_activite(es) == [{"activite": "Conseil", "heures": 2.5},
                                       {"activite": "Admin", "heures": 0.5}]


def test_lundi_de_la_semaine():
    assert lundi_de_la_semaine(date(2026, 1, 7)) == date(2026, 1, 5)          # mercredi -> lundi
    assert lundi_de_la_semaine(date(2026, 1, 7), -1) == date(2025, 12, 29)    # semaine précédente


def test_trous_dans_la_journee():
    es = [entree("A", "2026-01-05T09:00:00.000", "2026-01-05T10:00:00.000"),
          entree("B", "2026-01-05T12:00:00.000", "2026-01-05T13:00:00.000")]
    trous = trouver_trous(es, date(2026, 1, 5), time(9), time(18), 15)
    assert trous == [(datetime(2026, 1, 5, 10), datetime(2026, 1, 5, 12)),
                     (datetime(2026, 1, 5, 13), datetime(2026, 1, 5, 18))]


def test_journee_vide_est_un_seul_trou():
    assert trouver_trous([], date(2026, 1, 5), time(9), time(18), 15) == [
        (datetime(2026, 1, 5, 9), datetime(2026, 1, 5, 18))]


def test_entrees_qui_se_chevauchent_et_depassent_la_fenetre():
    es = [entree("A", "2026-01-05T08:00:00.000", "2026-01-05T11:00:00.000"),   # commence avant 9h
          entree("B", "2026-01-05T10:00:00.000", "2026-01-05T17:50:00.000")]   # chevauche A
    assert trouver_trous(es, date(2026, 1, 5), time(9), time(18), 15) == []    # reste 10 min < 15


def test_petit_trou_ignore():
    es = [entree("A", "2026-01-05T09:00:00.000", "2026-01-05T10:00:00.000"),
          entree("B", "2026-01-05T10:10:00.000", "2026-01-05T18:00:00.000")]
    assert trouver_trous(es, date(2026, 1, 5), time(9), time(18), 15) == []
