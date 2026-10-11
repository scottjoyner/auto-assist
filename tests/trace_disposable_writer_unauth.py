"""Disposable broker-side negative: writer rejects a request with no Bearer."""
import json
from pathlib import Path
import urllib.error
import urllib.request

conf = json.loads(Path("/tmp/broker-config.json").read_text())
endpoint = conf["writer_endpoint"]
request = urllib.request.Request(
    endpoint + "/v1/admit", method="POST",
    data=b"{}", headers={"Content-Type": "application/json"},
)
try:
    with urllib.request.urlopen(request, timeout=4) as response:
        status = response.status
except urllib.error.HTTPError as exc:
    status = exc.code
assert status == 401, "EXPECTED_UNAUTH_POLICY_REJECTION_" + str(status)
print(json.dumps({"writer_without_authorization_denied": True,
                  "observed_status": status}))
