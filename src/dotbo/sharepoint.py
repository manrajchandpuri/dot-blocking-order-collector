"""Microsoft Graph uploads using GitHub OIDC; never sends email itself."""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from urllib.parse import quote

import requests

GRAPH = "https://graph.microsoft.com/v1.0"
CHUNK = 10 * 320 * 1024


def required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"Missing setting: {name}")
    return value


def checked_request(method: str, url: str, **kwargs):
    """Do not log bearer tokens, signed upload URLs or raw identity responses.

    Ambiguous writes fail closed. A rerun uploads into a fresh attempt folder;
    the stable outbox marker prevents a second release of identical content.
    """
    try:
        response = requests.request(method, url, timeout=120, **kwargs)
    except requests.RequestException:
        raise RuntimeError("Microsoft/GitHub connection failed; retry the workflow") from None
    if response.status_code not in (200, 201, 202, 204, 404):
        raise RuntimeError(f"Microsoft/GitHub request failed (HTTP {response.status_code}); check permissions or retry")
    return response


def oidc_token() -> tuple[str, float]:
    request_url = required("ACTIONS_ID_TOKEN_REQUEST_URL")
    separator = "&" if "?" in request_url else "?"
    response = checked_request("GET", request_url + separator + "audience=api%3A%2F%2FAzureADTokenExchange",
                               headers={"Authorization": "Bearer " + required("ACTIONS_ID_TOKEN_REQUEST_TOKEN")})
    if response.status_code != 200:
        raise RuntimeError("GitHub OIDC token request failed")
    assertion = response.json()["value"]
    response = checked_request("POST", f"https://login.microsoftonline.com/{quote(required('AZURE_TENANT_ID'), safe='')}/oauth2/v2.0/token",
        data={"client_id": required("AZURE_CLIENT_ID"), "scope": "https://graph.microsoft.com/.default",
              "grant_type": "client_credentials", "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
              "client_assertion": assertion})
    if response.status_code != 200:
        raise RuntimeError("Microsoft token exchange failed")
    data = response.json()
    return data["access_token"], time.monotonic() + int(data.get("expires_in", 3600)) - 120


class GraphClient:
    def __init__(self, drive_id: str, root_id: str, token_provider=oidc_token):
        self.drive_id, self.root_id = drive_id, root_id
        self.token_provider = token_provider
        self._token, self._expires = "", 0

    @classmethod
    def from_environment(cls):
        return cls(required("SHAREPOINT_DRIVE_ID"), required("SHAREPOINT_ROOT_FOLDER_ID"))

    def call(self, method: str, path: str, **kwargs):
        if time.monotonic() >= self._expires:
            self._token, self._expires = self.token_provider()
        response = checked_request(method, GRAPH + path,
            headers={"Authorization": "Bearer " + self._token}, **kwargs)
        if response.status_code == 404:
            if method == "GET":
                return None
            raise RuntimeError("SharePoint destination not found")
        return response.json() if response.content else {}

    def item_path(self, item_id: str) -> str:
        return f"/drives/{quote(self.drive_id, safe='')}/items/{quote(item_id, safe='')}"

    def child(self, parent: str, name: str):
        return self.call("GET", self.item_path(parent) + ":/" + quote(name, safe=""))

    def folder(self, parent: str, name: str):
        item = self.child(parent, name)
        if item is None:
            item = self.call("POST", self.item_path(parent) + "/children", json={
                "name": name, "folder": {}, "@microsoft.graph.conflictBehavior": "fail"})
        if "folder" not in item:
            raise RuntimeError(f"Expected a SharePoint folder: {name}")
        return item

    def upload(self, parent: str, name: str, path: Path):
        size = path.stat().st_size
        if not size:
            raise ValueError("Cannot publish an empty file")
        endpoint = self.item_path(parent) + ":/" + quote(name, safe="")
        if size <= CHUNK:
            item = self.call("PUT", endpoint + ":/content", data=path.read_bytes())
        else:
            session = self.call("POST", endpoint + ":/createUploadSession", json={
                "item": {"@microsoft.graph.conflictBehavior": "fail", "name": name}})
            upload_url = session["uploadUrl"]
            if not upload_url.startswith("https://"):
                raise RuntimeError("Invalid upload session URL")
            item = None
            with path.open("rb") as stream:
                offset = 0
                while block := stream.read(CHUNK):
                    end = offset + len(block) - 1
                    response = checked_request("PUT", upload_url, data=block, headers={
                        "Content-Length": str(len(block)), "Content-Range": f"bytes {offset}-{end}/{size}"})
                    if end + 1 < size and response.status_code != 202:
                        raise RuntimeError("Upload ended before all bytes were sent")
                    if end + 1 == size:
                        if response.status_code not in (200, 201):
                            raise RuntimeError("Upload was not committed")
                        item = response.json()
                    offset = end + 1
        if not item or item.get("size") != size or not item.get("id") or not item.get("webUrl"):
            raise RuntimeError("Uploaded file could not be verified")
        return item


def publish(client: GraphClient, manifest: dict, output: Path) -> dict:
    from .automation import sha256
    if manifest.get("status") != "Ready":
        raise ValueError("Only a passing package may be published")
    # Check all local files before creating any remote release state.
    for file in manifest["files"].values():
        path = output / file["name"]
        if Path(file["name"]).name != file["name"] or sha256(path) != file["sha256"]:
            raise ValueError("Package files changed after validation")
    outbox = client.folder(client.root_id, "outbox")
    marker_name = manifest["package_key"] + ".json"
    existing = client.child(outbox["id"], marker_name)
    if existing:
        return existing
    packages = client.folder(client.root_id, "packages")
    month = client.folder(packages["id"], manifest["period"])
    attempt = client.folder(month["id"], manifest["package_key"][-12:] + "-" + uuid.uuid4().hex[:12])
    published = json.loads(json.dumps(manifest))
    published["folder_url"] = attempt["webUrl"]
    for role, file in manifest["files"].items():
        item = client.upload(attempt["id"], file["name"], output / file["name"])
        published["files"][role].update(web_url=item["webUrl"], item_id=item["id"])
    marker = output / "published-package.json"
    marker.write_text(json.dumps(published, indent=2), encoding="utf-8")
    # Sole release signal, after all four files were committed and size checked.
    return client.upload(outbox["id"], marker_name, marker)
