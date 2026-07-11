from __future__ import annotations

DUELIST = "Duelist"
INITIATOR = "Initiator"
CONTROLLER = "Controller"
SENTINEL = "Sentinel"

_AGENTS = {
    "Jett":      ("add6443a-41bd-e414-f6ad-e58d267f4e95", DUELIST,    "#9ADEFF"),
    "Phoenix":   ("eb93336a-449b-9c1b-0a54-a891f7921d69", DUELIST,    "#FE8266"),
    "Reyna":     ("a3bfb853-43b2-7238-a4f1-ad90e9e46bcc", DUELIST,    "#B565B5"),
    "Raze":      ("f94c3b30-42be-e959-889c-5aa313dba261", DUELIST,    "#FFA400"),
    "Yoru":      ("7f94d92c-4234-0a36-9646-3a87eb8b5c89", DUELIST,    "#2846C8"),
    "Neon":      ("bb2a4828-46eb-8cd1-e765-15848195d751", DUELIST,    "#00CFFF"),
    "Iso":       ("0e38b510-41a8-5780-5e8f-568b2a4f2d6c", DUELIST,    "#574AC2"),
    "Waylay":    ("df1cb487-4902-002e-5c17-d28e83e78588", DUELIST,    "#82C3EB"),

    "Sova":      ("320b2a48-4d9b-a075-30f1-1f93a9b638fa", INITIATOR,  "#3BA0E5"),
    "Breach":    ("5f8d3a7f-467b-97f3-062c-13acf203c006", INITIATOR,  "#C76B3B"),
    "Skye":      ("6f2a04ca-43e0-be17-7f36-b3908627744d", INITIATOR,  "#C0E69E"),
    "KAY/O":     ("601dbbe7-43ce-be57-2a40-4abd24953621", INITIATOR,  "#85929C"),
    "Fade":      ("dade69b4-4f5a-8528-247b-219e5a1facd6", INITIATOR,  "#5C5C5E"),
    "Gekko":     ("e370fa57-4757-3604-3648-499e1f642d3f", INITIATOR,  "#A8E65E"),
    "Tejo":      ("b444168c-4e35-8076-db47-ef9bf368f384", INITIATOR,  "#FFB761"),

    "Brimstone": ("9f0d8ba9-4140-b941-57d3-a7ad57c6b417", CONTROLLER, "#D1691F"),
    "Omen":      ("8e253930-4c05-31dd-1b6c-968525494517", CONTROLLER, "#47508F"),
    "Viper":     ("707eab51-4836-f488-046a-cda6bf494859", CONTROLLER, "#38C659"),
    "Astra":     ("41fb69c1-4189-7b37-f117-bcaf1e96f1bf", CONTROLLER, "#712AE8"),
    "Harbor":    ("95b78ed7-4637-86d9-7e41-71ba8c293152", CONTROLLER, "#008080"),
    "Clove":     ("1dbf2edd-4729-0984-3115-daa5eed44993", CONTROLLER, "#F28FD0"),
    "Miks":      ("7c8a4701-4de6-9355-b254-e09bc2a34b72", CONTROLLER, "#6B4BB0"),

    "Sage":      ("569fdd95-4d10-43ab-ca70-79becc718b46", SENTINEL,   "#26C8AF"),
    "Cypher":    ("117ed9e3-49f3-6512-3ccf-0cada7e3823b", SENTINEL,   "#E6D9C5"),
    "Killjoy":   ("1e58de9c-4950-5125-93e9-a0aee9f98746", SENTINEL,   "#FFD91F"),
    "Chamber":   ("22697a3d-45bf-8dd7-4fec-84a9e28c69d7", SENTINEL,   "#B89A46"),
    "Deadlock":  ("cc8b64c8-4b25-4ff9-6e7f-37b4da43d235", SENTINEL,   "#6677B0"),
    "Vyse":      ("efba5359-4016-a1e5-7626-b1ae76895940", SENTINEL,   "#656B8B"),
    "Veto":      ("92eeef5d-43b5-1d4a-8d03-b3927a09034b", SENTINEL,   "#2E8A96"),
}

CDN = "https://media.valorant-api.com/agents"

def _portrait(uuid: str) -> str:
    return f"{CDN}/{uuid}/displayicon.png"

def _full(uuid: str) -> str:
    return f"{CDN}/{uuid}/fullportrait.png"

AGENTS = [
    {
        "name": name,
        "uuid": uuid,
        "role": role,
        "color": color,
        "portrait": _portrait(uuid),
        "fullPortrait": _full(uuid),
    }
    for name, (uuid, role, color) in _AGENTS.items()
]

AGENT_BY_NAME = {a["name"].lower(): a for a in AGENTS}
AGENT_BY_UUID = {a["uuid"].lower(): a for a in AGENTS}
NAME_TO_UUID = {name: uuid for name, (uuid, _r, _c) in _AGENTS.items()}
UUID_TO_NAME = {uuid.lower(): name for name, (uuid, _r, _c) in _AGENTS.items()}

def resolve_agent(identifier: str) -> dict | None:
    pass
    if not identifier:
        return None
    key = identifier.strip().lower()
    return AGENT_BY_NAME.get(key) or AGENT_BY_UUID.get(key)

def role_of(name_or_uuid: str) -> str:
    agent = resolve_agent(name_or_uuid)
    return agent["role"] if agent else "Flex"

def color_of(name_or_uuid: str) -> str:
    agent = resolve_agent(name_or_uuid)
    return agent["color"] if agent else "#FF4655"
