import json

import pytest
import requests
import responses

from src.publish_instagram import ContainerError, CredentialsError, InstagramClient, InstagramError

UID = "17841400000000000"
TOKEN = "EAAsecret_token_value_should_never_leak_123"
BASE = "https://graph.facebook.com/v21.0"
IMG = "https://example.github.io/repo/images/vl-001.jpg"


def client(**kw):
    return InstagramClient(user_id=UID, access_token=TOKEN, sleep=lambda s: None, poll_interval=1,
                           poll_timeout=3, **kw)


def ok_recent(rsps, captions=()):
    rsps.get(f"{BASE}/{UID}/media", json={"data": [{"id": str(17900000000000 + i), "caption": c}
                                                   for i, c in enumerate(captions)]})


def err(code, msg="boom", transient=False, sub=None):
    return {"error": {"message": msg, "code": code, "is_transient": transient, "error_subcode": sub}}


@responses.activate
def test_full_publish_flow_and_bearer_auth():
    ok_recent(responses)
    responses.post(f"{BASE}/{UID}/media", json={"id": "1790000000001"})
    responses.get(f"{BASE}/1790000000001", json={"status_code": "IN_PROGRESS"})
    responses.get(f"{BASE}/1790000000001", json={"status_code": "FINISHED"})
    responses.post(f"{BASE}/{UID}/media_publish", json={"id": "1790000000099"})
    res = client().publish_image(IMG, "caption here", "alt text")
    assert res.media_id == "1790000000099" and res.container_id == "1790000000001" and not res.recovered
    create = next(c for c in responses.calls if c.request.url.endswith("/media") and c.request.method == "POST")
    assert json.loads(create.request.body) == {"image_url": IMG, "caption": "caption here", "alt_text": "alt text"}
    for c in responses.calls:
        assert c.request.headers["Authorization"] == f"Bearer {TOKEN}"
        assert TOKEN not in c.request.url  # never in URLs (logs, proxies)


@responses.activate
def test_existing_caption_is_recovered_without_reposting():
    ok_recent(responses, captions=["other", "caption here"])
    res = client().publish_image(IMG, "caption here")
    assert res.recovered and res.media_id == "17900000000001"
    assert not any(c.request.method == "POST" for c in responses.calls)


@responses.activate
def test_transient_error_is_retried():
    responses.get(f"{BASE}/{UID}", json=err(2, "temporary", transient=True), status=500)
    responses.get(f"{BASE}/{UID}", json={"id": UID, "username": "veganleaf"})
    assert client().verify_credentials()["username"] == "veganleaf"
    assert len(responses.calls) == 2


@responses.activate
def test_retries_exhausted_raise():
    for _ in range(3):
        responses.get(f"{BASE}/{UID}", json=err(4, "rate limited", transient=True), status=400)
    with pytest.raises(InstagramError) as ei:
        client().verify_credentials()
    assert ei.value.transient and len(responses.calls) == 3


@responses.activate
def test_expired_token_raises_credentials_error_without_retry_or_leak():
    responses.get(f"{BASE}/{UID}", status=400,
                  json=err(190, f"Error validating access token: Session has expired. token={TOKEN}", sub=463))
    with pytest.raises(CredentialsError) as ei:
        client().verify_credentials()
    assert len(responses.calls) == 1
    assert TOKEN not in str(ei.value) and "renew" in str(ei.value)


@responses.activate
def test_token_for_wrong_account_rejected():
    responses.get(f"{BASE}/{UID}", json={"id": "123456789", "username": "someoneelse"})
    with pytest.raises(CredentialsError):
        client().verify_credentials()


@responses.activate
def test_network_error_message_is_redacted():
    responses.get(f"{BASE}/{UID}", body=requests.ConnectionError(f"failed ?access_token={TOKEN}"))
    with pytest.raises(InstagramError) as ei:
        client(max_retries=1).verify_credentials()
    assert TOKEN not in str(ei.value)


@responses.activate
def test_container_error_status():
    ok_recent(responses)
    responses.post(f"{BASE}/{UID}/media", json={"id": "1790000000001"})
    responses.get(f"{BASE}/1790000000001", json={"status_code": "ERROR"})
    with pytest.raises(ContainerError):
        client().publish_image(IMG, "cap")
    assert not any("media_publish" in c.request.url for c in responses.calls)


@responses.activate
def test_container_timeout():
    ok_recent(responses)
    responses.post(f"{BASE}/{UID}/media", json={"id": "1790000000001"})
    responses.get(f"{BASE}/1790000000001", json={"status_code": "IN_PROGRESS"})
    with pytest.raises(ContainerError, match="not ready"):
        client().publish_image(IMG, "cap")


@responses.activate
def test_permission_error_not_retried():
    ok_recent(responses)
    responses.post(f"{BASE}/{UID}/media", status=403, json=err(10, "Application does not have permission"))
    with pytest.raises(InstagramError, match="permission"):
        client().publish_image(IMG, "cap")
    assert sum(c.request.method == "POST" for c in responses.calls) == 1


@responses.activate
def test_malformed_api_response_rejected():
    ok_recent(responses)
    responses.post(f"{BASE}/{UID}/media", json={"id": "<script>"})
    with pytest.raises(InstagramError, match="unexpected API response"):
        client().publish_image(IMG, "cap")


@responses.activate
def test_non_json_response_handled():
    responses.get(f"{BASE}/{UID}", body="<html>gateway error</html>", status=502)
    with pytest.raises(InstagramError):
        client(max_retries=1).verify_credentials()


@responses.activate
def test_media_publish_transient_then_success_is_safe():
    ok_recent(responses)
    responses.post(f"{BASE}/{UID}/media", json={"id": "1790000000001"})
    responses.get(f"{BASE}/1790000000001", json={"status_code": "FINISHED"})
    responses.post(f"{BASE}/{UID}/media_publish", status=500, json=err(2, "temp", transient=True))
    responses.get(f"{BASE}/1790000000001", json={"status_code": "FINISHED"})
    responses.post(f"{BASE}/{UID}/media_publish", json={"id": "1790000000099"})
    assert client().publish_image(IMG, "cap").media_id == "1790000000099"


@responses.activate
def test_media_publish_ambiguous_failure_does_not_double_post():
    ok_recent(responses)
    responses.post(f"{BASE}/{UID}/media", json={"id": "1790000000001"})
    responses.get(f"{BASE}/1790000000001", json={"status_code": "FINISHED"})
    responses.post(f"{BASE}/{UID}/media_publish", status=500, json=err(2, "temp", transient=True))
    responses.get(f"{BASE}/1790000000001", json={"status_code": "PUBLISHED"})
    with pytest.raises(InstagramError, match="reconcile"):
        client().publish_image(IMG, "cap")
    assert sum("media_publish" in c.request.url for c in responses.calls) == 1


@responses.activate
def test_instagram_login_host_supported():
    responses.get(f"https://graph.instagram.com/v21.0/{UID}", json={"id": UID, "username": "veganleaf"})
    c = InstagramClient(user_id=UID, access_token=TOKEN, host="graph.instagram.com", sleep=lambda s: None)
    assert c.verify_credentials()["id"] == UID


@responses.activate
def test_publishing_limit_parsed():
    responses.get(f"{BASE}/{UID}/content_publishing_limit", json={"data": [{"quota_usage": 2}]})
    assert client().publishing_limit() == 2
