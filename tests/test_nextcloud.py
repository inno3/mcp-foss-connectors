"""Tests unitaires offline pour le connecteur Nextcloud.

Tous les appels HTTP/WebDAV sont mockés : la suite tourne sans réseau ni credentials réels.
"""

import json
import os
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("NEXTCLOUD_URL", "https://cloud.example.test")
os.environ.setdefault("NEXTCLOUD_USER", "alice")
os.environ.setdefault("NEXTCLOUD_APP_PASSWORD", "app-pass-xxxx")

from nextcloud_mcp.server import (  # noqa: E402
    _compact,
    _extract_ocs_data,
    _parse_propfind,
    nextcloud_list_files,
)

PROPFIND_XML = """<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:">
  <d:response>
    <d:href>/remote.php/dav/files/alice/Documents/</d:href>
    <d:propstat>
      <d:prop>
        <d:displayname>Documents</d:displayname>
        <d:resourcetype><d:collection/></d:resourcetype>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
  <d:response>
    <d:href>/remote.php/dav/files/alice/Documents/rapport.pdf</d:href>
    <d:propstat>
      <d:prop>
        <d:displayname>rapport.pdf</d:displayname>
        <d:getcontenttype>application/pdf</d:getcontenttype>
        <d:getcontentlength>2048</d:getcontentlength>
        <d:resourcetype/>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
</d:multistatus>"""


class TestHelpers:
    def test_parse_propfind_extracts_entries(self) -> None:
        items = _parse_propfind(PROPFIND_XML)
        assert len(items) == 2
        folder, pdf = items
        assert folder["is_directory"] is True
        assert pdf["filename"] == "rapport.pdf"
        assert pdf["size"] == 2048
        assert pdf["content_type"] == "application/pdf"
        assert pdf["is_directory"] is False

    def test_parse_propfind_invalid_xml_returns_empty(self) -> None:
        assert _parse_propfind("<not xml") == []

    def test_extract_ocs_data_unwraps_envelope(self) -> None:
        resp = {"ocs": {"data": {"id": 1}}}
        assert _extract_ocs_data(resp) == {"id": 1}

    def test_compact(self) -> None:
        assert _compact({"a": 1}) == '{"a":1}'


@pytest.mark.asyncio
class TestListFiles:
    async def test_drops_self_entry_at_depth1(self) -> None:
        with patch(
            "nextcloud_mcp.server._webdav",
            new_callable=AsyncMock,
            return_value=PROPFIND_XML,
        ) as mock_dav:
            raw = await nextcloud_list_files("/Documents", depth=1)
        assert mock_dav.call_args[0][0] == "PROPFIND"
        data = json.loads(raw)
        # depth=1 strips the folder itself -> only the pdf remains
        assert data["count"] == 1
        assert data["items"][0]["filename"] == "rapport.pdf"
