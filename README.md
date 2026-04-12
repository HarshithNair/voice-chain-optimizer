## Problem Statement - 
Warehouse operations rely on unstructured phone calls and manual processes, causing errors, delays, and lack of visibility.

## Solution - 
A voice-native AI system that converts conversations into real-time inventory updates, automation, and smart insights.

# 🚚 Logistey- AI Powered Warehouse Intelligence

> **Aapka Godown, Ab Smart Ho Gaya**  
> An AI voice assistant that manages stock, negotiates prices, places orders, tracks deliveries, and handles supplier reorders — all via phone call, in Hindi, English, or Hinglish.


## 🧠 What is Logistey?

Logistey is a voice first warehouse management system built for Indian agricultural commodity godowns. Truck drivers, buyers, and suppliers can call a single phone number and:

- Check stock availability and prices
- Place orders and get a bill instantly
- Negotiate prices (the AI negotiates back)
- Track delivery status by quoting an order ID
- Get delivery reminders the day before

The system also runs autonomously in the background:
- Auto calls suppliers when stock drops below threshold
- Takes confirmation from suppliers (two way conversation)
- Falls back to the next supplier in priority chain if one declines
- Sends payment reminders to buyers before and after due date

## 🏗️ Architecture
Phone Call (Twilio)
       │
       ▼
Flask Server (app.py)
       │
       ├── Audio → Gemini 2.5 Flash (transcription + intent extraction)
       │
       ├── Intent Router
       │   ├── stock_arrival    → Update inventory
       │   ├── stock_query      → Return stock info
       │   ├── price_offer      → 4 tier negotiation engine
       │   ├── order_placed     → Create order + bill + credit check
       │   ├── order_status     → Lookup by order ref or phone
       │   ├── delivery_query   → Return ETA and timeline
       │   └── cancel_order     → Cancel if not dispatched
       │
       ├── TTS → ElevenLabs Multilingual v2
       │
       ├── SQLite Database (warehouse.db)
       │   ├── inventory        → Stock levels, thresholds, suppliers
       │   ├── orders           → Orders with unique GDN-YEAR-XXXXXX IDs
       │   ├── delivery_updates → Full delivery timeline per order
       │   ├── bills            → GST invoices (5% agricultural rate)
       │   ├── buyer_credit     → Monthly credit cycles per buyer
       │   ├── credit_transactions
       │   ├── suppliers        → Multi supplier priority chain per item
       │   ├── reorder_log      → Auto reorder call history
       │   ├── followup_log     → Follow up call history
       │   └── call_log         → All inbound call transcripts
       │
       └── Background Threads
           ├── Stock Monitor (every 30s) → Auto-reorder calls
           └── Follow-up Monitor (hourly) → Delivery + payment reminders

<img width="1536" height="1024" alt="System Architecture" src="https://github.com/user-attachments/assets/3d6236bc-f5da-47b0-a5f7-ad86b6bbcfb2" />
           
## ✨ Features

### 📞 Voice Bot
- Handles inbound calls via Twilio
- Understands Hindi, English, and Hinglish (code-switching)
- Processes audio with Gemini 2.5 Flash in one API call
- Responds via ElevenLabs multilingual TTS
- Session-based audio cache (no disk writes, no race conditions)

### 📦 Inventory Management
- Real-time stock tracking for agricultural commodities
- Low stock alerts with configurable thresholds per item
- Auto-reorder triggers when stock drops below threshold

### 🤝 Smart Negotiation
- 4 tier negotiation engine:
  1. Accept immediately if offer ≥ market rate
  2. Accept with minimum quantity condition if offer ≥ floor price
  3. Counter just above floor price with urgency framing
  4. Hard floor refusal with final price

### 📋 Order Management
- Human readable unique order IDs (`GDN-2026-AB3X7K`)
- Estimated delivery date auto-assigned (2–4 business days)
- Full delivery timeline (pending → confirmed → dispatched → out_for_delivery → delivered)
- Order cancellation with stock restoration

### 🧾 Billing
- Auto generated GST bill on every order
- 5% GST rate (agricultural commodities)
- Printable bill page with PDF/print support
- Bill accessible at `/bill/<bill_ref>`

### 💳 Credit Management
- Monthly credit cycles per buyer
- Credit limit enforcement before order confirmation
- Payment recording and balance tracking
- Automated payment reminder calls (3 days before due, on due date, after overdue)

### 🔄 Auto-Reorder with Supplier Confirmation
- Background thread checks stock every 30 seconds
- Calls primary supplier when stock is low
- Supplier responds via voice (yes/no/new date)
- If supplier declines → automatically calls next supplier in priority chain
- Priority chain prevents infinite loops (strictly moves forward)

### 📅 Follow-up Calls
- Calls buyer the day before estimated delivery
- Calls supplier to confirm delivery is on track
- If supplier reports delay → updates delivery date in DB
- Payment reminder calls on monthly credit cycle

### 📊 REST API (for website integration)
All endpoints return JSON with CORS enabled.


## 🛠️ Tech Stack

| Layer | Technology |
|-------|-----------|
| Voice Calls | Twilio Programmable Voice |
| Speech + Intent | Google Gemini 2.5 Flash |
| Text-to-Speech | ElevenLabs Multilingual v2 |
| Backend | Python + Flask |
| Database | SQLite |
| CORS / API | Flask-CORS |
| Tunneling (dev) | ngrok |


## 🚀 Setup

### 1. Clone and install dependencies

bash
git clone https://github.com/yourusername/logistey.git
cd logistey
pip3 install flask flask-cors twilio google-genai python-dotenv requests
```

### 2. Create `.env` file

env
GEMINI_API_KEY=your_gemini_api_key
TWILIO_ACCOUNT_SID=your_twilio_sid
TWILIO_AUTH_TOKEN=your_twilio_auth_token
TWILIO_PHONE_NUMBER=+1xxxxxxxxxx
ELEVENLABS_API_KEY=your_elevenlabs_key
BASE_URL=https://your-ngrok-or-server-url.com

### 3. Start ngrok (for local development)

bash
ngrok http 5000


Copy the `https://xxxx.ngrok-free.app` URL and set it as `BASE_URL` in `.env`.

### 4. Set Twilio webhook

Go to **twilio.com/console → Phone Numbers → your number → Voice Configuration** and set the webhook to:

https://your-ngrok-url/voice


### 5. Run the server

bash
python3 app.py


You should see:

[REORDER] Stock monitor started — checking every 30 seconds.
[FOLLOWUP] Follow-up call monitor started — checking every hour.
* Running on http://127.0.0.1:5000

## 📡 API Reference

### Inventory
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/inventory` | All items with stock and price |
| PATCH | `/api/inventory/<item>` | Update price, stock, or min price |
| POST | `/api/inventory/<item>/supplier` | Set primary supplier |
| POST | `/api/inventory/<item>/suppliers` | Add supplier to priority chain |
| POST | `/api/inventory/<item>/test-reorder` | Manually trigger reorder call |

### Orders
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/orders` | List orders (filter by status/phone/item) |
| POST | `/api/orders` | Create order directly |
| GET | `/api/orders/<ref>` | Full order detail + delivery timeline |
| POST | `/api/orders/<ref>/status` | Update order status |

### Buyers & Credit
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/buyers` | All buyers with credit balances |
| POST | `/api/buyers` | Register a buyer with credit account |
| GET | `/api/buyers/<phone>` | Buyer detail + transactions + bills |
| POST | `/api/buyers/<phone>/payment` | Record a payment received |

### Bills
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/bills` | All bills |
| GET | `/api/bills/<ref>` | Single bill detail |
| GET | `/bill/<ref>` | Printable bill page (HTML) |

### Analytics
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/stats` | Summary stats for dashboard |
| GET | `/api/calls` | Last 50 call logs |
| GET | `/api/reorder-log` | Auto-reorder call history |
| GET | `/api/followup-log` | Follow-up call history |

### Exports
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/export/orders.csv` | Orders as CSV |
| GET | `/export/inventory.csv` | Inventory as CSV |
| GET | `/export/buyers.csv` | Buyers as CSV |
| GET | `/export/bills.csv` | Bills as CSV |
| GET | `/export/calls.csv` | Call logs as CSV |
| GET | `/export/all` | All data as JSON |

### Dashboards (HTML)
| Endpoint | Description |
|----------|-------------|
| `/dashboard` | Live inventory + call log dashboard |
| `/orders` | Orders management with status updates |

## 🌐 Website Integration

Drop `logistey-integration.js` in your website folder and add to your HTML:

```html
<script src="logistey-integration.js"></script>
```

Update `BASE_URL` in the file to your server URL. The script auto-loads real data into your dashboard and refreshes every 30 seconds.


## 📱 Supported Voice Intents

| Intent | Example (Hindi/English) |
|--------|------------------------|
| `stock_arrival` | "200 kilo pyaaz aaya godown mein" |
| `stock_query` | "Tamatar kitna hai aur rate kya hai?" |
| `price_offer` | "40 rupay per kilo mein doge onions?" |
| `price_counter` | "Theek hai 43 pe kar do" |
| `deal_confirm` | "Haan deal pakka" |
| `order_placed` | "100 kilo chawal ka order lagao" |
| `order_status` | "Mera order GDN-2026-AB3X7K kahan hai?" |
| `delivery_query` | "Kab tak delivery hogi?" |
| `cancel_order` | "Order cancel kar do" |

## 🗂️ Project Structure

```
logistey/
├── app.py                  # Main Flask application
├── warehouse.db            # SQLite database (auto-created)
├── logistey-sdk.js         # JavaScript SDK for website integration
├── logistey-integration.js # Frontend data integration script
├── .env                    # Environment variables (not committed)
├── .env.example            # Environment variable template
├── requirements.txt        # Python dependencies
└── README.md
```

## ⚠️ Important Notes

- **Twilio Trial Account**: Can only call verified numbers. Verify all numbers at twilio.com/console → Verified Caller IDs
- **ngrok URL**: Changes every restart unless you have a paid ngrok plan. Update `BASE_URL` in `.env` and Twilio webhook each time
- **Production**: Replace `sqlite3` with PostgreSQL (Supabase recommended), run with `gunicorn` instead of Flask dev server, use a persistent server instead of ngrok


## 🏆 Built For

Hackathon project demonstrating AI-powered voice automation for India's agricultural supply chain — enabling warehouse owners to manage operations entirely through phone calls in their native language.
