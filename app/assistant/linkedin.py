"""LinkedIn: one-off OAuth login (Share on LinkedIn + OpenID Connect products) and a posting tool."""
import json
import secrets
import time
from urllib.parse import urlencode

import httpx

from .tools import Tool, params

AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"
TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
SCOPES = "openid profile w_member_social"


def login_url(settings, memory) -> str:
    state = secrets.token_urlsafe(16)
    memory.put("linkedin_state", state)
    return AUTH_URL + "?" + urlencode({"response_type": "code", "client_id": settings.linkedin_client_id,
                                       "redirect_uri": settings.linkedin_redirect_uri, "state": state,
                                       "scope": SCOPES})


async def handle_callback(settings, memory, code: str, state: str) -> str:
    if not state or state != memory.get("linkedin_state"):
        raise ValueError("OAuth state mismatch")
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(TOKEN_URL, data={"grant_type": "authorization_code", "code": code,
                                          "redirect_uri": settings.linkedin_redirect_uri,
                                          "client_id": settings.linkedin_client_id,
                                          "client_secret": settings.linkedin_client_secret})
        r.raise_for_status()
        tok = r.json()
        me = await c.get("https://api.linkedin.com/v2/userinfo",
                         headers={"Authorization": f"Bearer {tok['access_token']}"})
        me.raise_for_status()
    memory.put("linkedin", json.dumps({"token": tok["access_token"], "sub": me.json()["sub"],
                                       "expires": time.time() + tok.get("expires_in", 0)}))
    return me.json().get("name", "LinkedIn user")


def make_tools(settings, memory) -> list[Tool]:
    async def linkedin_post(text: str) -> str:
        creds = json.loads(memory.get("linkedin") or "null")
        if not creds:
            return "ERROR: LinkedIn not connected. Open http://localhost:8000/linkedin/login once to connect."
        if time.time() > creds["expires"]:
            return "ERROR: LinkedIn token expired (they last ~60 days). Re-open /linkedin/login."
        body = {
            "author": f"urn:li:person:{creds['sub']}",
            "lifecycleState": "PUBLISHED",
            "specificContent": {"com.linkedin.ugc.ShareContent": {
                "shareCommentary": {"text": text}, "shareMediaCategory": "NONE"}},
            "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
        }
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post("https://api.linkedin.com/v2/ugcPosts", json=body,
                             headers={"Authorization": f"Bearer {creds['token']}",
                                      "X-Restli-Protocol-Version": "2.0.0"})
        if r.status_code >= 400:
            return f"ERROR: LinkedIn returned {r.status_code}: {r.text[:200]}"
        return f"Posted to LinkedIn ({r.headers.get('x-restli-id', 'ok')})."

    def always_confirm(args: dict) -> str:
        return f"📝 Publish this to LinkedIn?\n\n{args.get('text', '')}"

    return [Tool("linkedin_post", "Publish a finished post to the user's LinkedIn. Only use when the user explicitly "
                 "asks to post/publish; show drafts as normal replies first.",
                 params(["text"], text={"type": "string"}), linkedin_post, confirm=always_confirm)]
