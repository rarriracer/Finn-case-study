"""Microsoft Graph transport for MIME email."""

from base64 import b64encode
from urllib.request import Request, urlopen

GRAPH_SCOPES = ["https://graph.microsoft.com/Mail.Send"]

def send_graph(reply, token):
    request = Request(
        "https://graph.microsoft.com/v1.0/me/sendMail",
        data=b64encode(reply if isinstance(reply, bytes) else reply.as_bytes()),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "text/plain"},
    )
    with urlopen(request, timeout=30) as response:
        if response.status != 202:
            raise RuntimeError(f"Unerwarteter Graph-Status: HTTP {response.status}.")

