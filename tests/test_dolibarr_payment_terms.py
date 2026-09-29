"""Les échéances par défaut ne doivent pas déclencher une relance sans réserve."""

import json
import os
from collections import Counter
from datetime import datetime

import httpx
import pytest

os.environ.setdefault("DOLIBARR_URL", "https://erp.example.test")
os.environ.setdefault("DOLIBARR_API_KEY", "test-key")

from dolibarr_mcp import server

CHAMPS_CONDITION = ("cond_reglement_id", "cond_reglement_code", "cond_reglement_label")
CHAMPS_FACTURE = (*CHAMPS_CONDITION, *("thirdparty_" + c for c in CHAMPS_CONDITION),
                  "date_due_is_default")
OUTILS_LISTE = ("dolibarr_list_invoices", "dolibarr_list_supplier_invoices")
OUTILS_DETAIL = ("dolibarr_get_invoice", "dolibarr_get_supplier_invoice")


def facture(identifiant=1, **champs):
    return {
        "id": identifiant, "ref": "FA2609-0942", "socid": "37", "fk_statut": "1",
        "date": "2026-09-03", "date_lim_reglement": "2026-09-04",
        "cond_reglement_id": "1", **champs,
    }


def erreur_http(statut):
    reponse = httpx.Response(statut, request=httpx.Request("GET", "https://erp.example.test"))
    return httpx.HTTPStatusError("", request=reponse.request, response=reponse)


@pytest.fixture
def api(monkeypatch):
    """Un faux transport compté évite de masquer des lectures hors de la page rendue."""
    monkeypatch.setattr(server, "_PAYMENT_TERMS_CACHE", None)
    donnees = {
        "factures": [facture()],
        "dictionnaire": [
            {"id": "1", "code": "30J", "label": "À 30 jours"},
            {"id": "2", "code": "60J", "libelle": "À 60 jours"},
        ],
        "tiers": {37: {"id": "37", "cond_reglement_id": "2"}},
        "appels": [],
    }

    async def lire(endpoint, params=None):
        donnees["appels"].append(endpoint)
        if endpoint == server.PAYMENT_TERMS_ENDPOINT:
            resultat = donnees["dictionnaire"]
        elif endpoint in ("invoices", "supplierinvoices"):
            debut = params["page"] * params["limit"]
            resultat = donnees["factures"][debut:debut + params["limit"]]
        elif endpoint.startswith("thirdparties/"):
            resultat = donnees["tiers"][int(endpoint.split("/")[-1])]
        elif endpoint.startswith(("invoices/", "supplierinvoices/")):
            identifiant = int(endpoint.split("/")[-1])
            resultat = next(f for f in donnees["factures"] if f["id"] == identifiant)
        else:
            raise AssertionError(f"Route inattendue : {endpoint}")
        if isinstance(resultat, Exception):
            raise resultat
        return resultat

    monkeypatch.setattr(server, "api_get", lire)
    return donnees


async def lire_factures(nom, **options):
    if nom in OUTILS_DETAIL:
        options.setdefault("invoice_id", 1)
    else:
        options.setdefault("status", "all")
    return json.loads(await getattr(server, nom)(include_payments=False, **options))


def lignes(nom, reponse):
    return reponse["invoices"] if nom in OUTILS_LISTE else [reponse]


@pytest.mark.parametrize("nom", (*OUTILS_LISTE, *OUTILS_DETAIL))
@pytest.mark.parametrize("echeance,attendu", [
    ("2026-09-04", True), ("2026-10-03", False), (None, None), ("illisible", None),
])
async def test_echeance_de_la_facture_et_note(api, nom, echeance, attendu):
    if echeance is None:
        api["factures"][0].pop("date_lim_reglement")
    else:
        api["factures"][0]["date_lim_reglement"] = echeance
    reponse = await lire_factures(nom)
    ligne = lignes(nom, reponse)[0]
    assert ligne["date_due_is_default"] is attendu
    assert ("date_due_is_default" in ligne["unavailable_fields"]) is (attendu is None)
    assert ("payment_terms_note" in reponse) is (attendu is True)
    if attendu is True:
        assert "défaut Dolibarr" in reponse["payment_terms_note"]
        assert "négociée" in reponse["payment_terms_note"]
        assert "days_late surévalue" in reponse["payment_terms_note"]
    if nom in OUTILS_LISTE:
        assert reponse["date_due_default_count"] == (1 if attendu else 0)


@pytest.mark.parametrize("date_facture,echeance,attendu", [
    ("2026-09-03", "2026-09-04", True),
    ("2026-09-03", "2026-10-03", False),
    ("2026-12-31", "2027-01-01", True),
    ("2028-02-28", "2028-02-29", True),
    ("2026-09-03", "2026-09-03", False),
    ("2026-09-03", "2026-09-02", False),
    ("9999-12-31", "9999-12-31", False),
    ("", "2026-09-04", None),
    (None, "2026-09-04", None),
    ("2026-09-03", "", None),
    ("2026-09-03", None, None),
    ("illisible", "2026-09-04", None),
    ("2026-02-30", "2026-03-01", None),
])
def test_dates_inconnues_et_passages_de_mois(date_facture, echeance, attendu):
    assert server._due_date_is_default(date_facture, echeance) is attendu


@pytest.mark.parametrize("nom", (*OUTILS_LISTE, *OUTILS_DETAIL))
async def test_conditions_facture_et_tiers_resolues_separement(api, nom):
    ligne = lignes(nom, await lire_factures(nom))[0]
    assert [ligne[c] for c in CHAMPS_CONDITION] == [1, "30J", "À 30 jours"]
    assert [ligne["thirdparty_" + c] for c in CHAMPS_CONDITION] == [2, "60J", "À 60 jours"]
    assert all(c not in ligne["unavailable_fields"] for c in CHAMPS_FACTURE)
    assert "total_ttc" in ligne["unavailable_fields"]
    assert "payments" in ligne["unavailable_fields"]


@pytest.mark.parametrize("nom", OUTILS_LISTE)
async def test_cache_dictionnaire_et_cache_tiers_par_appel(api, nom):
    api["factures"] = [facture(1), facture(2), facture(3, socid=38)]
    api["tiers"][38] = {"id": 38}
    for tour in (1, 2):
        await lire_factures(nom)
        compte = Counter(api["appels"])
        assert compte[server.PAYMENT_TERMS_ENDPOINT] == 1
        assert compte["thirdparties/37"] == tour
        assert compte["thirdparties/38"] == tour


@pytest.mark.parametrize("nom", OUTILS_LISTE)
async def test_enrichissement_uniquement_apres_pagination(api, nom):
    api["factures"] = [facture(i, socid=i) for i in range(1, 106)]
    api["tiers"] = {101: {"id": 101}, 102: {"id": 102}}
    reponse = await lire_factures(nom, limit=2, cursor="100")
    assert reponse["total"] == 105
    assert [f["id"] for f in reponse["invoices"]] == [101, 102]
    assert [c for c in api["appels"] if c.startswith("thirdparties/")] == [
        "thirdparties/101", "thirdparties/102",
    ]
    assert all("_payment_terms_source" not in f for f in reponse["invoices"])
    assert reponse["date_due_default_count"] == 2


@pytest.mark.parametrize("nom", (*OUTILS_LISTE, *OUTILS_DETAIL))
@pytest.mark.parametrize("statut", [403, 404])
async def test_dictionnaire_refuse_ne_masque_pas_les_factures(api, nom, statut):
    api["dictionnaire"] = erreur_http(statut)
    api["factures"] = [facture(1), facture(2)]
    for _ in range(2):
        reponse = await lire_factures(nom)
        assert str(statut) in reponse["payment_terms_unavailable"]
        for ligne in lignes(nom, reponse):
            assert ligne["cond_reglement_id"] == 1
            assert ligne["thirdparty_cond_reglement_id"] == 2
            for prefixe in ("", "thirdparty_"):
                for champ in ("cond_reglement_code", "cond_reglement_label"):
                    assert ligne[prefixe + champ] is None
                    assert prefixe + champ in ligne["unavailable_fields"]
            assert ligne["date_due_is_default"] is True
    assert api["appels"].count(server.PAYMENT_TERMS_ENDPOINT) == 1


@pytest.mark.parametrize("nom", (*OUTILS_LISTE, *OUTILS_DETAIL))
async def test_tiers_refuse_ne_masque_pas_les_factures(api, nom):
    api["tiers"][37] = erreur_http(403)
    api["factures"] = [facture(1), facture(2)]
    reponse = await lire_factures(nom)
    for ligne in lignes(nom, reponse):
        assert ligne["cond_reglement_label"] == "À 30 jours"
        assert "403" in ligne["thirdparty_payment_terms_error"]
        for champ in CHAMPS_CONDITION:
            assert ligne["thirdparty_" + champ] is None
            assert "thirdparty_" + champ in ligne["unavailable_fields"]
    assert api["appels"].count("thirdparties/37") == 1


@pytest.mark.parametrize("nom", (*OUTILS_LISTE, *OUTILS_DETAIL))
async def test_option_desactivee_ne_lit_aucune_condition(api, nom, monkeypatch):
    monkeypatch.setattr(server, "_PAYMENT_TERMS_CACHE", {
        "by_id": {}, "by_code": {}, "error": "Refus mémorisé avant cet appel.",
    })
    reponse = await lire_factures(nom, include_payment_terms=False)
    for ligne in lignes(nom, reponse):
        assert all(ligne[c] is None for c in CHAMPS_FACTURE)
        assert all(c in ligne["unavailable_fields"] for c in CHAMPS_FACTURE)
        assert "include_payment_terms=false" in ligne["payment_terms_error"]
        assert "payment_terms_unavailable" not in ligne
    assert len(api["appels"]) == 1
    assert "payment_terms_unavailable" not in reponse
    assert "payment_terms_note" not in reponse


@pytest.mark.parametrize("nom", OUTILS_LISTE)
async def test_compteur_et_note_portent_sur_la_page_rendue(api, nom):
    api["factures"] = [
        facture(1), facture(2, date_lim_reglement="2026-10-03"),
        facture(3, date_lim_reglement=None), facture(4),
    ]
    premiere = await lire_factures(nom, limit=3)
    assert premiere["date_due_default_count"] == 1
    assert "payment_terms_note" in premiere
    suivante = await lire_factures(nom, limit=1, cursor="1")
    assert suivante["date_due_default_count"] == 0
    assert "payment_terms_note" not in suivante
    vide = await lire_factures(nom, cursor="4")
    assert vide["date_due_default_count"] == 0
    assert "payment_terms_note" not in vide


@pytest.mark.parametrize("condition,attendu", [
    ({"cond_reglement_id": "2"}, [2, "60J", "À 60 jours"]),
    ({"cond_reglement": "1"}, [1, "30J", "À 30 jours"]),
    ({"cond_reglement_code": "30J"}, [1, "30J", "À 30 jours"]),
    ({}, [None, None, None]),
    ({"cond_reglement_id": None}, [None, None, None]),
    ({"cond_reglement_id": "0"}, [None, None, None]),
])
async def test_condition_du_tiers_ou_absence_de_parametrage(api, condition, attendu):
    api["tiers"][37] = {"id": 37, **condition}
    reponse = json.loads(await server.dolibarr_get_thirdparty(37))
    assert [reponse[c] for c in CHAMPS_CONDITION] == attendu
    assert reponse["unavailable_fields"] == []


@pytest.mark.parametrize("libelles,attendu", [
    ({"label": "Libellé prioritaire", "libelle": "Second"}, "Libellé prioritaire"),
    ({"label": "", "libelle": "Second", "libelle_facture": "Troisième"}, "Second"),
    ({"label": None, "libelle": "", "libelle_facture": "Troisième"}, "Troisième"),
])
async def test_alias_du_libelle_dans_le_dictionnaire(api, libelles, attendu):
    api["dictionnaire"] = [{"id": "1", "code": "30J", **libelles}]
    dictionnaire = await server._payment_terms()
    assert dictionnaire["error"] is None
    assert dictionnaire["by_id"]["1"]["label"] == attendu
    assert dictionnaire["by_code"]["30J"] == dictionnaire["by_id"]["1"]


@pytest.mark.parametrize("nom", (*OUTILS_LISTE, *OUTILS_DETAIL))
async def test_alias_et_valeurs_portees_par_la_facture_priment(api, nom):
    api["factures"] = [facture(
        cond_reglement_id="1", cond_reglement="2", cond_reglement_code="CODE_FACTURE",
        cond_reglement_doc="Condition propre à la facture",
    )]
    api["dictionnaire"] = erreur_http(404)
    ligne = lignes(nom, await lire_factures(nom))[0]
    assert [ligne[c] for c in CHAMPS_CONDITION] == [
        1, "CODE_FACTURE", "Condition propre à la facture",
    ]
    assert all(c not in ligne["unavailable_fields"] for c in CHAMPS_CONDITION)


@pytest.mark.parametrize("nom", (*OUTILS_LISTE, *OUTILS_DETAIL))
async def test_dates_unix_et_alias_fournisseur(api, nom):
    api["factures"] = [facture(
        datef=int(datetime(2026, 9, 3, 12).timestamp()),
        date_echeance=int(datetime(2026, 9, 4, 12).timestamp()),
        date_lim_reglement="2026-10-03",
    )]
    api["factures"][0].pop("date")
    ligne = lignes(nom, await lire_factures(nom))[0]
    assert ligne["date_invoice"] == "2026-09-03"
    assert ligne["date_due"] == "2026-09-04"
    assert ligne["date_due_is_default"] is True


@pytest.mark.parametrize("reponse", [{"error": "refus"}, [None], "illisible"])
async def test_dictionnaire_invalide_memorise_comme_indisponible(api, reponse):
    api["dictionnaire"] = reponse
    for _ in range(2):
        dictionnaire = await server._payment_terms()
        assert dictionnaire["by_id"] == dictionnaire["by_code"] == {}
        assert dictionnaire["error"]
    assert api["appels"].count(server.PAYMENT_TERMS_ENDPOINT) == 1


async def test_absence_de_condition_facture_ne_copie_pas_celle_du_tiers(api):
    api["factures"][0].pop("cond_reglement_id")
    ligne = await lire_factures("dolibarr_get_invoice")
    assert all(ligne[c] is None for c in CHAMPS_CONDITION)
    assert ligne["thirdparty_cond_reglement_id"] == 2


async def test_condition_inconnue_reste_partielle(api):
    api["factures"][0]["cond_reglement_id"] = "999"
    ligne = await lire_factures("dolibarr_get_invoice")
    assert ligne["cond_reglement_id"] == 999
    assert ligne["cond_reglement_code"] is None
    assert ligne["cond_reglement_label"] is None
    assert "cond_reglement_code" in ligne["unavailable_fields"]
    assert "cond_reglement_label" in ligne["unavailable_fields"]


async def test_tiers_sans_identifiant_ne_declenche_pas_de_lecture(api):
    api["factures"][0].pop("socid")
    ligne = await lire_factures("dolibarr_get_invoice")
    assert "Identifiant de tiers" in ligne["thirdparty_payment_terms_error"]
    assert not any(c.startswith("thirdparties/") for c in api["appels"])


async def test_detail_tiers_reste_lisible_sans_dictionnaire(api):
    api["dictionnaire"] = erreur_http(403)
    reponse = json.loads(await server.dolibarr_get_thirdparty(37))
    assert reponse["cond_reglement_id"] == 2
    assert reponse["cond_reglement_label"] is None
    assert "403" in reponse["payment_terms_unavailable"]
    assert "cond_reglement_label" in reponse["unavailable_fields"]
