import requests
import json

BASE_URL = "http://localhost:8080/v1"

def print_res(label, res):
    print(f"\n{'='*50}")
    print(f"  {label}")
    print(f"  Status: {res.status_code}")
    try:
        print(json.dumps(res.json(), indent=2, ensure_ascii=False))
    except:
        print(res.text)
    print(f"{'='*50}")

# ---- CORE ENDPOINTS ----
print_res("GET /healthz", requests.get(f"{BASE_URL}/healthz"))
print_res("GET /metadata", requests.get(f"{BASE_URL}/metadata"))

# ---- POST /context (various scopes) ----
def add_merchant(mid, category_slug, name, locality):
    return requests.post(f"{BASE_URL}/context", json={
        "scope": "merchant",
        "context_id": mid,
        "version": 1,
        "payload": {
            "category_slug": category_slug,
            "identity": {"name": name, "locality": locality},
            "offers": [{"title": f"{name} Special"}]
        },
        "delivered_at": "2026-05-01T10:00:00Z"
    })

def add_trigger(tid, merchant_id, kind="generic"):
    return requests.post(f"{BASE_URL}/context", json={
        "scope": "trigger",
        "context_id": tid,
        "version": 1,
        "payload": {"kind": kind, "merchant_id": merchant_id},
        "delivered_at": "2026-05-01T10:05:00Z"
    })

# Seed all 5 categories
categories = [
    ("m_dentist",    "dentists",     "Dr. Priya Sharma",  "Lajpat Nagar"),
    ("m_salon",      "salons",       "GlamourCuts",       "Saket"),
    ("m_restaurant", "restaurants",  "Pizza Hub",         "Connaught Place"),
    ("m_gym",        "gyms",         "FitZone",           "Vasant Kunj"),
    ("m_pharmacy",   "pharmacies",   "MedPlus",           "Hauz Khas"),
]

for mid, slug, name, loc in categories:
    res = add_merchant(mid, slug, name, loc)
    print_res(f"POST /context → merchant [{slug}]", res)

for i, (mid, slug, name, loc) in enumerate(categories):
    res = add_trigger(f"t_{i+1}", mid)
    print_res(f"POST /context → trigger for [{slug}]", res)

# ---- POST /tick: all 5 triggers ----
tick_res = requests.post(f"{BASE_URL}/tick", json={
    "now": "2026-05-01T18:00:00Z",
    "available_triggers": ["t_1", "t_2", "t_3", "t_4", "t_5"]
})
print_res("POST /tick — All 5 verticals", tick_res)

# ---- POST /reply ----
print_res("POST /reply — Generic reply", requests.post(f"{BASE_URL}/reply", json={
    "conversation_id": "conv_m_restaurant_t_3",
    "merchant_id": "m_restaurant",
    "from_role": "merchant",
    "message": "Sounds good, how do I start?",
    "received_at": "2026-05-01T18:05:00Z",
    "turn_number": 1
}))

print_res("POST /reply — Price objection", requests.post(f"{BASE_URL}/reply", json={
    "conversation_id": "conv_m_salon_t_2",
    "merchant_id": "m_salon",
    "from_role": "merchant",
    "message": "too expensive for me",
    "received_at": "2026-05-01T18:06:00Z",
    "turn_number": 2
}))

print_res("POST /reply — Stop", requests.post(f"{BASE_URL}/reply", json={
    "conversation_id": "conv_m_gym_t_4",
    "merchant_id": "m_gym",
    "from_role": "merchant",
    "message": "please stop",
    "received_at": "2026-05-01T18:07:00Z",
    "turn_number": 3
}))
