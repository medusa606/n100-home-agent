"""Home Assistant over its REST API, restricted to an allowlist of domains."""
import json

import httpx

from . import Tool, params


def domain_of(entity_id: str) -> str:
    return entity_id.split(".", 1)[0]


def match_entities(states: list[dict], query: str, domain: str, allowed: set[str], limit: int = 15) -> list[dict]:
    """Rank entities by how many query words appear in their id/name ('kitchen lights' finds 'Kitchen Light')."""
    words = [w.rstrip("s") if len(w) > 3 else w for w in query.lower().split()]
    scored = []
    for s in states:
        eid, d = s["entity_id"], domain_of(s["entity_id"])
        if d not in allowed or (domain and d != domain):
            continue
        name = s.get("attributes", {}).get("friendly_name", "")
        hay = f"{eid} {name}".lower().replace("_", " ")
        score = sum(w in hay for w in words)
        if score or not words:
            scored.append((score, {"entity_id": eid, "name": name, "state": s["state"]}))
    scored.sort(key=lambda x: -x[0])
    return [e for _, e in scored[:limit]]


def service_error(domain: str, service: str, entity_id: str, allowed: set[str]) -> str | None:
    d = domain_of(entity_id)
    if d not in allowed or domain not in (d, "homeassistant"):
        return f"ERROR: not allowed to call {domain}.{service} on {entity_id}."
    return None


def make_tools(settings) -> list[Tool]:
    client = httpx.AsyncClient(base_url=settings.ha_url.rstrip("/"), timeout=15,
                               headers={"Authorization": f"Bearer {settings.ha_token}"})
    allowed, sensitive = settings.ha_allowed, settings.ha_confirm

    async def ha_find_entities(query: str = "", domain: str = "") -> str:
        r = await client.get("/api/states")
        r.raise_for_status()
        hits = match_entities(r.json(), query, domain, allowed)
        return json.dumps(hits) if hits else "No matching entities."

    async def ha_get_state(entity_id: str) -> str:
        if domain_of(entity_id) not in allowed:
            return f"ERROR: domain '{domain_of(entity_id)}' is not allowed."
        r = await client.get(f"/api/states/{entity_id}")
        if r.status_code == 404:
            return "ERROR: unknown entity. Use ha_find_entities first."
        r.raise_for_status()
        return json.dumps(summarise_state(r.json()))

    async def ha_call_service(domain: str, service: str, entity_id: str, data: dict | None = None) -> str:
        if err := service_error(domain, service, entity_id, allowed):
            return err
        r = await client.post(f"/api/services/{domain}/{service}", json={"entity_id": entity_id, **(data or {})})
        if r.status_code >= 400:
            return f"ERROR: Home Assistant returned {r.status_code}: {r.text[:200]}"
        return f"Called {domain}.{service} on {entity_id}."

    def confirm_service(args: dict) -> str | None:
        if domain_of(args.get("entity_id", "")) in sensitive or args.get("domain") in sensitive:
            return f"🏠 About to call *{args.get('domain')}.{args.get('service')}* on `{args.get('entity_id')}`."
        return None

    return [
        Tool("ha_find_entities", "Search Home Assistant devices by name words (e.g. 'kitchen light') and/or domain. "
             "Always use this to get the exact entity_id before controlling anything.",
             params(query={"type": "string"}, domain={"type": "string"}), ha_find_entities),
        Tool("ha_get_state", "Get the current state of one Home Assistant entity.",
             params(["entity_id"], entity_id={"type": "string"}), ha_get_state),
        Tool("ha_call_service", "Control a device, e.g. domain=light service=turn_on entity_id=light.kitchen "
             "data={\"brightness_pct\": 50}. Or domain=climate service=set_temperature data={\"temperature\": 20}.",
             params(["domain", "service", "entity_id"], domain={"type": "string"}, service={"type": "string"},
                    entity_id={"type": "string"}, data={"type": "object"}),
             ha_call_service, confirm=confirm_service),
    ]


def summarise_state(s: dict) -> dict:
    attrs = {k: v for k, v in s.get("attributes", {}).items()
             if k in ("friendly_name", "unit_of_measurement", "current_temperature", "temperature",
                      "brightness", "hvac_mode", "media_title", "battery_level")}
    return {"entity_id": s["entity_id"], "state": s["state"], **attrs}
