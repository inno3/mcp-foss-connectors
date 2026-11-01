"""Contre-épreuves hors ligne de la garde des recettes HTTP réelles."""

from unittest.mock import AsyncMock

import httpx
import pytest

from tests.test_dolibarr_live_readonly import (
    _assert_readonly_statuses,
    _capture_readonly_requests,
)


@pytest.mark.parametrize("status", [201, 202, 204, 301, 404, 500, 503])
async def test_un_statut_non_200_est_refuse_meme_avec_un_tableau_json(status):
    original = AsyncMock(return_value=httpx.Response(status, json=[]))
    statuses = []
    response = await _capture_readonly_requests(original, statuses)(None, "GET", "https://erp.example.test")
    assert response.json() == []
    with pytest.raises(AssertionError, match="Statuts HTTP"):
        _assert_readonly_statuses(statuses)


async def test_une_ecriture_est_refusee_avant_transport():
    original = AsyncMock()
    with pytest.raises(AssertionError, match="seulement GET"):
        await _capture_readonly_requests(original, [])(None, "POST", "https://erp.example.test")
    original.assert_not_called()


def test_une_recette_sans_reponse_ne_peut_pas_reussir():
    with pytest.raises(AssertionError, match="Aucune réponse"):
        _assert_readonly_statuses([])


def test_toutes_les_reponses_200_sont_acceptees():
    _assert_readonly_statuses([200, 200])
