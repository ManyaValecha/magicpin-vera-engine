import time
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional
import os
import json
from uuid import uuid4
try:
    from openai import OpenAI
    has_openai = True
    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", "dummy"))
except ImportError:
    has_openai = False

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
    uptime_seconds: int
    contexts_loaded: Dict[str, int]

class MetadataResponse(BaseModel):
    team_name: str
    team_members: List[str]
    model: str
    approach: str
    contact_email: str
    version: str
    submitted_at: str

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
    template_name: str
    template_params: List[str]
    body: str
    cta: str
    suppression_key: str
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
    action: str  # "send", "wait", "end"
    body: Optional[str] = None
    wait_seconds: Optional[int] = None
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
            body += f" Check out {offers[0].get('title')}."
        else:
            body += " Inquire for latest rates."

    # 4. Final Length check
    if len(body) > 320:
        body = body[:317] + "..."
            
    return body, "Validated & Repaired"

# ---------------------------------------------------------
# State Management
# ---------------------------------------------------------
# Key: (scope, context_id) -> {"version": int, "payload": dict}
storage: Dict[tuple[str, str], Dict[str, Any]] = {}
conversations: Dict[str, List[Dict[str, str]]] = {} 
auto_reply_tracker: Dict[str, int] = {} # conversation_id -> count
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
    
    # Select Strategic CTA based on trigger category
    if "dentist" in category_slug:
        if trigger_kind == "research_digest":
            body = f"Dr. {name}, new JIDA research on fluoride just dropped. Great to share with patients."
            cta = "Review Digest"
            strat_rationale = f"Educating patients on {locality} health trends to increase clinic authority."
        elif trigger_kind == "recall_due":
            body = f"Dr. {name}, some patients are due for their 6-month cleaning. Should we send a reminder?"
            cta = "Send Reminders"
            strat_rationale = "Optimizing clinic chair-time by automating standard 6-month recall cycles."
        else:
            body = f"Dr. {name}, searches for dental care in {locality} are up. Let's push {offer_title}."
            cta = "Boost Now"
            strat_rationale = f"Capitalizing on local search volume spike to drive conversion for '{offer_title}'."
    elif "salon" in category_slug:
        body = f"Hi {name}, {locality} is buzzing with festive searches! Want to promote {offer_title}?"
        cta = "Promote Offer"
        strat_rationale = "Leveraging seasonal high-intent windows to maximize booking density."
    else:
        body = f"Hi {name}, noticing some growth trends in {locality}. Ready to take a step with {offer_title}?"
        cta = "Check Trends"
        strat_rationale = "Proactive merchant engagement based on locality performance benchmarks."

    body, val_rationale = _validate_and_repair(body, merchant_ctx)
    conv_id = f"conv_{merchant_id}_{trigger_id}"
    customer_id = trigger_payload.get("customer_id")
    
    return ActionObject(
        conversation_id=conv_id,
        merchant_id=merchant_id,
        customer_id=customer_id,
        send_as="vera" if not customer_id else "merchant_on_behalf",
        trigger_id=trigger_id,
        template_name="strat_v3",
        template_params=[],
        body=body,
        cta=cta,
        suppression_key=trigger_payload.get("suppression_key") or f"act_{uuid4().hex[:8]}",
        rationale=f"{strat_rationale} | {val_rationale}"
    )

def _deterministic_reply_intent(text: str, conversation_id: str) -> ReplyResponse:
    text_clean = text.lower().strip()
    
    # Identify context from conversation_id pattern
    merchant_id = conversation_id.replace("conv_", "")
    m_ctx = storage.get(("merchant", merchant_id), {}).get("payload", {})
    name = m_ctx.get("identity", {}).get("name", "merchant")
    locality = m_ctx.get("identity", {}).get("locality", "the area")
    category = str(m_ctx.get("category_slug", "business")).lower()

    if any(i in text_clean for i in ["stop", "no", "not interested", "automated assistant"]):
        return ReplyResponse(action="end", rationale="Merchant requested stop.")
    
    elif any(i in text_clean for i in ["later", "snooze", "wait"]):
        return ReplyResponse(action="wait", wait_seconds=1800, rationale="Merchant requested delay.")
    
    # Specific growth advice for "tell me", "what should i do", "help"
    elif any(i in text_clean for i in ["tell me", "what should i do", "how", "help", "ideas", "grow", "ipl", "festival", "dip"]):
        # IPL Trigger
        if any(i in text_clean for i in ["ipl", "match", "cricket"]):
            body = f"Hi {name}, IPL match tonight brings heavy footfall to {locality}! We should run a 'Match Day' combo to keep them coming. Ready to boost?"
            cta = "Activate Match Combo"
        # Sales Dip Trigger
        elif any(i in text_clean for i in ["dip", "low", "slow", "down"]):
            body = f"Hi {name}, noticing a slight dip in {locality} traffic today. I recommend a 2-hour 'Flash Sale' to pull in nearby magicpin users. Should we launch?"
            cta = "Launch Flash Sale"
        # Festival Trigger
        elif any(i in text_clean for i in ["festival", "diwali", "holi", "celebration"]):
            body = f"Hi {name}, festive season is starting in {locality}! People are looking for gifting and treats. Let's push your best-seller as a Festive Special. Go ahead?"
            cta = "Push Festive Special"
        # Generic Growth
        else:
            if "dentist" in category:
                body = f"Dr. {name}, noticing health searches peaking in {locality}. I recommend boosting your dental cleaning offer to capture this intent. Should I activate it?"
                cta = "Boost Tonight"
            elif "salon" in category:
                body = f"Hi {name}, {locality} is buzzing! Let's push a BOGO offer for facial treatments to fill up your slots for tomorrow. Want to proceed?"
                cta = "Push Offer"
            else:
                body = f"Hi {name}, performance in {locality} is looking good. We can scale your current ads to reach 20% more locals. Ready?"
                cta = "Scale Now"
        return ReplyResponse(action="send", body=body, cta=cta, rationale=f"Strategic growth advice for {category} context.")

    return ReplyResponse(
        action="send",
        body="Got it. I'll analyze your latest performance data and prepare the next step. Updates will reflect shortly.",
        cta="Check Insights",
        rationale="Generic acknowledgement with intentional CTA."
    )

def generate_growth_action(trigger_id: str, trigger_payload: Dict[str, Any]) -> Optional[ActionObject]:
    if not has_openai or not os.environ.get("OPENAI_API_KEY"):
        return _deterministic_growth_action(trigger_id, trigger_payload)

    merchant_id = trigger_payload.get("merchant_id")
    if not merchant_id: return None

    m_ctx = storage.get(("merchant", merchant_id), {}).get("payload", {})
    c_slug = m_ctx.get("category_slug", "generic")
    cat_ctx = storage.get(("category", c_slug), {}).get("payload", {})
    cust_id = trigger_payload.get("customer_id")
    cust_ctx = storage.get(("customer", cust_id), {}).get("payload", {}) if cust_id else {}

    system_prompt = f"""You are Vera, the Strategy Brain for magicpin merchants.
Your goal: Decide the best next growth action for the merchant.
Rules:
1. USE Hinglish (natural mix of Hindi + English) for a warm, peer-to-peer feel.
2. USE numeric anchors (prices, %, dates, counts). NO fake stats.
3. ABSOLUTELY NO URLs.
4. Keep body < 300 characters.
5. Tone: {cat_ctx.get('voice', 'Professional and helpful')}.
6. CTA: Select an actionable growth-oriented CTA (e.g., 'Boost Tonight', 'Send Recall', 'Activate Now').
7. Rationale: Explain the STRATEGIC reasoning (e.g., 'Recovering lost revenue from lapsed customers' or 'Capturing seasonal traffic spike').
"""
    user_context = {
        "trigger": trigger_payload,
        "merchant": m_ctx,
        "category": cat_ctx,
        "customer": cust_ctx
    }

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_context)}
            ],
            response_format={"type": "json_object"},
            temperature=0.3
        )
        out = json.loads(response.choices[0].message.content)
        body, val_rationale = _validate_and_repair(out.get("body", ""), m_ctx)
        
        return ActionObject(
            conversation_id=f"conv_{merchant_id}_{trigger_id}",
            merchant_id=merchant_id,
            customer_id=cust_id,
            send_as="vera" if not cust_id else "merchant_on_behalf",
            trigger_id=trigger_id,
            template_name="vera_v3_llm",
            template_params=[],
            body=body,
            cta=out.get("cta", "open_ended"),
            suppression_key=trigger_payload.get("suppression_key") or f"act_{uuid4().hex[:8]}",
            rationale=f"{out.get('rationale')} | {val_rationale}"
        )
    except Exception:
        return _deterministic_growth_action(trigger_id, trigger_payload)

def handle_reply_intent(text: str, conversation_id: str, merchant_id: str = None) -> ReplyResponse:
    # 1. Clean and track auto-replies (3-strike rule)
    text_clean = text.lower().strip()
    is_auto = any(i in text_clean for i in ["automated response", "busy right now", "standard reply", "thank you for contacting"])
    
    if is_auto:
        count = auto_reply_tracker.get(conversation_id, 0) + 1
        auto_reply_tracker[conversation_id] = count
        if count >= 3:
            return ReplyResponse(action="end", rationale="3-strike auto-reply rule reached. Exiting.")
        return ReplyResponse(action="wait", wait_seconds=3600 * count, rationale=f"Auto-reply detected (Strike {count}). Waiting.")

    # 2. Fetch Context for LLM
    m_ctx = {}
    cat_ctx = {}
    if merchant_id:
        m_ctx = storage.get(("merchant", merchant_id), {}).get("payload", {})
        c_slug = m_ctx.get("category_slug", "generic")
        cat_ctx = storage.get(("category", c_slug), {}).get("payload", {})

    if not has_openai or not os.environ.get("OPENAI_API_KEY"):
        return _deterministic_reply_intent(text, conversation_id)

    # 3. LLM Strategy Brain
    history = conversations.get(conversation_id, [])
    system_prompt = f"""You are Vera, the Strategy Brain for magicpin merchants.
Your goal: Respond to the merchant as a Strategic Growth PM.
Persona:
- Expert, proactive, data-driven.
- Uses Hinglish (natural Hindi/English mix).
- Tone: {cat_ctx.get('voice', 'Professional and helpful')}.

Rules:
1. If the merchant asks for growth advice (e.g., 'tell me', 'help', 'ideas'), provide a DATA-DRIVEN recommendation based on their context.
2. If they are negative or want to stop, set action to 'end'.
3. If they are busy, set action to 'wait'.
4. OTHERWISE, set action to 'send' and provide a strategic body + CTA.

Output JSON: 
{{
  "action": "send"|"wait"|"end", 
  "body": "Your Hinglish growth advice here", 
  "cta": "Actionable CTA e.g. Boost Tonight", 
  "rationale": "Why you chose this advice"
}}
"""
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Merchant Context: {m_ctx}\nHistory: {history}\nMerchant Reply: {text}"}
            ],
            response_format={"type": "json_object"}
        )
        out = json.loads(response.choices[0].message.content)
        body = out.get("body", "")
        if body:
            body, val_rat = _validate_and_repair(body, m_ctx)
        
        return ReplyResponse(
            action=out.get("action", "send"),
            body=body,
            cta=out.get("cta"),
            wait_seconds=out.get("wait_seconds", 3600) if out.get("action") == "wait" else None,
            rationale=f"{out.get('rationale')} | {val_rat}" if body else out.get("rationale", "LLM Intent")
        )
    except Exception as e:
        return _deterministic_reply_intent(text, conversation_id)

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
    for (scope, _), _ in storage.items():
        counts[scope] = counts.get(scope, 0) + 1
        
    return HealthzResponse(
        status="ok",
        uptime_seconds=int(time.time() - START_TIME),
        contexts_loaded=counts
    )

@app.get("/v1/metadata", response_model=MetadataResponse)
def get_metadata():
    return MetadataResponse(
        team_name="Deterministic Alphas",
        team_members=["Manya Valecha"],
        model="deterministic-rules",
        approach="In-memory context matcher with conditional template gating",
        contact_email="manya@example.com",
        version="1.0.0",
        submitted_at="2026-04-26T08:00:00Z"
    )

@app.post("/v1/context")
def ingest_context(ctx: ContextPayload):
    key = (ctx.scope, ctx.context_id)
    
    # 400 validation
    if ctx.scope not in ["category", "merchant", "customer", "trigger"]:
        return JSONResponse(status_code=400, content={
            "accepted": False, 
            "reason": "invalid_scope", 
            "details": f"Unknown scope {ctx.scope}"
        })

    cur = storage.get(key)
    
    # Check for stale versions
    if cur and cur.get("version", 0) > ctx.version:
        return JSONResponse(status_code=409, content={
            "accepted": False, 
            "reason": "stale_version", 
            "current_version": cur["version"]
        })
    elif cur and cur.get("version", 0) == ctx.version:
        # Idempotent return
        return {
            "accepted": True,
            "ack_id": f"ack_{ctx.context_id}_v{ctx.version}",
            "stored_at": datetime.now(timezone.utc).isoformat() + "Z"
        }

    storage[key] = {
        "version": ctx.version,
        "payload": ctx.payload
    }

    return {
        "accepted": True,
        "ack_id": f"ack_{ctx.context_id}_v{ctx.version}",
        "stored_at": datetime.now(timezone.utc).isoformat() + "Z"
    }

@app.post("/v1/tick", response_model=TickResponse)
def execute_tick(req: TickRequest):
    actions: List[ActionObject] = []
    
    for trg_id in req.available_triggers:
        trg_data = storage.get(("trigger", trg_id))
        if not trg_data:
            continue
            
        payload = trg_data.get("payload", {})
        action = generate_growth_action(trigger_id=trg_id, trigger_payload=payload)
        if action:
            actions.append(action)
            
    return TickResponse(actions=actions)

@app.post("/v1/reply", response_model=ReplyResponse)
def receive_reply(req: ReplyRequest):
    if req.conversation_id not in conversations:
        conversations[req.conversation_id] = []
    conversations[req.conversation_id].append({
        "from": req.from_role,
        "msg": req.message
    })
    
    return handle_reply_intent(text=req.message, conversation_id=req.conversation_id, merchant_id=req.merchant_id)

# ---------------------------------------------------------
# Static File Serving (Production)
# ---------------------------------------------------------
# Mount the dist folder for assets
if os.path.exists("frontend/dist"):
    app.mount("/assets", StaticFiles(directory="frontend/dist/assets"), name="assets")

@app.get("/")
@app.get("/{full_path:path}")
async def serve_frontend(full_path: str = None):
    # If the path starts with v1, let the API handlers take it
    if full_path and full_path.startswith("v1"):
        return JSONResponse(status_code=404, content={"detail": "Not Found"})
    
    index_path = "frontend/dist/index.html"
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return JSONResponse(status_code=404, content={"detail": "Frontend build not found. Run 'npm run build' in frontend directory."})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8080, reload=True)
