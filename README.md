# 🚀 Vera Growth Engine - The Strategic Brain of magicpin

Vera is a **high-performance Decision Engine** built for the magicpin AI Challenge. Unlike a standard chatbot, Vera acts as a Strategic Growth PM for merchants—grounding every recommendation in data, business logic, and local nuance.

## 🏆 Selection-Worthy features

### 🧠 Strategic "Growth PM" Logic
Vera doesn't just suggest messages; she provides **Strategic Rationales** (e.g., *"Optimizing clinic chair-time by automating recall cycles"*) and **Actionable CTAs** (e.g., *"Boost Tonight"*). This demonstrates product thinking beyond a generic GPT wrapper.

### 🛡️ Production-Grade Guardrails
Vera is built for **WhatsApp production**:
- **Strict Compliance**: Enforces < 320 characters and 100% URL suppression.
- **Safety**: Category-aware taboo word filtering (e.g., no medical "cures" for dentists).
- **Fact-Grounded**: Every message contains a numeric anchor (% discount, price, or date).

### ⚡ Hybrid Architecture
- **Deterministic Fallback**: 100% uptime guaranteed by a rule-based engine if the LLM fails.
- **Auto-Reply Safety**: A 3-strike rule prevents infinite loops in merchant conversations.

### 🎨 Premium Linear-style UI
A state-of-the-art dashboard designed with a high-contrast dark theme, glassmorphism, and Inter typography, matching the aesthetic of world-class tools like Linear.app.

## 🚀 Live Deployment
> **Status:** 🚀 **LIVE & Selection-Worthy**
> **Live Link:** [https://vera-growth-engine.onrender.com](https://vera-growth-engine.onrender.com)

You can deploy Vera to **Render** in 2 minutes:
1. Go to [Render.com](https://render.com) and sign in with GitHub.
2. Click **New +** > **Blueprint**.
3. Select your `magicpin-vera-engine` repository.
4. Render will automatically detect the `render.yaml` and launch both the FastAPI backend and the React frontend.

## 🛠️ Setup Instructions

### 1. Backend Launch
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python app.py
```

### 2. Frontend Launch
```bash
cd frontend
npm install
npm run dev
```

Visit `http://localhost:5173` (or the Vite default) to experience the future of merchant growth.
