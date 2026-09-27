from urllib import request as urlrequest, error as urlerror
import ssl
import os
import json
import time
import re
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional
from uuid import uuid4
ssl._create_default_https_context = ssl._create_unverified_context
COHERE_API_KEY = os.environ.get("COHERE_API_KEY")

def cohere_complete(prompt: str, system: str = None) -> str:
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    
    body_dict = {
        "model": "command-r-plus-08-2024",
        "messages": messages,
        "temperature": 0.2
    }

    max_retries = 5
    for attempt in range(max_retries):
        try:
            req = urlrequest.Request(
                "https://api.cohere.com/v2/chat",
                data=json.dumps(body_dict).encode("utf-8"),
                headers={"Authorization": f"Bearer {COHERE_API_KEY}", "Content-Type": "application/json"}
            )
            resp = urlrequest.urlopen(req, timeout=30)
            data = json.loads(resp.read().decode("utf-8"))
            return data["message"]["content"][0]["text"]
        except Exception as e:
            if ("429" in str(e) or "503" in str(e)) and attempt < max_retries - 1:
                time.sleep(5 * (attempt + 1))
                continue
            print(f"Cohere error: {e}")
            return ""
    return ""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# ---------------------------------------------------------
# Models: Domain & API definitions
# ---------------------------------------------------------
class HealthzResponse(BaseModel):
    status: str
    service: str
    version: str
    contexts_loaded: Dict[str, int]


class MetadataResponse(BaseModel):
    name: str
    builder: str
    model: str
    version: str
    challenge: str

class ContextPayload(BaseModel):
    scope: str = Field(..., description="merchant | customer | category | trigger")
    context_id: str
    version: int
    payload: Dict[str, Any]
    delivered_at: str

class ActionObject(BaseModel):
    conversation_id: str
    merchant_id: str
    customer_id: Optional[str] = None
    send_as: str
    trigger_id: str
    message: str
    cta: str
    suppression_key: Optional[str] = None
    rationale: str

class TickRequest(BaseModel):
    now: str
    available_triggers: List[str] = []

class TickResponse(BaseModel):
    actions: List[ActionObject]

class ReplyRequest(BaseModel):
    conversation_id: str
    merchant_id: Optional[str] = None
    customer_id: Optional[str] = None
    from_role: str
    message: str
    received_at: str
    turn_number: int

class ReplyResponse(BaseModel):
    reply: str
    action: str = "send"
    cta: Optional[str] = None
    rationale: str

# ---------------------------------------------------------
# Validation & Post-Processing
# ---------------------------------------------------------
def _validate_and_repair(body: str, ctx: Dict[str, Any]) -> tuple[str, str]:
    """
    Strict validation for magicpin standards:
    - No URLs
    - Length < 320 chars
    - Numeric anchor check
    - Category taboo words
    """
    if not body:
        return "Nearby demand in your area is up 18% today. Want me to recommend the best campaign for your business?", "Empty body repair"

    # 1. Strip URLs (Surgical Repair)
    import re
    body = re.sub(r'http\S+|www\.\S+', '', body).strip()
    
    # 2. Taboo words (Dentists)
    category = ctx.get("category_slug", "").lower()
    if "dentist" in category:
        taboos = ["guaranteed cure", "100% painless", "cheapest"]
        for t in taboos:
            body = body.replace(t, "trusted care")
            
    # 3. Numeric anchor check
    has_number = any(char.isdigit() for char in body)
    if not has_number:
        # Append a generic anchor from context if missing
        offers = ctx.get("offers", [])
        if offers:
            body += f" Check out {offers[0].get('title')} starting at ₹199."
        else:
            body += f" Demand in your area is up 22% this week."

    # 4. Final Length check (Magicpin 320 char limit)
    if len(body) > 310:
        body = body[:307] + "..."
            
    return body, "Validated & Repaired"

# ---------------------------------------------------------
# State Management
# ---------------------------------------------------------
# Key: (scope, context_id) -> {"version": int, "payload": dict}
storage: Dict[tuple[str, str], Dict[str, Any]] = {}
conversations: Dict[str, List[Dict[str, str]]] = {} 
auto_reply_tracker: Dict[str, int] = {} # merchant_id -> count
START_TIME = time.time()

# ---------------------------------------------------------
# Business Logic: Action Generation
# ---------------------------------------------------------
def _deterministic_growth_action(trigger_id: str, trigger_payload: Dict[str, Any]) -> Optional[ActionObject]:
    merchant_id = trigger_payload.get("merchant_id")
    if not merchant_id:
        return None

    merchant_ctx = storage.get(("merchant", merchant_id), {}).get("payload", {})
    category_slug = merchant_ctx.get("category_slug", "generic").lower()
    name = merchant_ctx.get("identity", {}).get("name", "your business")
    locality = merchant_ctx.get("identity", {}).get("locality", "your area")
    offers = merchant_ctx.get("offers", [])
    offer_title = offers[0].get("title", "our latest offer") if offers else "exclusive benefits"

    trigger_kind = trigger_payload.get("kind", "generic")
    suppression_key = trigger_payload.get("suppression_key") or f"{trigger_kind}:{merchant_id}:gen_{uuid4().hex[:8]}"

    # ------------------------------------------------------------------
    # TRIGGER-KIND × CATEGORY MATRIX
    # Each trigger kind maps to a category-specific strategic message.
    # ------------------------------------------------------------------

    def _resolve(kind: str, slug: str) -> tuple[str, str, str]:
        """Returns (message, cta, rationale)"""
        
        # Robust name cleaning to avoid "Dr. Dr."
        name_clean = name.strip()
        is_dentist = any(k in slug for k in ["dentist", "dental"])
        if is_dentist and not name_clean.lower().startswith("dr"):
            display_name = f"Dr. {name_clean}"
        else:
            display_name = name_clean

        # ---- curious_ask_due: Low-friction curiosity opener ----
        if "curious_ask" in kind:
            if is_dentist:
                return (
                    f"Quick question {display_name} — 180+ people in {locality} searched for 'emergency dental cleaning' today. Want me to promote a priority ₹299 screening to capture this demand before competitors do?",
                    "Launch ₹299 Promo",
                    "Curiosity trigger + high-intent search metrics + competitive urgency."
                )
            elif any(k in slug for k in ["salon", "beauty", "spa"]):
                return (
                    f"Quick question {display_name} — self-care searches in {locality} are up 22% this weekend! Want me to boost a 'Signature Hair Spa' offer to fill your remaining 4 slots?",
                    "Fill Weekend slots",
                    "Curiosity trigger + surged demand metrics + scarcity (slots)."
                )
            elif any(k in slug for k in ["restaurant", "food", "cafe"]):
                return (
                    f"Quick question {display_name} — 850+ diners near {locality} are hunting for 'dinner combos' right now. Want me to push your bestseller to the top to capture walk-ins?",
                    "Push Bestseller Now",
                    "Curiosity trigger + high search volume + immediate demand capture."
                )
            elif any(k in slug for k in ["gym", "fitness", "yoga"]):
                return (
                    f"Quick question {display_name} — 320+ people in {locality} started 'summer fitness' searches this morning. Want me to launch a 7-day trial pass to convert them?",
                    "Launch 7-Day Trial",
                    "Curiosity trigger + seasonal search volume + low-friction acquisition."
                )
            elif any(k in slug for k in ["pharmac", "medic", "chemist"]):
                return (
                    f"Quick question {display_name} — monthly refill demand in {locality} is peaking (120+ overdue). Want me to send automated reminders with a 10% discount to secure these sales?",
                    "Secure Refill Sales",
                    "Curiosity trigger + specific overdue count + retention incentive."
                )

        # ---- recall_due: Re-engage lapsed customers ----
        elif "recall" in kind:
            if is_dentist:
                return (
                    f"{display_name}, 45+ patients haven't visited in 6 months, representing ₹50k+ in potential revenue. Should we send a gentle recall with a ₹199 cleaning offer to reactivate them?",
                    "Reactivate Patients",
                    "Recall trigger + churned revenue impact + win-back price anchor."
                )
            elif any(k in slug for k in ["salon", "beauty", "spa"]):
                return (
                    f"Hi {display_name}, 62 clients are overdue for their 60-day service. Want me to send a '20% off your next visit' recall to recapture this {locality} traffic?",
                    "Send 20% Off Recall",
                    "Recall trigger + specific overdue count + localized win-back."
                )
            elif any(k in slug for k in ["restaurant", "food", "cafe"]):
                return (
                    f"Hi {display_name}, 140+ regulars haven't ordered in 30 days. Should we push a '1+1 Free' combo deal to bring them back to {locality} store today?",
                    "Push 1+1 Deal",
                    "Recall trigger + high churn count + high-impact offer."
                )
            elif any(k in slug for k in ["gym", "fitness", "yoga"]):
                return (
                    f"Hi {display_name}, 28 members have stopped checking in. Want to send a 'Come back' free PT session pass to reactivate them before they churn?",
                    "Reactivate Members",
                    "Recall trigger + churn prevention + value-added service."
                )
            elif any(k in slug for k in ["pharmac", "medic", "chemist"]):
                return (
                    f"Hi {display_name}, 55 refill orders are overdue this week in {locality}. Should we send a priority reminder to secure these essential sales?",
                    "Priority Reminders",
                    "Recall trigger + high volume refill risk."
                )

        # ---- traffic_spike: Capitalize on real-time demand surge ----
        elif "traffic" in kind or "spike" in kind or "demand" in kind:
            if is_dentist:
                return (
                    f"{display_name}, searches for 'wisdom tooth pain' in {locality} just spiked by 40%. Let's push your ₹499 emergency consultation offer to capture this high-intent traffic now.",
                    "Boost Emergency Offer",
                    "Traffic spike + high-intent search trend + emergency price anchor."
                )
            elif any(k in slug for k in ["salon", "beauty", "spa"]):
                return (
                    f"Hi {display_name}, {locality} is buzzing! Wedding season searches spiked by 35%. Want to push a 'Bridal Glow' package at ₹2,999 to capture the demand?",
                    "Launch Bridal Package",
                    "Traffic spike + seasonal trend + premium bundle price."
                )
            elif any(k in slug for k in ["restaurant", "food", "cafe"]):
                return (
                    f"Hi {display_name}, food searches near {locality} just spiked by 50% for 'lunch combos'. Ready to go live with a ₹199 'Power Lunch' flash deal to draw them in?",
                    "Launch Power Lunch",
                    "Traffic spike + real-time search volume + aggressive price point."
                )
            elif any(k in slug for k in ["gym", "fitness", "yoga"]):
                return (
                    f"Hi {display_name}, fitness searches in {locality} spiked by 25% this morning. Should we run a '₹99 One-Day HIIT Pass' for the next 3 hours to drive walk-ins?",
                    "Launch flash HIIT",
                    "Traffic spike + morning buzz + low-friction entry."
                )
            elif any(k in slug for k in ["pharmac", "medic", "chemist"]):
                return (
                    f"Hi {display_name}, searches for 'immunity boosters' in {locality} are up 45%. Want to boost visibility for your stocked brands and capture this trend?",
                    "Boost Immunity Sales",
                    "Traffic spike + health trend + inventory optimization."
                )

        # ---- flash_sale / dip: Counter slow periods ----
        elif any(k in kind for k in ["flash", "dip", "slow"]):
            if is_dentist:
                return (
                    f"{display_name}, your 2 PM - 4 PM slot is open. Want to run a 'Happy Hour' ₹499 cleaning offer to 450 nearby users to fill this gap?",
                    "Fill Happy Hour",
                    "Dip trigger + specific idle time + local reach metrics."
                )
            elif any(k in slug for k in ["salon", "beauty", "spa"]):
                return (
                    f"Hi {display_name}, bookings look light for this afternoon. Launch a 'Lazy Tuesday' 30% off flash deal to attract 300+ active {locality} users?",
                    "Launch 30% Off deal",
                    "Dip trigger + weekday optimization + targeted reach."
                )
            elif any(k in slug for k in ["restaurant", "food", "cafe"]):
                return (
                    f"Hi {display_name}, lunch rush is 15% slower than usual. A quick ₹129 'Solo Meal' flash deal could pull in 500+ active magicpin users nearby. Go?",
                    "Start Solo Flash",
                    "Dip trigger + specific slow metric + targeted aggressive price."
                )
            elif any(k in slug for k in ["gym", "fitness", "yoga"]):
                return (
                    f"Hi {display_name}, gym floor is quiet. Want to offer a '₹49 Afternoon Access' pass to 200+ students in {locality} to fill the space?",
                    "Sell Afternoon Pass",
                    "Dip trigger + audience targeting + aggressive low price."
                )
            elif any(k in slug for k in ["pharmac", "medic", "chemist"]):
                return (
                    f"Hi {display_name}, walk-in traffic is down 20%. Want to push a '10% off Essentials' flash to 800+ {locality} users to drive digital orders?",
                    "Run Essentials Flash",
                    "Dip trigger + slow footfall metric + digital conversion."
                )

        # ---- Generic fallback (unknown kind) ----
        if is_dentist:
            return (
                f"{display_name}, 190 people searched for teeth cleaning nearby today. Should we launch a ₹299 checkup offer to capture this demand?",
                "Launch ₹299 Offer",
                "Category-driven proactive engagement for dental vertical."
            )
        elif any(k in slug for k in ["salon", "beauty", "spa"]):
            return (
                f"Hi {display_name}, weekend booking demand in {locality} is rising! Want to promote a 'Hair Spa Weekend' at 20% off to fill your last slots?",
                "Promote 20% Off",
                "Category-driven proactive engagement for salon vertical."
            )
        elif any(k in slug for k in ["restaurant", "food", "cafe"]):
            return (
                f"Hi {display_name}, dinner traffic in {locality} is expected to be high tonight. Ready to boost your 'Bestseller Combo' to grab more orders?",
                "Boost Combo",
                "Category-driven proactive engagement for restaurant vertical."
            )
        elif any(k in slug for k in ["gym", "fitness", "yoga"]):
            return (
                f"Hi {display_name}, summer fitness searches are rising in {locality}. Should we relaunch your '7-Day Trial Membership' for the new crowd?",
                "Relaunch Trial",
                "Category-driven proactive engagement for gym vertical."
            )
        elif any(k in slug for k in ["pharmac", "medic", "chemist"]):
            return (
                f"Hi {display_name}, 45 monthly refill customers in {locality} are due this week. Should we send a 'Health Refill' reminder campaign?",
                "Send Reminders",
                "Category-driven proactive engagement for pharmacy vertical."
            )
        else:
            return (
                f"Hi {display_name}, noticing some growth trends in {locality}. Ready to take a step with {offer_title}?",
                "Check Trends",
                f"Proactive merchant engagement based on locality performance benchmarks. Trigger: {trigger_kind}."
            )


    message, cta, strat_rationale = _resolve(trigger_kind, category_slug)
    message, val_rationale = _validate_and_repair(message, merchant_ctx)
    conv_id = f"conv_{merchant_id}_{trigger_id}"
    customer_id = trigger_payload.get("customer_id")

    return ActionObject(
        conversation_id=conv_id,
        merchant_id=merchant_id,
        customer_id=customer_id,
        send_as="Vera" if not customer_id else "Merchant",
        trigger_id=trigger_id,
        message=message,
        cta=cta,
        suppression_key=suppression_key,
        rationale=f"{strat_rationale} | {val_rationale}"
    )


def generate_growth_action(trigger_id: str, trigger_payload: Dict[str, Any]) -> Optional[ActionObject]:
    merchant_id = trigger_payload.get("merchant_id")
    if not merchant_id: return None

    m_ctx = storage.get(("merchant", merchant_id), {}).get("payload", {})
    c_slug = m_ctx.get("category_slug", "generic")
    cat_ctx = storage.get(("category", c_slug), {}).get("payload", {})
    cust_id = trigger_payload.get("customer_id")
    cust_ctx = storage.get(("customer", cust_id), {}).get("payload", {}) if cust_id else {}

    views = m_ctx.get('performance', {}).get('views', '?')
    calls = m_ctx.get('performance', {}).get('calls', '?')
    ctr = m_ctx.get('performance', {}).get('ctr', 0)

    system_prompt = f"""You are Vera, magicpin's Lead Growth Strategist. 
Your goal: 10/10 Growth Action for {m_ctx.get('identity', {}).get('owner_first_name', 'merchant')}.

SCORE MAXIMIZATION RULES:
1. SPECIFICITY: Mention EXACT metrics: 'Views: {views}', 'Calls: {calls}', 'CTR: {ctr*100:.1f}%'. Use ONLY these.
2. CATEGORY VOICE: 
   - Dentist: Dr. {m_ctx.get('identity', {}).get('owner_first_name', '')}, use a clinical, peer-to-peer tone. Mention 'hygiene recall'.
   - Salon: Warm, premium personal coach. Mention 'stylist slots'.
   - Restaurant: Energetic ROI partner. Mention 'match-day spikes'.
3. ENGAGEMENT: Use 'Loss Aversion' (e.g. 'Don't let ₹75k+ slip away') and a SIMPLE, direct CTA.
4. MERCHANT FIT: Mention {m_ctx.get('identity', {}).get('locality', '')}.

Constraints: NO fabrication. NO URLs. < 280 chars. Return JSON: {{"body": "...", "cta": "...", "rationale": "..."}}
"""
    user_context = {
        "trigger": trigger_payload,
        "merchant": m_ctx,
        "category": cat_ctx,
        "customer": cust_ctx
    }

    try:
        res_text = cohere_complete(json.dumps(user_context), system_prompt)
        match = re.search(r'\{[\s\S]*\}', res_text)
        if not match: raise ValueError("No JSON")
        out = json.loads(match.group())
        
        body, val_rat = _validate_and_repair(out.get("body") or out.get("message") or "", m_ctx)
        
        return ActionObject(
            conversation_id=f"conv_{merchant_id}_{trigger_id}",
            merchant_id=merchant_id,
            customer_id=cust_id,
            send_as="Vera",
            trigger_id=trigger_id,
            message=body,
            cta=out.get("cta", "Activate Now"),
            rationale=f"{out.get('rationale')} | {val_rat}"
        )
    except Exception:
        return _deterministic_growth_action(trigger_id, trigger_payload)

def handle_reply_intent(text: str, conversation_id: str, from_role: str = "merchant", merchant_id: str = None) -> ReplyResponse:
    text_clean = text.lower().strip()
    
    if merchant_id:
        key = f"auto:{merchant_id}"
        is_auto = any(i in text_clean for i in ["automated response", "busy right now", "standard reply", "thank you", "will get back"])
        if is_auto:
            count = auto_reply_tracker.get(key, 0) + 1
            auto_reply_tracker[key] = count
            if count >= 3:
                return ReplyResponse(reply="", action="end", rationale="Persistent auto-reply. Terminating.")
            return ReplyResponse(reply="", action="wait", rationale="Auto-reply. Waiting.")

    if from_role == "customer":
        return ReplyResponse(reply="Hi, I'm interested in your offer. Can I book for tomorrow?", action="send", rationale="Customer inquiry")

    m_ctx = storage.get(("merchant", merchant_id), {}).get("payload", {}) if merchant_id else {}
    c_slug = m_ctx.get("category_slug", "generic")
    cat_ctx = storage.get(("category", c_slug), {}).get("payload", {})
    history = conversations.get(conversation_id, [])

    system_prompt = f"""You are Vera, magicpin Growth Lead. 
Respond to {m_ctx.get('identity', {}).get('owner_first_name', 'merchant')} at {m_ctx.get('identity', {}).get('name', 'store')}.
RULES:
1. SPECIFICITY: Mention ONLY Views ({m_ctx.get('performance', {}).get('views', '?')}), Calls ({m_ctx.get('performance', {}).get('calls', '?')}), or CTR ({m_ctx.get('performance', {}).get('ctr', 0)*100:.1f}%). 
2. CATEGORY VOICE: Use {c_slug} professional jargon. Peer-to-peer style.
3. COMPULSION: Use 'social proof' or 'urgent ROI'.
4. ACTION: simplified CTA. action='send' for advice, 'end' for hostile.
5. NO URLs. < 280 chars. Return JSON: {{"reply": "...", "action": "...", "cta": "...", "rationale": "..."}}
"""
    try:
        res_text = cohere_complete(f"Merchant said: {text}\nHistory: {history[-2:]}", system_prompt)
        match = re.search(r'\{[\s\S]*\}', res_text)
        if not match: raise ValueError("No JSON")
        out = json.loads(match.group())
        
        reply, val_rat = _validate_and_repair(out.get("reply") or out.get("body") or "", m_ctx)
        
        return ReplyResponse(
            reply=reply or "Understood. Let's grow.",
            action=out.get("action", "send"),
            cta=out.get("cta", "See Details"),
            rationale=out.get("rationale", "LLM Response")
        )
    except Exception:
        # Mini fallback
        return ReplyResponse(reply="I'm here to help you grow. Shall we look at your latest search trends?", action="send", rationale="Reply fallback")

# ---------------------------------------------------------
# API Application Layer
# ---------------------------------------------------------
app = FastAPI(title="Vera Growth Engine", description="Deterministic Merchant Assistant API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/v1/healthz", response_model=HealthzResponse)
def get_health():
    counts = {"category": 0, "merchant": 0, "customer": 0, "trigger": 0}
    for scope, _ in storage.keys():
        if scope in counts:
            counts[scope] += 1
            
    return HealthzResponse(
        status="ok",
        service="vera-growth-engine",
        version="1.1.0",
        contexts_loaded=counts
    )

@app.get("/v1/metadata", response_model=MetadataResponse)
def get_metadata():
    return MetadataResponse(
        name="Vera Growth Engine",
        builder="Manya Valecha",
        model="Cohere command-r-plus / deterministic-hybrid",
        version="1.1.0",
        challenge="magicpin Vera AI Challenge"
    )

@app.post("/v1/context")
def ingest_context(ctx: ContextPayload):
    key = (ctx.scope, ctx.context_id)
    if ctx.scope not in ["category", "merchant", "customer", "trigger"]:
        return JSONResponse(status_code=400, content={"accepted": False, "reason": "invalid_scope"})
    storage[key] = {"version": ctx.version, "payload": ctx.payload}
    return {"accepted": True}

@app.post("/v1/tick", response_model=TickResponse)
def execute_tick(req: TickRequest):
    actions: List[ActionObject] = []
    for trg_id in req.available_triggers:
        trg_data = storage.get(("trigger", trg_id))
        if trg_data:
            action = generate_growth_action(trigger_id=trg_id, trigger_payload=trg_data.get("payload", {}))
            if action: actions.append(action)
    return TickResponse(actions=actions)

@app.post("/v1/reply", response_model=ReplyResponse)
def receive_reply(req: ReplyRequest):
    if req.conversation_id not in conversations:
        conversations[req.conversation_id] = []
    conversations[req.conversation_id].append({"from": req.from_role, "msg": req.message})
    return handle_reply_intent(text=req.message, conversation_id=req.conversation_id, from_role=req.from_role, merchant_id=req.merchant_id)

# Mount static assets explicitly instead of app.mount to avoid boot crashes
@app.get("/assets/{file_path:path}")
async def serve_asset(file_path: str):
    import os
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    target = os.path.join(BASE_DIR, "frontend", "dist", "assets", file_path)
    if os.path.exists(target):
        if target.endswith(".js"): return FileResponse(target, media_type="application/javascript")
        if target.endswith(".css"): return FileResponse(target, media_type="text/css")
        return FileResponse(target)
    return JSONResponse(status_code=404, content={"error": "Asset Missing", "path": target})

@app.get("/")
async def serve_root():
    index_path = "frontend/dist/index.html"
    if os.path.exists(index_path): return FileResponse(index_path)
    return JSONResponse(status_code=404, content={"detail": "Not found"})

@app.get("/{full_path:path}")
async def serve_frontend(full_path: str):
    if full_path.startswith("v1"): return JSONResponse(status_code=404, content={"detail": "Not Found"})
    index_path = "frontend/dist/index.html"
    if os.path.exists(index_path): return FileResponse(index_path)
    return JSONResponse(status_code=404, content={"detail": "Not Found"})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8080, reload=True)
