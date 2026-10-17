"""Écriture des conditions d'un tiers : transport, preuve et erreurs hors ligne."""

import json
from unittest.mock import AsyncMock

import httpx
import pytest

from dolibarr_mcp import server


@pytest.fixture
def api(monkeypatch):
    appels = []
    etat = {"tiers": {"id": "37", "cond_reglement_id": "2"}, "erreur_put": None}

    async def put(route, data):
        appels.append(("PUT", route, data))
        if etat["erreur_put"]:
            raise etat["erreur_put"]
        return 37

    async def get(route, params=None):
        appels.append(("GET", route))
        if route == server.PAYMENT_TERMS_ENDPOINT:
            return [{"id": "2", "code": "60J", "label": "À 60 jours"}]
        assert route == "thirdparties/37"
        if isinstance(etat["tiers"], Exception):
            raise etat["tiers"]
        return etat["tiers"]

    monkeypatch.setattr(server, "api_put", put)
    monkeypatch.setattr(server, "api_get", get)
    monkeypatch.setattr(server, "_PAYMENT_TERMS_CACHE", None)
    return etat, appels


async def test_condition_ecrite_puis_relue_avec_son_libelle(api):
    _, appels = api
    resultat = json.loads(await server.dolibarr_update_thirdparty(37, cond_reglement_id=2))
    assert appels[:2] == [
        ("PUT", "thirdparties/37", {"cond_reglement_id": 2}), ("GET", "thirdparties/37"),
    ]
    assert resultat["success"] and resultat["verified"]
    assert resultat["write_state"] == "accepted"
    assert resultat["write_may_have_succeeded"]
    assert resultat["readback"]["cond_reglement_id"] == 2
    assert resultat["readback"]["cond_reglement_label"] == "À 60 jours"
    lecture = json.loads(await server.dolibarr_get_thirdparty(37))
    assert lecture["cond_reglement_id"] == 2
    assert lecture["cond_reglement_label"] == "À 60 jours"


@pytest.mark.parametrize("valeur", [0, -1, True, False, "2", "", 2.0, 1.5, [], {}])
async def test_entier_strict_positif_avant_tout_appel(api, valeur):
    _, appels = api
    resultat = json.loads(await server.dolibarr_update_thirdparty(37, cond_reglement_id=valeur))
    assert resultat["success"] is False
    assert resultat["write_state"] == "not_attempted"
    assert resultat["write_may_have_succeeded"] is False
    assert appels == []


@pytest.mark.parametrize("valeur", [True, "2", 2.0, 0])
async def test_schema_mcp_refuse_la_coercition_avant_transport(api, valeur):
    _, appels = api
    with pytest.raises(Exception, match="cond_reglement_id"):
        await server.mcp.call_tool("dolibarr_update_thirdparty", {
            "thirdparty_id": 37, "cond_reglement_id": valeur,
        })
    assert appels == []


async def test_autres_parametres_conserves_sans_condition(api):
    _, appels = api
    resultat = json.loads(await server.dolibarr_update_thirdparty(
        37, name="Tiers fictif", client=0, fournisseur=1, status=0, zip_code="00000",
    ))
    assert resultat["success"]
    assert appels == [("PUT", "thirdparties/37", {
        "name": "Tiers fictif", "zip": "00000", "client": "0", "fournisseur": "1", "status": "0",
    })]


@pytest.mark.parametrize("tiers", [{"id": "37", "cond_reglement_id": "1"}, {"id": "37"}])
async def test_valeur_absente_ou_differente_ne_confirme_pas_ecriture(api, tiers):
    etat, _ = api
    etat["tiers"] = tiers
    resultat = json.loads(await server.dolibarr_update_thirdparty(37, cond_reglement_id=2))
    assert not resultat["success"] and not resultat["verified"]
    assert resultat["write_state"] == "accepted"
    assert resultat["write_may_have_succeeded"]
    assert resultat["payment_terms_warning"]
    assert resultat["readback"]["cond_reglement_id"] != 2


@pytest.mark.parametrize("tiers", [httpx.ReadTimeout("Lecture impossible"), [], {"id": "38"}])
async def test_echec_relecture_ne_nie_pas_ecriture(api, tiers):
    etat, appels = api
    etat["tiers"] = tiers
    resultat = json.loads(await server.dolibarr_update_thirdparty(37, cond_reglement_id=2))
    assert not resultat["success"] and not resultat["verified"]
    assert resultat["write_state"] == "accepted" and resultat["write_may_have_succeeded"]
    assert resultat["readback"] is None and resultat["refetch_warning"]
    assert len(appels) == 2


@pytest.mark.parametrize("statut,attendu", [(403, "rejected"), (500, "unknown")])
async def test_echec_ecriture_declare_et_relecture_conservee(api, statut, attendu):
    etat, appels = api
    reponse = httpx.Response(statut, request=httpx.Request("PUT", "https://example.test/"))
    etat["erreur_put"] = httpx.HTTPStatusError("Refus", request=reponse.request, response=reponse)
    etat["tiers"]["cond_reglement_id"] = "1"
    resultat = json.loads(await server.dolibarr_update_thirdparty(37, cond_reglement_id=2))
    assert not resultat["success"]
    assert resultat["write_state"] == attendu and resultat["error"]
    assert resultat["write_may_have_succeeded"] is (statut == 500)
    assert resultat["readback"]["cond_reglement_id"] == 1
    assert [a[0] for a in appels] == ["PUT", "GET", "GET"]


async def test_reponse_perdue_mais_condition_relue_sans_rejeu(api):
    etat, appels = api
    etat["erreur_put"] = httpx.ReadTimeout("Réponse perdue")
    resultat = json.loads(await server.dolibarr_update_thirdparty(37, cond_reglement_id=2))
    assert not resultat["success"] and resultat["verified"]
    assert resultat["write_state"] == "unknown" and resultat["write_may_have_succeeded"]
    assert resultat["error"]
    assert len([a for a in appels if a[0] == "PUT"]) == 1


async def test_dictionnaire_indisponible_ne_masque_pas_condition_relue(api, monkeypatch):
    monkeypatch.setattr(server, "_payment_terms", AsyncMock(return_value={
        "by_id": {}, "by_code": {}, "error": "Dictionnaire inaccessible",
    }))
    resultat = json.loads(await server.dolibarr_update_thirdparty(37, cond_reglement_id=2))
    assert resultat["success"]
    assert resultat["readback"]["cond_reglement_id"] == 2
    assert resultat["readback"]["cond_reglement_label"] is None
    assert resultat["readback"]["payment_terms_unavailable"]


@pytest.mark.parametrize("tiers", [
    {"id": "37", "cond_reglement_code": "60J"},
    {"id": "37", "cond_reglement_id": 2.9},
    {"id": "37", "cond_reglement_id": True},
])
async def test_identifiant_brut_requis_pour_confirmer_la_condition(api, tiers):
    etat, _ = api
    etat["tiers"] = tiers
    resultat = json.loads(await server.dolibarr_update_thirdparty(37, cond_reglement_id=2))
    assert not resultat["success"] and not resultat["verified"]
    assert resultat["readback"]["cond_reglement_id_raw"] == tiers.get("cond_reglement_id")
