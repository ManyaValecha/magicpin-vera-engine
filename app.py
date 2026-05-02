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
    service: str
    version: str

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
                    f"Quick question {display_name} — many {locality} users delay their yearly checkup. Want me to promote a ₹299 dental screening to re-engage them?",
                    "Promote ₹299 Screening",
                    "Curiosity trigger + dentist category + low-friction entry-price CTA."
                )
            elif any(k in slug for k in ["salon", "beauty", "spa"]):
                return (
                    f"Quick question {display_name} — weekend self-care searches in {locality} are rising. Want me to boost a 'Hair Spa' offer to fill your slots?",
                    "Boost Hair Spa",
                    "Curiosity trigger + salon category + weekend yield optimization."
                )
            elif any(k in slug for k in ["restaurant", "food", "cafe"]):
                return (
                    f"Quick question {display_name} — dinner traffic near {locality} is active tonight. Want me to push your combo meal to capture walk-ins?",
                    "Push Combo Tonight",
                    "Curiosity trigger + restaurant category + peak-hour traffic play."
                )
            elif any(k in slug for k in ["gym", "fitness", "yoga"]):
                return (
                    f"Quick question {display_name} — many {locality} users start fitness plans before summer. Want me to create a 7-day trial offer for your gym?",
                    "Create Trial Offer",
                    "Curiosity trigger + gym category + seasonal fitness acquisition."
                )
            elif any(k in slug for k in ["pharmac", "medic", "chemist"]):
                return (
                    f"Quick question {display_name} — refill demand in {locality} is rising. Want me to send monthly medicine reminder offers to your regulars?",
                    "Send Refill Reminders",
                    "Curiosity trigger + pharmacy category + retention automation."
                )

        # ---- recall_due: Re-engage lapsed customers ----
        elif "recall" in kind:
            if is_dentist:
                return (
                    f"{display_name}, some patients haven't visited in 6+ months. Should we send a gentle recall with a ₹199 cleaning offer?",
                    "Send Recall",
                    "Recall trigger: reactivating lapsed patients with entry-level offer."
                )
            elif any(k in slug for k in ["salon", "beauty", "spa"]):
                return (
                    f"Hi {display_name}, several clients haven't booked in 60+ days. Want me to send a '20% off your next visit' recall message?",
                    "Send Recall Offer",
                    "Recall trigger: lapsed salon clients with win-back discount."
                )
            elif any(k in slug for k in ["restaurant", "food", "cafe"]):
                return (
                    f"Hi {display_name}, some regulars haven't ordered in 30 days. Should we push a 'We miss you' combo deal to bring them back?",
                    "Send Win-Back Offer",
                    "Recall trigger: re-engaging lapsed diners with a win-back deal."
                )
            elif any(k in slug for k in ["gym", "fitness", "yoga"]):
                return (
                    f"Hi {display_name}, several members have stopped checking in. Want to send a 'Come back' free session pass to reactivate them?",
                    "Send Re-Activation",
                    "Recall trigger: reactivating churned gym members."
                )
            elif any(k in slug for k in ["pharmac", "medic", "chemist"]):
                return (
                    f"Hi {display_name}, 45 refill customers are overdue this week. Should we send them a reminder to avoid running out?",
                    "Send Refill Alert",
                    "Recall trigger: automating pharmaceutical refill reminders."
                )

        # ---- traffic_spike: Capitalize on real-time demand surge ----
        elif "traffic" in kind or "spike" in kind or "demand" in kind:
            if is_dentist:
                return (
                    f"{display_name}, searches for dental care in {locality} are up 40% today. Let's push your teeth-whitening offer to capture this intent now.",
                    "Boost Whitening Offer",
                    "Traffic spike: capturing high-intent dental searches in locality."
                )
            elif any(k in slug for k in ["salon", "beauty", "spa"]):
                return (
                    f"Hi {display_name}, {locality} is buzzing! Beauty searches spiked — want to push a 'Walk-in Welcome' deal to capture the demand?",
                    "Push Walk-in Deal",
                    "Traffic spike: converting intent into same-day salon bookings."
                )
            elif any(k in slug for k in ["restaurant", "food", "cafe"]):
                return (
                    f"Hi {display_name}, food delivery searches near {locality} just spiked! Ready to go live with a '15% off first order' flash deal?",
                    "Go Live Now",
                    "Traffic spike: converting surge in food intent into immediate orders."
                )
            elif any(k in slug for k in ["gym", "fitness", "yoga"]):
                return (
                    f"Hi {display_name}, fitness searches in {locality} spiked this morning. Should we run a 'Flash Membership' offer for the next 3 hours?",
                    "Launch Flash Offer",
                    "Traffic spike: converting high-intent gym searches to memberships."
                )
            elif any(k in slug for k in ["pharmac", "medic", "chemist"]):
                return (
                    f"Hi {display_name}, health product searches in {locality} are trending. Want to boost visibility for your top-selling OTC medicines?",
                    "Boost Visibility",
                    "Traffic spike: capturing pharmacy intent during health search surge."
                )

        # ---- flash_sale / dip: Counter slow periods ----
        elif any(k in kind for k in ["flash", "dip", "slow"]):
            if is_dentist:
                return (
                    f"{display_name}, appointments are a bit slow today. Want to run a 2-hour window for ₹499 checkup & cleaning to fill slots?",
                    "Fill Today's Slots",
                    "Dip trigger: yield management via discounted cleaning to fill idle time."
                )
            elif any(k in slug for k in ["salon", "beauty", "spa"]):
                return (
                    f"Hi {display_name}, bookings look light this afternoon. Launch a '2-hour flash: 30% off any service' to attract walk-ins in {locality}?",
                    "Launch Flash Deal",
                    "Dip trigger: converting idle capacity into same-day bookings."
                )
            elif any(k in slug for k in ["restaurant", "food", "cafe"]):
                return (
                    f"Hi {display_name}, lunch rush is slower than usual. A quick '₹99 lunch combo' flash deal could pull in nearby magicpin users. Go?",
                    "Start Lunch Flash",
                    "Dip trigger: driving incremental covers during low-traffic lunch."
                )
            elif any(k in slug for k in ["gym", "fitness", "yoga"]):
                return (
                    f"Hi {display_name}, the gym is quieter than usual today. Want to offer a '₹99 Day Pass' flash to attract walk-ins from {locality}?",
                    "Sell Day Pass",
                    "Dip trigger: filling empty gym floor time with day-pass flash."
                )
            elif any(k in slug for k in ["pharmac", "medic", "chemist"]):
                return (
                    f"Hi {display_name}, walk-in traffic is low. Want to push a '10% off vitamins today only' deal to drive impulse wellness purchases?",
                    "Run Vitamin Flash",
                    "Dip trigger: driving impulse purchases during low foot-traffic."
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


def _deterministic_reply_intent(text: str, conversation_id: str) -> ReplyResponse:
    text_clean = text.lower().strip()
    words = text_clean.split()
    
    # Identify context
    merchant_id = conversation_id.replace("conv_", "")
    m_ctx = storage.get(("merchant", merchant_id), {}).get("payload", {})
    name = m_ctx.get("identity", {}).get("name", "merchant")
    locality = m_ctx.get("identity", {}).get("locality", "Lajpat Nagar")
    category = str(m_ctx.get("category_slug", "business")).lower()
    
    # Category Normalization
    cat_kind = "generic"
    if any(k in category for k in ["dentist", "dental"]): cat_kind = "dentist"
    elif any(k in category for k in ["gym", "fitness", "yoga"]): cat_kind = "gym"
    elif any(k in category for k in ["salon", "beaut", "spa"]): cat_kind = "salon"
    elif any(k in category for k in ["restaur", "food", "cafe", "bakery", "dining"]): cat_kind = "food"
    elif any(k in category for k in ["pharmac", "medic", "chemist"]): cat_kind = "pharmacy"

    # Name Handling
    name_clean = name.strip()
    if cat_kind == "dentist" and not name_clean.lower().startswith("dr"):
        display_name = f"Dr. {name_clean}"
    else:
        display_name = name_clean

    # Intent Templates Matrix
    templates = {
        "hi": {
            "dentist": f"Hi {display_name}. Health searches are active in {locality} today. Want to run ₹299 checkup promo now?",
            "gym": f"Hi {display_name}. Fitness searches are peaking in {locality}! Ready to launch a 7-day trial offer?",
            "salon": f"Hi {display_name}. Self-care interest is high in {locality} today. Should we push a 'New Look' makeover deal?",
            "food": f"Hi {display_name}. Dining demand near {locality} is rising. Ready to boost your 'Quick Lunch' combo?",
            "pharmacy": f"Hi {display_name}. Health utility searches are active in {locality}. Want to run a delivery-first promo?",
            "generic": f"Hi {display_name}. Nearby demand in {locality} is active today. Want to launch a high-conversion offer now?"
        },
        "sales": {
            "dentist": f"Views are strong but conversions can improve. Launch ₹299 checkup offer today to capture nearby {locality} demand. Activate?",
            "gym": f"Traffic is active but sign-ups are soft. A ₹499 membership trial could boost {locality} conversions. Launch?",
            "salon": f"Views are high! A 'Bridal Preview' or 'Glow Deal' would convert this {locality} traffic into bookings. Activate?",
            "food": f"Footfall is strong but orders are soft. A 'Flash Combo' for the next 2 hours can capture {locality} demand. Start?",
            "pharmacy": f"Search volume is good. Let's push an 'Essentials Bundle' to boost your {locality} sales today. Activate?",
            "generic": f"Views are strong but conversions can improve. I recommend a localized flash offer for {locality} users. Launch?"
        },
        "calls": {
            "dentist": f"Calls are soft this week. Add a call-first CTA with ₹299 dental checkup to increase bookings in {locality}. Should I start it?",
            "gym": f"Call volume is low. Let's add a 'Book a Free Trial' CTA to your {locality} ads to drive appointments. Start?",
            "salon": f"Inquiries are light. A call-based 'Style Consultation' offer can fill your slots in {locality}. Initiate?",
            "food": f"Booking calls are soft. Let's add a 'Call for Table Reservation' CTA to capture more {locality} diners. Go?",
            "pharmacy": f"Prescription inquiries are down. A direct call-for-delivery CTA could increase your {locality} orders. Start?",
            "generic": f"Call volume is lower this week. Let's add a direct-call CTA to your {locality} campaign for better reach. Go?"
        },
        "recommend": {
            "dentist": f"Best option today: ₹299 Dental Checkup campaign targeting nearby {locality} search traffic. Launch now?",
            "gym": f"Top recommendation: '7-Day Summer Restart' membership drive for {locality} fitness seekers. Launch?",
            "salon": f"Best for you today: 'Weekend Glow' bridal/makeup special. Captures current {locality} booking spikes. Run?",
            "food": f"Recommended play: 'Evening Family Combo' targeting {locality} dinner traffic. Launch and capture orders?",
            "pharmacy": f"Strategic choice: 'Health Essentials Refill' push for your top 50 {locality} customers. Send now?",
            "generic": f"Best option today: A targeted 'Trending Offer' for {locality} search traffic. Launch now?"
        },
        "ipl": {
            "food": f"Hi {display_name}, IPL match tonight brings heavy footfall! A 'Match Day Combo' will drive massive orders. Ready?",
            "dentist": f"Hi {display_name}, IPL excitement is high in {locality}! A 'Match Day Shine' special checkup till 9 PM could capture footfall. Ready?",
            "salon": f"Hi {display_name}, IPL traffic is heavy tonight. A 'Quick Match-Day Grooming' special can attract walk-ins. Ready?",
            "gym": f"Hi {display_name}, IPL buzz is peak! A 'Match Day Fitness Trial' could drive high footfall tonight. Activate?",
            "pharmacy": f"Hi {display_name}, IPL traffic is rising. Boost your 'Match-Day Essentials' visibility to capture demand. Go?",
            "generic": f"IPL excitement is peak in {locality}! A 'Match Day Special' can drive heavy footfall tonight. Activate?"
        }
    }

    # Intent selection
    intent = None
    if any(h in words for h in ["hi", "hello", "hey"]): intent = "hi"
    elif "sales" in text_clean: intent = "sales"
    elif "calls" in text_clean: intent = "calls"
    elif any(k in text_clean for k in ["what should i run", "offer", "recommend", "how to"]): intent = "recommend"
    elif "boost" in text_clean and intent is None: intent = "recommend"
    elif "ipl" in text_clean: intent = "ipl"

    if intent:
        reply = templates[intent].get(cat_kind, templates[intent]["generic"])
        ctas = {"hi": "Launch Promo", "sales": "Activate Now", "calls": "Start Ads", "recommend": "Launch Campaign", "ipl": "Activate"}
        return ReplyResponse(reply=reply, action="send", cta=ctas.get(intent, "Get Started"), rationale=f"Intent: {intent}, Category: {cat_kind}")

    # Standard fallbacks
    if any(i in text_clean for i in ["stop", "no", "not interested", "automated assistant"]):
        return ReplyResponse(reply="Understood. I will pause suggestions for now.", action="end", rationale="Merchant requested stop.")
    
    elif any(i in text_clean for i in ["expensive", "cost", "price", "too much"]):
        return ReplyResponse(
            reply="I can suggest a lower entry offer (e.g. ₹199) to increase conversions without hurting your margins. Want me to create one?",
            action="send",
            cta="See Lower Offer",
            rationale="Addressing price objection with strategic entry-level alternative."
        )

    return ReplyResponse(
        reply="Nearby demand looks active today. Want me to recommend the best campaign for your business now?",
        action="send",
        cta="Recommend",
        rationale="Strong default fallback with proactive CTA."
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
        body, val_rationale = _validate_and_repair(out.get("body") or out.get("message") or "", m_ctx)
        
        return ActionObject(
            conversation_id=f"conv_{merchant_id}_{trigger_id}",
            merchant_id=merchant_id,
            customer_id=cust_id,
            send_as="Vera",
            trigger_id=trigger_id,
            message=body,
            cta=out.get("cta", "open_ended"),
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
        reply = out.get("reply") or out.get("body") or ""
        if reply:
            reply, val_rat = _validate_and_repair(reply, m_ctx)
        
        return ReplyResponse(
            reply=reply,
            action=out.get("action", "send"),
            cta=out.get("cta"),
            rationale=f"{out.get('rationale')} | {val_rat}" if reply else out.get("rationale", "LLM Intent")
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
    return HealthzResponse(
        status="ok",
        service="vera-growth-engine",
        version="1.0.0"
    )

@app.get("/v1/metadata", response_model=MetadataResponse)
def get_metadata():
    return MetadataResponse(
        name="Vera Growth Engine",
        builder="Manya Valecha",
        model="gpt-4o-mini / deterministic-hybrid",
        version="1.0.0",
        challenge="magicpin Vera AI Challenge"
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

    storage[key] = {
        "version": ctx.version,
        "payload": ctx.payload
    }

    return {"accepted": True}

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
async def serve_root():
    index_path = "frontend/dist/index.html"
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return JSONResponse(status_code=404, content={"detail": "Frontend build not found."})

@app.get("/{full_path:path}")
async def serve_frontend(full_path: str):
    # If the path starts with v1, let the API handlers take it
    if full_path.startswith("v1"):
        return JSONResponse(status_code=404, content={"detail": "Not Found"})
    
    index_path = "frontend/dist/index.html"
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return JSONResponse(status_code=404, content={"detail": "Not Found"})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8080, reload=True)
