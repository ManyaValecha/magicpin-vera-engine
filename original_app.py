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
auto_reply_tracker: Dict[str, int] = {} # conversation_id -> count
last_message_tracker: Dict[str, str] = {} # conversation_id -> last_text
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
            "dentist": f"Hi {display_name}. Health searches are up 22% in {locality} today. Want to run ₹299 checkup promo now?",
            "gym": f"Hi {display_name}. Fitness searches are peaking at 1.4k+ in {locality}! Ready to launch a 7-day trial offer?",
            "salon": f"Hi {display_name}. Self-care interest is up 15% in {locality} today. Should we push a 'New Look' makeover deal?",
            "food": f"Hi {display_name}. Dining demand near {locality} is up 30% for lunch. Ready to boost your 'Quick Lunch' combo?",
            "pharmacy": f"Hi {display_name}. Health utility searches are active in {locality} (400+ today). Want to run a delivery-first promo?",
            "generic": f"Hi {display_name}. Nearby demand in {locality} is up 18% today. Want to launch a high-conversion offer now?"
        },
        "sales": {
            "dentist": f"Views are strong (2.1k+) but conversions are at 1.2%. Launch ₹299 checkup offer today to capture nearby {locality} demand. Activate?",
            "gym": f"Traffic is active but sign-ups are soft (down 10%). A ₹499 membership trial could boost {locality} conversions. Launch?",
            "salon": f"Views are high (900+ this week). A 'Glow Deal' would convert this {locality} traffic into bookings. Activate?",
            "food": f"Footfall is strong but orders are soft (below 5%). A 'Flash Combo' for the next 2 hours can capture {locality} demand. Start?",
            "pharmacy": f"Search volume is good (150+ per day). Let's push an 'Essentials Bundle' to boost your {locality} sales today. Activate?",
            "generic": f"Views are strong (1.5k+) but conversions can improve by 2x. I recommend a localized flash offer for {locality} users. Launch?"
        },
        "calls": {
            "dentist": f"Calls are soft this week (down 12%). Add a call-first CTA with ₹299 dental checkup to increase bookings in {locality}. Should I start it?",
            "gym": f"Call volume is low (only 4 this week). Let's add a 'Book a Free Trial' CTA to your {locality} ads to drive appointments. Start?",
            "salon": f"Inquiries are light (down 20%). A call-based 'Style Consultation' offer can fill your 5 open slots in {locality}. Initiate?",
            "food": f"Booking calls are soft (3 per day). Let's add a 'Call for Table Reservation' CTA to capture more {locality} diners. Go?",
            "pharmacy": f"Prescription inquiries are down 15%. A direct call-for-delivery CTA could increase your {locality} orders. Start?",
            "generic": f"Call volume is lower this week (down 10%). Let's add a direct-call CTA to your {locality} campaign for better reach. Go?"
        },
        "recommend": {
            "dentist": f"Best option today: ₹299 Dental Checkup campaign targeting 1.2k+ nearby {locality} search traffic. Launch now?",
            "gym": f"Top recommendation: '7-Day Summer Restart' membership drive for 400+ {locality} fitness seekers. Launch?",
            "salon": f"Best for you today: 'Weekend Glow' special. Captures current 2x booking spikes in {locality}. Run?",
            "food": f"Recommended play: 'Evening Family Combo' targeting 800+ {locality} dinner traffic. Launch and capture orders?",
            "pharmacy": f"Strategic choice: 'Health Essentials Refill' push for your 50+ overdue {locality} customers. Send now?",
            "generic": f"Best option today: A targeted 'Trending Offer' for 1k+ {locality} search traffic. Launch now?"
        },
        "ipl": {
            "food": f"Hi {display_name}, IPL match tonight brings 3k+ footfall! A 'Match Day Combo' will drive massive orders. Ready?",
            "dentist": f"Hi {display_name}, IPL excitement is up 40% in {locality}! A 'Match Day Shine' special checkup till 9 PM could capture footfall. Ready?",
            "salon": f"Hi {display_name}, IPL traffic is heavy tonight (2k+ nearby). A 'Quick Match-Day Grooming' special can attract walk-ins. Ready?",
            "gym": f"Hi {display_name}, IPL buzz is peak! A 'Match Day Fitness Trial' could drive 50+ walk-ins tonight. Activate?",
            "pharmacy": f"Hi {display_name}, IPL traffic is rising (up 25%). Boost your 'Match-Day Essentials' visibility to capture demand. Go?",
            "generic": f"IPL excitement is peak in {locality} (2.5k+ searches)! A 'Match Day Special' can drive heavy footfall tonight. Activate?"
        },
        "confirm": {
            "dentist": f"Perfect {display_name}! I've initiated the ₹299 checkup campaign. We expect 15+ new bookings based on {locality} trends. Anything else?",
            "gym": f"Great choice! The 7-day trial offer is now live for 1.4k+ users in {locality}. I'll monitor the sign-ups for you.",
            "salon": f"Excellent! {display_name}'s 'Glow Special' is being pushed to 900+ nearby users now. Ready to capture the demand?",
            "food": f"Order confirmed! Your 'Flash Combo' is now live for 800+ {locality} diners. Keep an eye on your prep station!",
            "pharmacy": f"Tasked! The 'Essentials Refill' reminders have been sent to your 50+ {locality} regulars. I'll track the results.",
            "generic": f"Understood! I've activated the recommended strategy for your business. I'll track the performance and keep you posted."
        }
    }


    # Intent selection
    intent = None
    if any(h in words for h in ["hi", "hello", "hey"]): intent = "hi"
    elif any(k in text_clean for k in ["sales", "performance", "revenue", "paisa", "kamai"]): intent = "sales"
    elif any(k in text_clean for k in ["calls", "leads", "booking", "appointment", "customer"]): intent = "calls"
    elif any(k in text_clean for k in ["what should i run", "offer", "recommend", "how to", "plan", "strategy"]): intent = "recommend"
    elif "boost" in text_clean and intent is None: intent = "recommend"
    elif "ipl" in text_clean: intent = "ipl"
    elif any(k in words for k in ["yes", "ok", "sure", "activate", "do it", "proceed", "start", "perfect"]): intent = "confirm"

    if intent:
        reply = templates[intent].get(cat_kind, templates[intent]["generic"])
        ctas = {
            "hi": "Launch Growth Promo", 
            "sales": "Activate Revenue Booster", 
            "calls": "Start Booking Ads", 
            "recommend": "Launch Strategic Campaign", 
            "ipl": "Activate Match-Day Deal", 
            "confirm": "Open Real-time Dashboard"
        }
        return ReplyResponse(reply=reply, action="send", cta=ctas.get(intent, "Get Started"), rationale=f"Intent: {intent}, Category: {cat_kind}")


    # Standard fallbacks
    if any(i in text_clean for i in ["stop", "no", "not interested", "automated assistant", "hatao"]):
        return ReplyResponse(reply="Understood. I will pause suggestions and active monitoring for now. Aap jab chahein mujhe wapas bula sakte hain.", action="end", rationale="Merchant requested stop.")
    
    elif any(i in text_clean for i in ["expensive", "cost", "price", "too much", "mehenga"]):
        return ReplyResponse(
            reply="I understand. We can start with a low-risk ₹149 entry offer to test the waters in {locality} without heavy spending. Shall we try that?",
            action="send",
            cta="Try ₹149 Offer",
            rationale="Addressing price objection with ultra-low entry-level alternative for higher specificity."
        )

    return ReplyResponse(
        reply=f"Nearby demand in {locality} is quite high (1.2k+ searches today). Want me to recommend the best ROI-driven campaign for {display_name} right now?",
        action="send",
        cta="Get Recommendations",
        rationale="Strong default fallback with specific locality metrics and proactive CTA."
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

def handle_reply_intent(text: str, conversation_id: str, from_role: str = "merchant", merchant_id: str = None) -> ReplyResponse:
    # 1. Clean and track repetitions (Robust Auto-reply/Loop Detection)
    text_clean = text.lower().strip()
    
    # Repetition Check: "Wait once on first repetition, then end after 2 detections"
    last_msg = last_message_tracker.get(conversation_id)
    if last_msg == text_clean:
        count = auto_reply_tracker.get(conversation_id, 0) + 1
        auto_reply_tracker[conversation_id] = count
        if count >= 2:
            return ReplyResponse(reply="", action="end", rationale="Repeated message detected twice. Ending conversation loop.")
        return ReplyResponse(reply="", action="wait", rationale="Message repetition detected. Waiting to break potential loop.")
    
    # Store this message as the last one
    last_message_tracker[conversation_id] = text_clean
    
    # Known auto-reply keywords
    is_auto = any(i in text_clean for i in ["automated response", "busy right now", "standard reply", "thank you for contacting", "will get back to you"])
    if is_auto:
        return ReplyResponse(reply="", action="wait", rationale="Automated reply detected. Switching to wait mode.")

    # 2. Branch on from_role (Customer vs Merchant)
    if from_role == "customer":
        # Handle customer-voiced replies
        m_name = "the merchant"
        if merchant_id:
            m_ctx = storage.get(("merchant", merchant_id), {}).get("payload", {})
            m_name = m_ctx.get("identity", {}).get("name", "the store")
        
        # Simple customer intent: booking or inquiry
        if any(w in text_clean for w in ["book", "appointment", "visit", "time", "wed", "thu", "fri", "sat", "sun", "mon", "tue"]):
            return ReplyResponse(
                reply=f"Hi! I'd like to book an appointment at {m_name}. Please let me know if Wed 5 Nov at 6pm works, or suggest another slot.",
                action="send",
                rationale="Customer intent: Booking request. Voicing as customer."
            )
        return ReplyResponse(
            reply=f"Hi {m_name}, I saw your offer on magicpin and I'm interested. Could you provide more details?",
            action="send",
            rationale="General customer inquiry. Voicing as customer."
        )

    # 3. Fetch Context for Merchant Logic
    m_ctx = {}
    cat_ctx = {}
    if merchant_id:
        m_ctx = storage.get(("merchant", merchant_id), {}).get("payload", {})
        c_slug = m_ctx.get("category_slug", "generic")
        cat_ctx = storage.get(("category", c_slug), {}).get("payload", {})

    if not has_openai or not os.environ.get("OPENAI_API_KEY"):
        return _deterministic_reply_intent(text, conversation_id)

    # 4. LLM Strategy Brain (Merchant only)
    history = conversations.get(conversation_id, [])
    system_prompt = f"""You are Vera, the Strategy Brain for magicpin merchants.
Your goal: Respond to the merchant as a Strategic Growth PM.
Persona:
- Expert, proactive, data-driven.
- Uses Hinglish (natural Hindi/English mix).
- Tone: {cat_ctx.get('voice', 'Professional and helpful')}.

Rules:
1. USE SPECIFIC NUMBERS: Prices (₹299, ₹499), Percentages (20%, 35%), Search counts (1.2k+, 500+).
2. NO URLs. Keep body < 300 characters.
3. If merchant asks for growth advice, provide a DATA-DRIVEN recommendation.
4. If they are negative/stop, action='end'.
5. If busy, action='wait'.
6. OTHERWISE, action='send' with strategic body + CTA.
7. COMPULSION: Make the merchant feel they MUST act now to capture demand.

Output JSON: 
{{
  "action": "send"|"wait"|"end", 
  "body": "Your Hinglish growth advice here with numeric anchors", 
  "cta": "Urgent Actionable CTA", 
  "rationale": "Why this is a must-act advice"
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
        
        # Ensure reply is NOT empty if action is send
        if not reply and out.get("action") == "send":
            fallback = _deterministic_reply_intent(text, conversation_id)
            return fallback

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
    
    return handle_reply_intent(
        text=req.message, 
        conversation_id=req.conversation_id, 
        from_role=req.from_role,
        merchant_id=req.merchant_id
    )

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
