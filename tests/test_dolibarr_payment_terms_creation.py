"""Conditions à la création : priorité, transport compté et refus hors ligne."""

import json
import os
from collections import Counter
from copy import deepcopy

import httpx
import pytest

os.environ.setdefault("DOLIBARR_URL", "https://erp.example.test")
os.environ.setdefault("DOLIBARR_API_KEY", "test-key")

from dolibarr_mcp import server

OUTILS = (
    "dolibarr_create_invoice",
    "dolibarr_create_proposal",
    "dolibarr_create_invoice_from_proposal",
)
OUTILS_FACTURE = (OUTILS[0], OUTILS[2])


def erreur_http(statut):
    reponse = httpx.Response(statut, request=httpx.Request("GET", "https://erp.example.test"))
    return httpx.HTTPStatusError("Lecture refusée", request=reponse.request, response=reponse)


@pytest.fixture
def api(monkeypatch):
    """Toutes les routes sont simulées ; l'ordre et le nombre d'appels sont conservés."""
    monkeypatch.setattr(server, "_PAYMENT_TERMS_CACHE", None)
    etat = {
        "tiers": {"id": "37", "cond_reglement_id": "3", "mode_reglement_id": "4"},
        "devis": {
            "id": "71", "ref": "PR-TEST", "socid": "37", "total_ht": "100",
            "mode_reglement_id": "6",
            "lines": [{"desc": "Prestation", "qty": "1", "subprice": "100", "tva_tx": "20"}],
        },
        "facture": {
            "id": "101", "ref": "(PROV101)", "socid": "37", "fk_statut": "0",
            "date": "2026-10-19", "total_ht": "100", "total_ttc": "120", "lines": [],
        },
        "dictionnaire": [
            {"id": "1", "code": "RECEP", "label": "À réception", "nbjour": "1"},
            {"id": "2", "code": "30D", "label": "À 30 jours"},
            {"id": "3", "code": "30DENDMONTH", "libelle": "À 30 jours fin de mois"},
            {"id": "8", "code": "PT_5050", "libelle_facture": "50 % à la commande, 50 % à livraison"},
        ],
        "appels": [],
        "ecritures": [],
    }

    async def lire(route, params=None):
        etat["appels"].append(("GET", route))
        routes = {
            "thirdparties/37": "tiers",
            "proposals/71": "devis",
            "invoices/101": "facture",
            server.PAYMENT_TERMS_ENDPOINT: "dictionnaire",
        }
        assert route in routes, f"Lecture inattendue : {route}"
        resultat = etat[routes[route]]
        if isinstance(resultat, Exception):
            raise resultat
        return deepcopy(resultat)

    async def ecrire(route, data):
        etat["appels"].append(("POST", route))
        etat["ecritures"].append((route, deepcopy(data)))
        assert route in ("invoices", "proposals", "invoices/101/lines")
        return 71 if route == "proposals" else 101

    monkeypatch.setattr(server, "api_get", lire)
    monkeypatch.setattr(server, "api_post", ecrire)
    return etat


def arguments(nom):
    return {"proposal_id": 71} if nom == OUTILS[2] else {"thirdparty_id": 37}


async def creer(nom, **options):
    return json.loads(await getattr(server, nom)(**arguments(nom), **options))


def creation(api, nom):
    route = "proposals" if nom == OUTILS[1] else "invoices"
    ecritures = [corps for endpoint, corps in api["ecritures"] if endpoint == route]
    assert len(ecritures) == 1
    assert "mode_reglement_id" not in ecritures[0]
    return ecritures[0]


@pytest.mark.parametrize("nom", OUTILS)
async def test_condition_du_tiers_lue_avant_creation(api, nom):
    resultat = await creer(nom)
    assert creation(api, nom)["cond_reglement_id"] == 3
    assert resultat["success"] is True
    assert resultat["payment_terms_applied"] == {
        "cond_reglement_id": 3, "code": "30DENDMONTH", "label": "À 30 jours fin de mois",
        "source": "tiers", "error": None,
    }
    lectures = Counter(api["appels"])
    assert lectures[("GET", "thirdparties/37")] == 1
    assert lectures[("GET", server.PAYMENT_TERMS_ENDPOINT)] == 1
    route = "proposals" if nom == OUTILS[1] else "invoices"
    assert api["appels"].index(("GET", "thirdparties/37")) < api["appels"].index(("POST", route))


@pytest.mark.parametrize("nom", OUTILS)
async def test_parametre_prioritaire_sans_lecture_du_tiers(api, nom):
    api["devis"]["cond_reglement_id"] = "8"
    api["tiers"] = erreur_http(403)
    resultat = await creer(nom, cond_reglement_id=2)
    assert creation(api, nom)["cond_reglement_id"] == 2
    assert resultat["success"] is True
    assert resultat["payment_terms_applied"] == {
        "cond_reglement_id": 2, "code": "30D", "label": "À 30 jours",
        "source": "parametre", "error": None,
    }
    assert ("GET", "thirdparties/37") not in api["appels"]


@pytest.mark.parametrize("nom", OUTILS)
@pytest.mark.parametrize("condition", [{}, {"cond_reglement_id": None}, {"cond_reglement_id": "0"}])
async def test_tiers_sans_condition_ne_transmet_pas_de_valeur(api, nom, condition):
    api["tiers"] = {"id": "37", **condition}
    resultat = await creer(nom)
    assert "cond_reglement_id" not in creation(api, nom)
    assert resultat["success"] is True
    assert resultat["payment_terms_applied"] == {
        "cond_reglement_id": None, "code": None, "label": None,
        "source": "aucune", "error": None,
    }


@pytest.mark.parametrize("nom", OUTILS)
async def test_null_explicitement_fourni_herite_du_tiers(api, nom):
    resultat = await creer(nom, cond_reglement_id=None)
    assert creation(api, nom)["cond_reglement_id"] == 3
    assert resultat["payment_terms_applied"]["source"] == "tiers"


@pytest.mark.parametrize("nom", OUTILS)
async def test_refus_403_du_tiers_ne_bloque_pas_creation(api, nom):
    api["tiers"] = erreur_http(403)
    resultat = await creer(nom)
    assert "cond_reglement_id" not in creation(api, nom)
    assert resultat["success"] is True
    condition = resultat["payment_terms_applied"]
    assert condition["source"] == "aucune"
    assert condition["cond_reglement_id"] is condition["code"] is condition["label"] is None
    assert "403" in condition["error"]


async def test_condition_du_devis_prime_sur_celle_du_tiers(api):
    api["devis"]["cond_reglement_id"] = "8"
    resultat = await creer(OUTILS[2])
    assert creation(api, OUTILS[2])["cond_reglement_id"] == 8
    assert resultat["success"] is True
    assert resultat["payment_terms_applied"] == {
        "cond_reglement_id": 8, "code": "PT_5050", "label": "50 % à la commande, 50 % à livraison",
        "source": "devis", "error": None,
    }
    assert ("GET", "thirdparties/37") not in api["appels"]
    assert api["appels"].count(("GET", "proposals/71")) == 1


@pytest.mark.parametrize("nom", OUTILS_FACTURE)
@pytest.mark.parametrize("options", [{}, {"cond_reglement_id": 2}])
async def test_echeance_expresse_prime_avec_condition_heritee_ou_forcee(api, nom, options):
    api["devis"]["cond_reglement_id"] = "8"
    resultat = await creer(nom, due_date="2026-12-05", **options)
    payload = creation(api, nom)
    assert payload["date_lim_reglement"] == "2026-12-05"
    assert payload["cond_reglement_id"] == options.get("cond_reglement_id", 8 if nom == OUTILS[2] else 3)
    assert resultat["success"] is True


@pytest.mark.parametrize("nom", OUTILS)
@pytest.mark.parametrize("valeur", [0, -1, "2", True, False, 2.0, 1.5, "", [], {}])
async def test_parametre_entier_strictement_positif_avant_tout_appel(api, nom, valeur):
    resultat = json.loads(await getattr(server, nom)(**arguments(nom), cond_reglement_id=valeur))
    assert resultat["success"] is False
    assert "cond_reglement_id" in resultat["error"]
    assert "entier strictement positif" in resultat["error"]
    assert api["appels"] == []


@pytest.mark.parametrize("nom", OUTILS)
@pytest.mark.parametrize("valeur", [True, "2", 2.0, 0])
async def test_schema_mcp_refuse_la_coercition_avant_transport(api, nom, valeur):
    with pytest.raises(Exception, match="cond_reglement_id"):
        await server.mcp.call_tool(nom, {**arguments(nom), "cond_reglement_id": valeur})
    assert api["appels"] == []


async def test_cache_deja_rempli_sans_nouvelle_lecture_du_dictionnaire(api):
    await server._payment_terms()
    api["appels"].clear()
    for nom in OUTILS:
        resultat = await creer(nom)
        assert resultat["payment_terms_applied"]["code"] == "30DENDMONTH"
    assert ("GET", server.PAYMENT_TERMS_ENDPOINT) not in api["appels"]


@pytest.mark.parametrize("nom", OUTILS)
async def test_dictionnaire_inaccessible_ne_perd_pas_condition_et_memorise_erreur(api, nom):
    api["dictionnaire"] = erreur_http(403)
    for _ in range(2):
        resultat = await creer(nom)
        condition = resultat["payment_terms_applied"]
        assert resultat["success"] is True
        assert condition["cond_reglement_id"] == 3
        assert condition["source"] == "tiers"
        assert condition["code"] is condition["label"] is None
        assert "403" in condition["error"]
    route = "proposals" if nom == OUTILS[1] else "invoices"
    assert [corps["cond_reglement_id"] for endpoint, corps in api["ecritures"] if endpoint == route] == [3, 3]
    assert api["appels"].count(("GET", server.PAYMENT_TERMS_ENDPOINT)) == 1


@pytest.mark.parametrize("nom", OUTILS)
async def test_reponse_tiers_invalide_signalee_sans_bloquer_creation(api, nom):
    api["tiers"] = {"error": "Lecture refusée"}
    resultat = await creer(nom)
    assert "cond_reglement_id" not in creation(api, nom)
    assert resultat["success"] is True
    assert resultat["payment_terms_applied"]["source"] == "aucune"
    assert resultat["payment_terms_applied"]["error"]


async def test_identifiant_inconnu_du_dictionnaire_reste_transmis(api):
    resultat = await creer(OUTILS[0], cond_reglement_id=999)
    assert creation(api, OUTILS[0])["cond_reglement_id"] == 999
    assert resultat["payment_terms_applied"]["cond_reglement_id"] == 999
    assert resultat["payment_terms_applied"]["source"] == "parametre"
    assert resultat["payment_terms_applied"]["code"] is None
    assert resultat["payment_terms_applied"]["label"] is None
