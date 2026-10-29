from datetime import date, datetime, time

from serveur_early.analyse import heures_par_activite, heures_par_tag, lundi_de_la_semaine, trouver_trous


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


def avec_tags(nom, debut, fin, *tags):
    e = entree(nom, debut, fin)
    e["note"] = {"text": "x", "tags": [{"id": i, "label": t} for i, t in enumerate(tags, 1)], "mentions": []}
    return e


def test_heures_par_tag_et_detail_par_activite():
    es = [avec_tags("Conseil", "2026-01-05T09:00:00.000", "2026-01-05T11:00:00.000", "Dupont"),
          avec_tags("Admin", "2026-01-05T11:00:00.000", "2026-01-05T11:30:00.000", "Dupont"),
          avec_tags("Conseil", "2026-01-05T14:00:00.000", "2026-01-05T15:00:00.000", "Martin")]
    par_tag, sans_tag = heures_par_tag(es)
    assert par_tag == [
        {"tag": "Dupont", "heures": 2.5, "par_activite": [{"activite": "Conseil", "heures": 2.0},
                                                          {"activite": "Admin", "heures": 0.5}]},
        {"tag": "Martin", "heures": 1.0, "par_activite": [{"activite": "Conseil", "heures": 1.0}]}]
    assert sans_tag == {"heures": 0.0, "par_activite": []}


def test_entree_a_plusieurs_tags_comptee_pour_chacun():
    par_tag, _ = heures_par_tag([avec_tags("Conseil", "2026-01-05T09:00:00.000", "2026-01-05T10:00:00.000",
                                           "Dupont", "audit")])
    assert {t["tag"]: t["heures"] for t in par_tag} == {"Dupont": 1.0, "audit": 1.0}


def test_sans_tag_note_absente_ou_vide():
    sans_note = entree("Admin", "2026-01-05T09:00:00.000", "2026-01-05T09:30:00.000")           # pas de clé note
    note_nulle = {**entree("Admin", "2026-01-05T10:00:00.000", "2026-01-05T10:30:00.000"), "note": None}
    par_tag, sans_tag = heures_par_tag([sans_note, note_nulle])
    assert par_tag == [] and sans_tag == {"heures": 1.0, "par_activite": [{"activite": "Admin", "heures": 1.0}]}
