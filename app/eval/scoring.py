"""Pass/fail rules for one eval case, given the tool calls the model made and its final reply."""
MISSING = object()


def get_path(d, path: str):
    for part in path.split("."):
        if not isinstance(d, dict) or part not in d:
            return MISSING
        d = d[part]
    return d


def match_value(actual, expected) -> bool:
    if actual is MISSING:
        return False
    if isinstance(expected, dict):
        text = str(actual).lower()
        if "contains" in expected:
            return expected["contains"].lower() in text
        if "contains_any" in expected:
            return any(e.lower() in text for e in expected["contains_any"])
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        try:
            return abs(float(actual) - expected) < 0.01
        except (TypeError, ValueError):
            return False
    return str(actual).strip().lower() == str(expected).lower()


def call_matches(call: dict, exp: dict) -> bool:
    names = exp["tool"] if isinstance(exp["tool"], list) else [exp["tool"]]
    return call["name"] in names and all(match_value(get_path(call["args"], k), v)
                                         for k, v in exp.get("args", {}).items())


def expected_names(case: dict) -> set[str]:
    names = set()
    for exp in case.get("expect", []):
        names.update(exp["tool"] if isinstance(exp["tool"], list) else [exp["tool"]])
    return names


def score(case: dict, calls: list[dict], reply: str, confirmed: bool, error: str | None) -> dict:
    reasons = []
    if error:
        reasons.append(f"error: {error}")
    for exp in case.get("expect", []):
        if not any(call_matches(c, exp) for c in calls):
            reasons.append(f"missing call {exp['tool']} {exp.get('args', '')}")
    called = [c["name"] for c in calls]
    for name in case.get("forbid", []):
        if name in called:
            reasons.append(f"forbidden call {name}")
    if case.get("no_tools") and calls:
        reasons.append(f"unexpected tools {called}")
    if case.get("confirm") and not confirmed:
        reasons.append("confirmation gate not reached")
    low = (reply or "").lower()
    if "reply_contains_any" in case and not any(s.lower() in low for s in case["reply_contains_any"]):
        reasons.append(f"reply lacks any of {case['reply_contains_any']}")
    if len(reply or "") < case.get("min_reply_chars", 0):
        reasons.append("reply too short")

    wanted = expected_names(case)
    if case.get("no_tools"):
        first_ok = not calls
    elif wanted:
        first_ok = bool(calls) and calls[0]["name"] in wanted
    else:
        first_ok = not calls or calls[0]["name"] not in case.get("forbid", [])
    return {"passed": not reasons, "reasons": reasons, "first_tool_ok": first_ok}
