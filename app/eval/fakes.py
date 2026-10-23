"""Deterministic stand-ins for every tool, keeping the real names, schemas and confirmation rules,
so the eval measures the model's decisions rather than the network."""
import json

from assistant.tools import build_tools
from assistant.tools.home import match_entities, service_error, summarise_state
from assistant.tools.web import UNTRUSTED


def _e(entity_id, state, name, **attrs):
    return {"entity_id": entity_id, "state": state, "attributes": {"friendly_name": name, **attrs}}


ENTITIES = [
    _e("light.kitchen", "on", "Kitchen Light", brightness=200),
    _e("light.living_room", "off", "Living Room Lamp"),
    _e("light.bedroom", "off", "Bedroom Ceiling Light"),
    _e("switch.coffee_machine", "off", "Coffee Machine"),
    _e("climate.hallway", "heat", "Hallway Thermostat", current_temperature=19.5, temperature=20, hvac_mode="heat"),
    _e("sensor.outdoor_temperature", "12.3", "Outdoor Temperature", unit_of_measurement="°C"),
    _e("binary_sensor.front_door_contact", "off", "Front Door Contact"),
    _e("lock.front_door", "locked", "Front Door Lock"),
    _e("cover.garage_door", "closed", "Garage Door"),
    _e("media_player.living_room_tv", "playing", "Living Room TV", media_title="The Great British Bake Off"),
]
BY_ID = {e["entity_id"]: e for e in ENTITIES}

ARTICLE = ("Running agents on CPU. A 4B-parameter model quantised to 4 bits runs at roughly 8 tokens per second "
           "on an Intel N100 mini PC, which is fast enough for a chat assistant. Tool calling works well when tools "
           "are few and their schemas are short.")


def build_fake_tools(settings, memory, overrides: dict | None = None):
    overrides = overrides or {}
    tools = build_tools(settings, memory)  # real schemas + confirm rules
    allowed = settings.ha_allowed

    async def web_search(query: str, category: str = "general", time_range: str = "") -> str:
        if "web_search" in overrides:
            return overrides["web_search"]
        return UNTRUSTED + json.dumps([
            {"title": f"{query.title()}: what changed this week", "url": "https://example.com/story-1",
             "snippet": f"Analysis of the latest developments in {query}.", "date": "2026-10-22"},
            {"title": f"Five takeaways on {query}", "url": "https://example.com/story-2",
             "snippet": "Practitioners share lessons learned.", "date": "2026-10-21"}])

    async def find_events(topic: str, location: str = "") -> str:
        where = location or settings.location
        return UNTRUSTED + json.dumps([
            {"title": f"{topic.title()} Meetup {where}", "url": "https://example.com/meetup",
             "snippet": f"Monthly {topic} meetup in {where}, 12 November, 6:30pm."}])

    async def read_webpage(url: str) -> str:
        return overrides.get("read_webpage", UNTRUSTED + ARTICLE)

    async def ha_find_entities(query: str = "", domain: str = "") -> str:
        hits = match_entities(ENTITIES, query, domain, allowed)
        return json.dumps(hits) if hits else "No matching entities."

    async def ha_get_state(entity_id: str) -> str:
        if entity_id not in BY_ID:
            return "ERROR: unknown entity. Use ha_find_entities first."
        return json.dumps(summarise_state(BY_ID[entity_id]))

    async def ha_call_service(domain: str, service: str, entity_id: str, data: dict | None = None) -> str:
        if err := service_error(domain, service, entity_id, allowed):
            return err
        if entity_id not in BY_ID:
            return "ERROR: unknown entity. Use ha_find_entities first."
        return f"Called {domain}.{service} on {entity_id}."

    async def system_status() -> str:
        return json.dumps({"load_avg": ["0.80", "0.65", "0.50"], "mem_used_pct": 61.0, "mem_total_gb": 15.5,
                           "disk_used_pct": 42.0, "disk_free_gb": 270.0, "cpu_temp_c": 48.0, "uptime_days": 12.3})

    async def linkedin_post(text: str) -> str:
        return "Posted to LinkedIn (eval)."

    fakes = {f.__name__: f for f in (web_search, find_events, read_webpage, ha_find_entities, ha_get_state,
                                     ha_call_service, system_status, linkedin_post)}
    for name, tool in tools.items():
        tool.func = fakes[name]
    return tools
