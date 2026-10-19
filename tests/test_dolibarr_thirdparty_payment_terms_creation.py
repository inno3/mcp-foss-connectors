"""Défaut configurable à la création d'un tiers, avec transport simulé et compté."""

import json
import os
from copy import deepcopy

import httpx
import pytest

os.environ.setdefault("DOLIBARR_URL", "https://erp.example.test")
os.environ.setdefault("DOLIBARR_API_KEY", "test-key")

from dolibarr_mcp import server

VARIABLE = "DOLIBARR_THIRDPARTY_PAYMENT_TERM"


def erreur_http(statut):
    reponse = httpx.Response(statut, request=httpx.Request("GET", "https://erp.example.test"))
    return httpx.HTTPStatusError("Lecture refusée", request=reponse.request, response=reponse)


@pytest.fixture
def api(monkeypatch):
    monkeypatch.delenv(VARIABLE, raising=False)
    monkeypatch.setattr(server, "_PAYMENT_TERMS_CACHE", None)
    etat = {
        "doublons": [],
        "tiers": {"id": "37", "cond_reglement_id": "2"},
        "dictionnaire": [
            {"id": "2", "code": "30D", "label": "À 30 jours"},
            {"id": "3", "code": "30DENDMONTH", "libelle": "À 30 jours fin de mois"},
        ],
        "appels": [],
        "ecritures": [],
    }

    async def lire(route, params=None):
        etat["appels"].append(("GET", route))
        routes = {
            "thirdparties": "doublons",
            "thirdparties/37": "tiers",
            server.PAYMENT_TERMS_ENDPOINT: "dictionnaire",
        }
        assert route in routes, f"Lecture inattendue : {route}"
        resultat = etat[routes[route]]
        if isinstance(resultat, Exception):
            raise resultat
        return deepcopy(resultat)

    async def ecrire(route, data):
        etat["appels"].append(("POST", route))
        etat["ecritures"].append(deepcopy(data))
        assert route == "thirdparties"
        return 37

    monkeypatch.setattr(server, "api_get", lire)
    monkeypatch.setattr(server, "api_post", ecrire)
    return etat


async def creer(**options):
    return json.loads(await server.dolibarr_create_thirdparty(
        **{"name": "Client de test", "type_tiers": "customer", **options},
    ))


def creation(api):
    assert len(api["ecritures"]) == 1
    assert "mode_reglement_id" not in api["ecritures"][0]
    return api["ecritures"][0]


@pytest.mark.parametrize("reglage", ["30D", "30d", " 30d "])
@pytest.mark.parametrize("options", [{}, {"cond_reglement_id": None}])
async def test_code_configure_resolu_et_verifie(api, monkeypatch, reglage, options):
    monkeypatch.setenv(VARIABLE, reglage)
    resultat = await creer(**options)
    assert creation(api)["cond_reglement_id"] == 2
    assert resultat["success"] is True
    assert resultat["payment_terms_applied"] == {
        "cond_reglement_id": 2, "code": "30D", "label": "À 30 jours",
        "source": "configuration", "verified": True, "error": None,
    }
    assert api["appels"] == [
        ("GET", "thirdparties"), ("GET", server.PAYMENT_TERMS_ENDPOINT),
        ("POST", "thirdparties"), ("GET", "thirdparties/37"),
    ]


@pytest.mark.parametrize("dictionnaire", [[], {"error": "illisible"}, erreur_http(403)])
async def test_identifiant_configure_sans_dictionnaire_utilisable(api, monkeypatch, dictionnaire):
    monkeypatch.setenv(VARIABLE, "2")
    api["dictionnaire"] = dictionnaire
    resultat = await creer()
    assert creation(api)["cond_reglement_id"] == 2
    assert resultat["success"] is True
    condition = resultat["payment_terms_applied"]
    assert condition["source"] == "configuration"
    assert condition["verified"] is True
    assert condition["code"] is condition["label"] is None
    if dictionnaire:
        assert condition["error"]


@pytest.mark.parametrize("reglage", ["30D", "INCONNU"])
async def test_parametre_prioritaire_sur_configuration(api, monkeypatch, reglage):
    monkeypatch.setenv(VARIABLE, reglage)
    api["tiers"]["cond_reglement_id"] = "3"
    resultat = await creer(cond_reglement_id=3)
    assert creation(api)["cond_reglement_id"] == 3
    assert resultat["payment_terms_applied"] == {
        "cond_reglement_id": 3, "code": "30DENDMONTH", "label": "À 30 jours fin de mois",
        "source": "parametre", "verified": True, "error": None,
    }


async def test_parametre_sans_configuration_ni_dictionnaire(api):
    api["dictionnaire"] = erreur_http(403)
    api["tiers"]["cond_reglement_id"] = "999"
    resultat = await creer(cond_reglement_id=999)
    assert creation(api)["cond_reglement_id"] == 999
    assert resultat["success"] is True
    condition = resultat["payment_terms_applied"]
    assert condition["source"] == "parametre"
    assert condition["verified"] is True
    assert "403" in condition["error"]


@pytest.mark.parametrize("reglage", [None, "", "   "])
async def test_sans_reglage_conserve_payload_reponse_et_appels_historiques(api, monkeypatch, reglage):
    if reglage is not None:
        monkeypatch.setenv(VARIABLE, reglage)
    resultat = await creer()
    assert creation(api) == {
        "name": "Client de test", "client": "1", "fournisseur": "0",
        "country_code": "FR", "status": "1",
    }
    assert resultat.pop("payment_terms_applied") == {
        "cond_reglement_id": None, "code": None, "label": None,
        "source": "aucune", "verified": None, "error": None,
    }
    assert resultat == {
        "success": True, "thirdparty_id": 37, "name": "Client de test", "type": "customer",
        "message": "Tiers 'Client de test' créé avec succès.",
    }
    assert api["appels"] == [("GET", "thirdparties"), ("POST", "thirdparties")]


@pytest.mark.parametrize("reglage", ["INCONNU", "0", "-2", "2.5"])
async def test_configuration_invalide_ne_bloque_pas_creation(api, monkeypatch, reglage):
    monkeypatch.setenv(VARIABLE, reglage)
    resultat = await creer()
    assert "cond_reglement_id" not in creation(api)
    assert resultat["success"] is True
    condition = resultat["payment_terms_applied"]
    assert condition["cond_reglement_id"] is condition["code"] is condition["label"] is None
    assert condition["source"] == "aucune"
    assert condition["verified"] is None
    assert VARIABLE in condition["error"]


@pytest.mark.parametrize("dictionnaire", [
    erreur_http(403), {"error": "illisible"}, [{"code": "30D", "id": "0"}],
])
async def test_code_non_resolu_ne_bloque_pas_creation(api, monkeypatch, dictionnaire):
    monkeypatch.setenv(VARIABLE, "30D")
    api["dictionnaire"] = dictionnaire
    resultat = await creer()
    assert "cond_reglement_id" not in creation(api)
    assert resultat["success"] is True
    assert resultat["payment_terms_applied"]["verified"] is None
    assert resultat["payment_terms_applied"]["error"]


@pytest.mark.parametrize("type_tiers,client,fournisseur", [
    ("prospect", "2", "0"), ("customer+supplier", "1", "1"),
])
async def test_defaut_pour_prospect_et_client_fournisseur(api, monkeypatch, type_tiers, client, fournisseur):
    monkeypatch.setenv(VARIABLE, "30D")
    resultat = await creer(type_tiers=type_tiers)
    payload = creation(api)
    assert payload["client"] == client
    assert payload["fournisseur"] == fournisseur
    assert payload["cond_reglement_id"] == 2
    assert resultat["payment_terms_applied"]["verified"] is True


@pytest.mark.parametrize("options", [{}, {"cond_reglement_id": 3}])
async def test_fournisseur_seul_sans_condition_ni_lecture_supplementaire(api, monkeypatch, options):
    monkeypatch.setenv(VARIABLE, "30D")
    resultat = await creer(type_tiers="supplier", **options)
    payload = creation(api)
    assert payload["client"] == "0"
    assert payload["fournisseur"] == "1"
    assert "cond_reglement_id" not in payload
    assert resultat["payment_terms_applied"] == {
        "cond_reglement_id": None, "code": None, "label": None,
        "source": "non_applicable", "verified": None, "error": None,
    }
    assert api["appels"] == [("GET", "thirdparties"), ("POST", "thirdparties")]


@pytest.mark.parametrize("options", [{}, {"cond_reglement_id": 3}])
async def test_doublon_arrete_avant_dictionnaire_et_creation(api, monkeypatch, options):
    monkeypatch.setenv(VARIABLE, "30D")
    api["doublons"] = [{"id": "12", "name": "Client de test", "email": "client@example.test"}]
    resultat = await creer(**options)
    assert resultat["success"] is False
    assert resultat["error"] == "duplicate"
    assert resultat["existing_thirdparties"] == api["doublons"]
    assert api["appels"] == [("GET", "thirdparties")]
    assert api["ecritures"] == []


async def test_force_conserve_contournement_anti_doublon(api, monkeypatch):
    monkeypatch.setenv(VARIABLE, "30D")
    resultat = await creer(force=True)
    assert creation(api)["cond_reglement_id"] == 2
    assert resultat["payment_terms_applied"]["verified"] is True
    assert ("GET", "thirdparties") not in api["appels"]


async def test_recherche_doublon_en_echec_reste_tolerante(api, monkeypatch):
    monkeypatch.setenv(VARIABLE, "30D")
    api["doublons"] = erreur_http(404)
    resultat = await creer()
    assert creation(api)["cond_reglement_id"] == 2
    assert resultat["success"] is True
    assert resultat["payment_terms_applied"]["verified"] is True


@pytest.mark.parametrize("tiers", [
    erreur_http(403), None, [], {"error": "illisible"},
    {"id": "38", "cond_reglement_id": "2"},
])
async def test_relecture_impossible_ne_transforme_pas_creation_en_echec(api, monkeypatch, tiers):
    monkeypatch.setenv(VARIABLE, "30D")
    api["tiers"] = tiers
    resultat = await creer()
    assert creation(api)["cond_reglement_id"] == 2
    assert resultat["success"] is True
    assert resultat["thirdparty_id"] == 37
    condition = resultat["payment_terms_applied"]
    assert condition["source"] == "configuration"
    assert condition["cond_reglement_id"] == 2
    assert condition["verified"] is None
    assert condition["error"]
    assert api["appels"].count(("GET", "thirdparties/37")) == 1


@pytest.mark.parametrize("condition", [
    {"cond_reglement_id": "3"}, {"cond_reglement_id": 2.0},
    {"cond_reglement_id": True}, {"cond_reglement_id": None, "cond_reglement": "2"},
    {"cond_reglement_code": "30D"}, {},
])
async def test_condition_differente_ou_absente_signalee_sans_fausse_verification(api, monkeypatch, condition):
    monkeypatch.setenv(VARIABLE, "30D")
    api["tiers"] = {"id": "37", **condition}
    resultat = await creer()
    assert creation(api)["cond_reglement_id"] == 2
    assert resultat["success"] is True
    assert resultat["payment_terms_applied"]["verified"] is False
    assert resultat["payment_terms_applied"]["error"]


@pytest.mark.parametrize("champ,valeur", [
    ("cond_reglement_id", 2), ("cond_reglement", "2"), ("fk_cond_reglement", "2"),
])
async def test_verification_identifiant_brut_et_alias_dolibarr(api, monkeypatch, champ, valeur):
    monkeypatch.setenv(VARIABLE, "30D")
    api["tiers"] = {"id": "37", champ: valeur}
    resultat = await creer()
    assert creation(api)["cond_reglement_id"] == 2
    assert resultat["payment_terms_applied"]["verified"] is True


async def test_erreurs_dictionnaire_et_relecture_conservees(api, monkeypatch):
    monkeypatch.setenv(VARIABLE, "2")
    api["dictionnaire"] = erreur_http(403)
    api["tiers"] = erreur_http(503)
    resultat = await creer()
    assert creation(api)["cond_reglement_id"] == 2
    assert resultat["success"] is True
    condition = resultat["payment_terms_applied"]
    assert condition["verified"] is None
    assert "403" in condition["error"] and "503" in condition["error"]


@pytest.mark.parametrize("valeur", [0, -1, "2", True, False, 2.0, 1.5, "", [], {}])
async def test_parametre_invalide_refuse_avant_tout_appel(api, monkeypatch, valeur):
    monkeypatch.setenv(VARIABLE, "30D")
    resultat = await creer(cond_reglement_id=valeur)
    assert resultat["success"] is False
    assert "cond_reglement_id" in resultat["error"]
    assert "entier strictement positif" in resultat["error"]
    assert api["appels"] == []


@pytest.mark.parametrize("valeur", [True, "2", 2.0, 0, -1])
async def test_schema_mcp_refuse_coercition_du_parametre(api, valeur):
    with pytest.raises(Exception, match="cond_reglement_id"):
        await server.mcp.call_tool("dolibarr_create_thirdparty", {
            "name": "Client de test", "cond_reglement_id": valeur,
        })
    assert api["appels"] == []


async def test_reglage_relu_a_chaque_appel_et_dictionnaire_partage(api, monkeypatch):
    for reglage, identifiant in [("30D", 2), ("3", 3), ("", None)]:
        monkeypatch.setenv(VARIABLE, reglage)
        api["tiers"]["cond_reglement_id"] = str(identifiant)
        resultat = await creer()
        assert api["ecritures"][-1].get("cond_reglement_id") == identifiant
        assert resultat["payment_terms_applied"]["verified"] is (None if identifiant is None else True)
    assert api["appels"].count(("GET", server.PAYMENT_TERMS_ENDPOINT)) == 1
    assert api["appels"].count(("GET", "thirdparties/37")) == 2


async def test_echec_dictionnaire_partage_entre_creations(api, monkeypatch):
    monkeypatch.setenv(VARIABLE, "30D")
    api["dictionnaire"] = erreur_http(403)
    for _ in range(2):
        resultat = await creer()
        assert resultat["success"] is True
        assert "cond_reglement_id" not in api["ecritures"][-1]
        assert "403" in resultat["payment_terms_applied"]["error"]
    assert api["appels"].count(("GET", server.PAYMENT_TERMS_ENDPOINT)) == 1
