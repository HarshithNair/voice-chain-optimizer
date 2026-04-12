import os, json, requests, uuid, threading, time
from flask import Flask, request, Response, redirect, url_for, jsonify
from flask_cors import CORS
from twilio.twiml.voice_response import VoiceResponse
from dotenv import load_dotenv
from google import genai
import sqlite3
from datetime import datetime, timedelta
import random
import string

load_dotenv()
app = Flask(__name__)
CORS(app)  # Allow cross-origin requests from your website
audio_cache = {}  # In-memory TTS audio store: session_id -> bytes

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

# ── DB SETUP ─────────────────────────────────────────

def get_db():
    conn = sqlite3.connect("warehouse.db")
    conn.row_factory = sqlite3.Row  # Return dict-like rows
    return conn

def generate_order_id():
    """Generate a human-readable unique order ID like GDN-2024-ABCD"""
    year = datetime.now().year
    suffix = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f"GDN-{year}-{suffix}"

def init_db():
    conn = get_db()
    
    conn.execute("""CREATE TABLE IF NOT EXISTS inventory (
        id INTEGER PRIMARY KEY,
        item TEXT UNIQUE,
        quantity REAL,
        unit TEXT,
        price_per_unit REAL,
        min_price REAL,              -- floor price for negotiation
        reorder_threshold REAL DEFAULT 100,  -- auto-order when stock drops below this
        reorder_quantity REAL DEFAULT 500,   -- how much to order from supplier
        supplier_phone TEXT,         -- supplier phone number to call
        supplier_name TEXT,          -- supplier display name
        last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")

    # Auto-reorder call log
    conn.execute("""CREATE TABLE IF NOT EXISTS reorder_log (
        id INTEGER PRIMARY KEY,
        item TEXT,
        quantity_ordered REAL,
        supplier_phone TEXT,
        trigger_stock REAL,
        status TEXT DEFAULT 'called',
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")

    # Buyer credit accounts — one row per buyer phone
    conn.execute("""CREATE TABLE IF NOT EXISTS buyer_credit (
        id INTEGER PRIMARY KEY,
        buyer_phone TEXT UNIQUE,
        buyer_name TEXT,
        credit_limit REAL DEFAULT 50000,
        outstanding_balance REAL DEFAULT 0,
        cycle_start_date DATE DEFAULT (DATE('now', 'start of month')),
        cycle_due_date DATE DEFAULT (DATE('now', 'start of month', '+1 month')),
        gst_number TEXT,
        address TEXT,
        last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")

    # Monthly credit transactions
    conn.execute("""CREATE TABLE IF NOT EXISTS credit_transactions (
        id INTEGER PRIMARY KEY,
        buyer_phone TEXT,
        order_ref TEXT,
        transaction_type TEXT,  -- 'charge'|'payment'|'adjustment'
        amount REAL,
        balance_after REAL,
        note TEXT,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")

    # Bills / invoices
    conn.execute("""CREATE TABLE IF NOT EXISTS bills (
        id INTEGER PRIMARY KEY,
        bill_ref TEXT UNIQUE,       -- BILL-2024-XXXXXX
        order_ref TEXT,
        buyer_phone TEXT,
        buyer_name TEXT,
        buyer_address TEXT,
        gst_number TEXT,
        item TEXT,
        quantity REAL,
        unit_price REAL,
        subtotal REAL,
        gst_rate REAL DEFAULT 5.0,  -- 5% GST on agricultural commodities
        gst_amount REAL,
        total_amount REAL,
        payment_status TEXT DEFAULT 'unpaid',  -- unpaid|partial|paid
        due_date DATE,
        paid_date DATE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")

    # Follow-up call log
    conn.execute("""CREATE TABLE IF NOT EXISTS followup_log (
        id INTEGER PRIMARY KEY,
        order_ref TEXT,
        buyer_phone TEXT,
        call_type TEXT,  -- 'delivery_reminder'|'payment_due'|'overdue'
        call_sid TEXT,
        status TEXT DEFAULT 'called',
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")

    conn.execute("""CREATE TABLE IF NOT EXISTS call_log (
        id INTEGER PRIMARY KEY,
        caller TEXT,
        transcript TEXT,
        intent TEXT,
        item TEXT,
        quantity REAL,
        language TEXT DEFAULT 'english',
        session_id TEXT,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")

    conn.execute("""CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY,
        order_ref TEXT UNIQUE NOT NULL,          -- Human-readable: GDN-2024-ABCD
        caller_phone TEXT,
        item TEXT,
        quantity REAL,
        price_per_unit REAL,
        total_price REAL,
        status TEXT DEFAULT 'pending',           -- pending|confirmed|dispatched|out_for_delivery|delivered|cancelled
        payment_status TEXT DEFAULT 'unpaid',    -- unpaid|partial|paid
        delivery_address TEXT,
        estimated_delivery DATE,
        actual_delivery DATE,
        notes TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")

    conn.execute("""CREATE TABLE IF NOT EXISTS delivery_updates (
        id INTEGER PRIMARY KEY,
        order_ref TEXT NOT NULL,
        status TEXT NOT NULL,
        note TEXT,
        updated_by TEXT DEFAULT 'system',
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(order_ref) REFERENCES orders(order_ref)
    )""")

    conn.execute("""CREATE TABLE IF NOT EXISTS negotiations (
        id INTEGER PRIMARY KEY,
        caller_phone TEXT,
        item TEXT,
        offered_price REAL,
        counter_price REAL,
        accepted INTEGER DEFAULT 0,   -- 0=pending, 1=accepted, -1=rejected
        session_id TEXT,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")

    # Seed inventory — add supplier_phone via /api/inventory/<item> PATCH or dashboard
    # (id, item, qty, unit, price, min_price, threshold, reorder_qty, supplier_phone, supplier_name)
    seeds = [
        (1, 'onions',   500,  'kg', 42, 37, 100, 500, None, None),
        (2, 'tomatoes', 300,  'kg', 38, 33,  80, 300, None, None),
        (3, 'rice',     1000, 'kg', 55, 48, 200, 800, None, None),
        (4, 'potatoes', 400,  'kg', 28, 24, 100, 400, None, None),
        (5, 'lentils',  200,  'kg', 95, 85,  50, 200, None, None),
        (6, 'wheat',    800,  'kg', 32, 28, 150, 600, None, None),
    ]
    for s in seeds:
        conn.execute(
            "INSERT OR IGNORE INTO inventory(id,item,quantity,unit,price_per_unit,min_price,reorder_threshold,reorder_quantity,supplier_phone,supplier_name) VALUES(?,?,?,?,?,?,?,?,?,?)", s)

    conn.commit()
    conn.close()

# ── INVENTORY HELPERS ────────────────────────────────

def get_stock(item):
    conn = get_db()
    row = conn.execute(
        "SELECT quantity, price_per_unit, min_price, unit FROM inventory WHERE item LIKE ?",
        (f"%{item}%",)
    ).fetchone()
    conn.close()
    return row

def update_stock(item, delta):
    conn = get_db()
    conn.execute(
        "UPDATE inventory SET quantity = quantity + ?, last_updated = CURRENT_TIMESTAMP WHERE item LIKE ?",
        (delta, f"%{item}%")
    )
    conn.commit()
    conn.close()

# ── ORDER HELPERS ────────────────────────────────────

def create_order(caller, item, quantity, price_per_unit, address=None, notes=None):
    order_ref = generate_order_id()
    total = round(float(quantity or 0) * float(price_per_unit or 0), 2)
    # Estimated delivery: 2–4 business days from now
    est_delivery = (datetime.now() + timedelta(days=random.randint(2, 4))).strftime("%Y-%m-%d")
    
    conn = get_db()
    conn.execute(
        """INSERT INTO orders(order_ref, caller_phone, item, quantity, price_per_unit, total_price,
           estimated_delivery, delivery_address, notes)
           VALUES(?,?,?,?,?,?,?,?,?)""",
        (order_ref, caller, item, quantity, price_per_unit, total, est_delivery, address, notes)
    )
    # Log first delivery update
    conn.execute(
        "INSERT INTO delivery_updates(order_ref, status, note) VALUES(?,?,?)",
        (order_ref, 'pending', 'Order received and being processed')
    )
    conn.commit()
    conn.close()
    return order_ref, est_delivery, total

def get_order_status(caller=None, order_ref=None):
    conn = get_db()
    if order_ref:
        order = conn.execute(
            "SELECT * FROM orders WHERE order_ref = ?", (order_ref,)
        ).fetchone()
    else:
        order = conn.execute(
            "SELECT * FROM orders WHERE caller_phone = ? ORDER BY created_at DESC LIMIT 1", (caller,)
        ).fetchone()
    
    if not order:
        conn.close()
        return None, []
    
    updates = conn.execute(
        "SELECT status, note, timestamp FROM delivery_updates WHERE order_ref = ? ORDER BY timestamp ASC",
        (order['order_ref'],)
    ).fetchall()
    conn.close()
    return dict(order), [dict(u) for u in updates]

def add_delivery_update(order_ref, status, note, updated_by="system"):
    conn = get_db()
    conn.execute(
        "INSERT INTO delivery_updates(order_ref, status, note, updated_by) VALUES(?,?,?,?)",
        (order_ref, status, note, updated_by)
    )
    conn.execute(
        "UPDATE orders SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE order_ref = ?",
        (status, order_ref)
    )
    if status == 'delivered':
        conn.execute(
            "UPDATE orders SET actual_delivery = DATE('now') WHERE order_ref = ?", (order_ref,)
        )
    conn.commit()
    conn.close()

# ── AUDIO PROCESSING ─────────────────────────────────

def process_audio(audio_url, session_id=None):
    auth = (os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN"))
    audio_data = requests.get(audio_url, auth=auth).content

    prompt = """You are Logistey — a sharp, experienced warehouse manager AI for an Indian agricultural commodity godown (warehouse).
You understand Hindi, English, and Hinglish (mixed). You talk to truck drivers, buyers, and suppliers.

Listen to the audio and return ONLY valid JSON in this exact format (no markdown, no preamble):

{
  "transcript": "exact words spoken",
  "intent": "stock_arrival|stock_query|price_offer|price_counter|deal_confirm|order_placed|order_status|delivery_query|cancel_order|unknown",
  "item": "commodity in English (onions/tomatoes/rice/potatoes/lentils/wheat) or null",
  "quantity": numeric_or_null,
  "unit": "kg|quintal|ton or null",
  "price": numeric_or_null,
  "order_ref": "if caller mentions an order ID like GDN-XXXX-XXXXXX, extract it, else null",
  "delivery_address": "if caller mentions an address, extract it, else null",
  "language": "hindi|english|mixed",
  "urgency": "normal|urgent"
}

Intent definitions:
- stock_arrival: driver/supplier reporting goods have arrived at godown
- stock_query: asking about available quantity or price
- price_offer: buyer making a price offer (they quote a price)
- price_counter: buyer responding to your counter-offer (could be acceptance or new offer)
- deal_confirm: buyer explicitly confirming/accepting a deal
- order_placed: buyer explicitly wanting to place an order
- order_status: asking about existing order status
- delivery_query: asking specifically about delivery date, ETA, tracking
- cancel_order: wanting to cancel an order
- unknown: anything else

Unit conversion: 1 quintal = 100 kg, 1 ton = 1000 kg. Convert all quantities to kg in the quantity field.
Hindi vocab: pyaaz/piaz=onions, tamatar=tomatoes, chawal=rice, aloo=potatoes, dal/masoor=lentils, gehu/gehun=wheat,
kilo/kg=kg, quintal=100kg, tonne/ton=1000kg, rate=price, maal=goods, gaadi=truck, order=order, delivery=delivery"""

    import concurrent.futures
    from google.genai import types

    def _call_gemini():
        return client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[
                prompt,
                types.Part.from_bytes(data=audio_data, mime_type='audio/wav')
            ]
        )

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_call_gemini)
            response = future.result(timeout=8.0)

        raw = response.text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw.strip())
        
        # Normalize quantity based on unit
        if data.get("quantity") and data.get("unit") == "quintal":
            data["quantity"] = float(data["quantity"]) * 100
        elif data.get("quantity") and data.get("unit") == "ton":
            data["quantity"] = float(data["quantity"]) * 1000

        return data.get("transcript", ""), data

    except Exception as e:
        print("Gemini parsing error:", e)
        return "", {"intent": "unknown", "item": None, "quantity": None, "price": None, "language": "english", "urgency": "normal"}

# ── TTS ──────────────────────────────────────────────

def generate_tts(text, lang="en"):
    headers = {
        "xi-api-key": os.getenv("ELEVENLABS_API_KEY"),
        "Content-Type": "application/json"
    }
    voice_id = "pNInz6obpgDQGcFmaJgB"
    model = "eleven_multilingual_v2"
    payload = {
        "text": text,
        "model_id": model,
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}
    }
    r = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
        json=payload, headers=headers, timeout=10
    )
    r.raise_for_status()
    # Return raw bytes — no disk writes, no cleanup needed
    return r.content

# ── RESPONSE BUILDER ─────────────────────────────────

def h(hindi_text, english_text, lang):
    """Helper: return Hindi or English based on detected language"""
    return hindi_text if lang == "hindi" else english_text

def build_response_text(intent_data, caller, session_id=None):
    intent   = intent_data.get("intent", "unknown")
    item     = (intent_data.get("item") or "").lower().strip() or "item"
    qty      = intent_data.get("quantity")
    price    = intent_data.get("price")
    lang     = intent_data.get("language", "english")
    order_ref = intent_data.get("order_ref")
    address  = intent_data.get("delivery_address")
    urgency  = intent_data.get("urgency", "normal")

    # ── Stock Arrival
    if intent == "stock_arrival":
        if qty and item != "item":
            update_stock(item, qty)
            stock = get_stock(item)
            new_qty = stock[0] if stock else "?"
        return h(
            f"Theek hai bhai. {qty} kilo {item} ka maal receive ho gaya. Ab total stock {new_qty} kilo hai.",
            f"Received {qty} kg of {item}. Total stock is now {new_qty} kg. Updated!"
        , lang)

    # ── Stock Query
    elif intent == "stock_query":
        stock = get_stock(item)
        if stock:
            qty_avail, rate, min_p, unit = stock
            return h(
                f"Haan bhai, {item} abhi {qty_avail} kilo available hai. Rate {rate} rupay per kilo hai.",
                f"{item.title()} available: {qty_avail} kg at ₹{rate}/kg."
            , lang)
        return h(f"Bhai, {item} abhi hamare paas nahi hai.", f"Sorry, {item} is not in our inventory right now.", lang)

    # ── Price Offer (buyer making first offer)
    elif intent == "price_offer":
        stock = get_stock(item)
        if not stock:
            return h(f"{item} hamare paas nahi hai.", f"We don't have {item} in stock.", lang)
        
        qty_avail, current_rate, min_price, unit = stock
        offered = float(price) if price else 0

        # Log negotiation
        conn = get_db()
        
        if offered >= current_rate:
            # Accept immediately
            conn.execute(
                "INSERT INTO negotiations(caller_phone,item,offered_price,counter_price,accepted,session_id) VALUES(?,?,?,?,?,?)",
                (caller, item, offered, current_rate, 1, session_id)
            )
            conn.commit(); conn.close()
            return h(
                f"Deal pakka bhai! {offered} rupay per kilo accepted. {qty_avail} kilo available hai. Order laga dun?",
                f"Deal! ₹{offered}/kg accepted. We have {qty_avail} kg available. Want me to place the order?"
            , lang)

        elif offered >= min_price:
            # Slight negotiation — accept with conditions
            conn.execute(
                "INSERT INTO negotiations(caller_phone,item,offered_price,counter_price,accepted,session_id) VALUES(?,?,?,?,?,?)",
                (caller, item, offered, offered, 1, session_id)
            )
            conn.commit(); conn.close()
            return h(
                f"Chalo bhai, {offered} rupay maan lete hain. Lekin minimum {qty or 100} kilo order hona chahiye. Theek hai?",
                f"Alright, ₹{offered}/kg works for us — but minimum order is {qty or 100} kg. Deal?"
            , lang)

        elif offered >= min_price * 0.90:
            # Counter-offer
            counter = round(min_price * 1.01, 0)  # just above floor
            conn.execute(
                "INSERT INTO negotiations(caller_phone,item,offered_price,counter_price,accepted,session_id) VALUES(?,?,?,?,?,?)",
                (caller, item, offered, counter, 0, session_id)
            )
            conn.commit(); conn.close()
            return h(
                f"Bhai {offered} thoda mushkil hai. Maal quality A-grade hai. Best price {counter} rupay de sakta hoon. Kal demand aur badhne wali hai, abhi lock kar lo.",
                f"₹{offered} is a stretch. This is A-grade quality. Best I can do is ₹{counter}/kg — and demand is rising tomorrow. Lock it in now?"
            , lang)

        else:
            # Too low — firm counter
            counter = round(current_rate * 0.97, 0)
            conn.execute(
                "INSERT INTO negotiations(caller_phone,item,offered_price,counter_price,accepted,session_id) VALUES(?,?,?,?,?,?)",
                (caller, item, offered, counter, 0, session_id)
            )
            conn.commit(); conn.close()
            return h(
                f"Bhai {offered} mein toh nahi ho sakta, seedha seedha bol raha hoon. Market rate {current_rate} hai. {counter} pe final karte hain — isse kam possible nahi.",
                f"₹{offered} isn't viable — market rate is ₹{current_rate}. Final offer: ₹{counter}/kg. That's the best I can do."
            , lang)

    # ── Price Counter (buyer responding to our counter)
    elif intent == "price_counter":
        stock = get_stock(item)
        if not stock:
            return h(f"{item} available nahi hai.", f"{item} not available.", lang)
        _, current_rate, min_price, unit = stock
        offered = float(price) if price else 0
        
        if offered >= min_price:
            return h(
                f"Done bhai! {offered} rupay final. Order confirm karte hain?",
                f"Done! ₹{offered}/kg final. Shall I confirm the order?"
            , lang)
        else:
            return h(
                f"Bhai itna nahi ho sakta. {round(min_price, 0)} se neeche possible nahi. Final jawab do.",
                f"Can't go below ₹{round(min_price, 0)}. That's my final number. Yes or no?"
            , lang)

    # ── Deal Confirm
    elif intent == "deal_confirm":
        return h(
            "Sahi hai! Ab order place karna hai toh quantity aur delivery address batao.",
            "Great! To place the order, just tell me the quantity and delivery address."
        , lang)

    # ── Order Placed
    elif intent == "order_placed":
        stock = get_stock(item)
        if not stock:
            return h(f"{item} stock mein nahi hai.", f"{item} is out of stock.", lang)

        _, rate, _, _ = stock
        prc = float(price) if price else rate

        # Credit check for registered buyers
        if qty:
            est_total = round(float(qty) * prc * 1.05, 2)  # include GST estimate
            allowed, reason = check_credit_limit(caller, est_total)
            if not allowed:
                return h(
                    f"Bhai, aapki credit limit exceed ho gayi hai. {reason}. Pehle payment karo.",
                    f"Sorry, your credit limit is exceeded. {reason}. Please clear dues first."
                , lang)

        order_ref_new, est_delivery, total = create_order(caller, item, qty, prc, address)

        # Deduct from stock
        if qty:
            update_stock(item, -float(qty))

        # Auto-generate bill
        bill_ref, bill_total, due_date = create_bill(order_ref_new, caller, item, float(qty or 0), prc)

        return h(
            f"Order ho gaya bhai! Order ID: {order_ref_new}. Bill ID: {bill_ref}. {qty} kilo {item}, total {bill_total} rupay (GST sahit). Delivery {est_delivery} tak. Payment due: {due_date}.",
            f"Order placed! Order ID: {order_ref_new}. Bill: {bill_ref}. {qty}kg {item}, total ₹{bill_total} (incl. GST). Delivery by {est_delivery}. Payment due by {due_date}."
        , lang)

    # ── Order Status
    elif intent == "order_status":
        order, updates = get_order_status(caller=caller, order_ref=order_ref)
        if not order:
            return h(
                "Bhai, aapka koi order database mein nahi mila. Order ID se try karein.",
                "No recent order found. Please try with your Order ID."
            , lang)
        last_update = updates[-1]['note'] if updates else "Processing"
        return h(
            f"Order {order['order_ref']}: {order['item']} {order['quantity']} kilo. Status: {order['status']}. {last_update}. Delivery: {order['estimated_delivery']}.",
            f"Order {order['order_ref']}: {order['quantity']} kg {order['item']}. Status: {order['status'].upper()}. {last_update}. Est. delivery: {order['estimated_delivery']}."
        , lang)

    # ── Delivery Query
    elif intent == "delivery_query":
        order, updates = get_order_status(caller=caller, order_ref=order_ref)
        if not order:
            return h(
                "Koi order nahi mila. Apna Order ID batao.",
                "No order found. Please share your Order ID."
            , lang)
        
        status = order['status']
        eta = order['estimated_delivery']
        
        if status == 'delivered':
            return h(
                f"Bhai, aapka order {order['actual_delivery']} ko deliver ho chuka hai.",
                f"Your order was delivered on {order['actual_delivery']}."
            , lang)
        elif status == 'dispatched':
            return h(
                f"Gaadi nikal chuki hai! {eta} tak pahunch jayega.",
                f"Your order is on the way! Expected by {eta}."
            , lang)
        elif status == 'out_for_delivery':
            return h(
                "Aaj aayega bhai, driver raste mein hai.",
                "Out for delivery today! Driver is on the way."
            , lang)
        else:
            return h(
                f"Order pack ho raha hai. {eta} tak delivery expect karo.",
                f"Order is being prepared. Expected delivery by {eta}."
            , lang)

    # ── Cancel Order
    elif intent == "cancel_order":
        order, _ = get_order_status(caller=caller, order_ref=order_ref)
        if not order:
            return h("Koi order nahi mila cancel karne ke liye.", "No order found to cancel.", lang)
        
        if order['status'] in ['dispatched', 'out_for_delivery', 'delivered']:
            return h(
                f"Bhai, order {order['order_ref']} already {order['status']} hai. Ab cancel nahi ho sakta. Hume call karo.",
                f"Order {order['order_ref']} is already {order['status']} — can't cancel. Please call our support."
            , lang)
        
        add_delivery_update(order['order_ref'], 'cancelled', 'Cancelled by customer via call', 'customer')
        # Return stock
        if order['quantity'] and order['item']:
            update_stock(order['item'], float(order['quantity']))
        
        return h(
            f"Order {order['order_ref']} cancel ho gaya hai. Koi aur kaam ho toh batao.",
            f"Order {order['order_ref']} has been cancelled. Is there anything else I can help with?"
        , lang)

    # ── Unknown
    else:
        return h(
            "Namaste! Main Logistey hoon. Aap stock ki jaankari, price, ya order ke baare mein baat kar sakte hain. Kya chahiye aapko?",
            "Hello! I'm Logistey. You can check stock, get prices, negotiate, or place and track orders. What can I help you with?"
        , lang)

# ── TWILIO VOICE ROUTES ──────────────────────────────

@app.route("/voice", methods=["POST"])
def voice_entry():
    resp = VoiceResponse()
    session_id = str(uuid.uuid4())
    resp.say(
        "Welcome to Logistey. Aap Hindi ya English mein bol sakte hain. Please speak after the beep.",
        voice="alice", language="en-IN"
    )
    resp.record(max_length=20, action=f"/handle-recording?session={session_id}", transcribe=False, play_beep=True)
    return Response(str(resp), mimetype="text/xml")

@app.route("/handle-recording", methods=["POST"])
def handle_recording():
    recording_url = request.form.get("RecordingUrl")
    caller = request.form.get("From", "unknown")
    session_id = request.args.get("session", str(uuid.uuid4()))

    resp = VoiceResponse()
    if not recording_url:
        resp.say("Sorry, I did not catch that. Please call again.", voice="alice", language="en-IN")
        return Response(str(resp), mimetype="text/xml")

    transcript, intent_data = process_audio(recording_url + ".wav", session_id)

    conn = get_db()
    conn.execute(
        "INSERT INTO call_log(caller,transcript,intent,item,quantity,language,session_id) VALUES(?,?,?,?,?,?,?)",
        (caller, transcript, intent_data.get("intent"), intent_data.get("item"),
         intent_data.get("quantity"), intent_data.get("language","english"), session_id)
    )
    conn.commit(); conn.close()

    response_text = build_response_text(intent_data, caller, session_id)

    try:
        audio_bytes = generate_tts(response_text, intent_data.get("language", "english"))
        audio_cache[session_id] = audio_bytes
        resp.play(f"/serve-audio?session={session_id}")
    except Exception as e:
        print("TTS Error:", e)
        lang_code = "hi-IN" if intent_data.get("language") == "hindi" else "en-IN"
        resp.say(response_text, voice="alice", language=lang_code)

    resp.record(max_length=20, action=f"/handle-recording?session={session_id}", transcribe=False, play_beep=True)
    return Response(str(resp), mimetype="text/xml")

@app.route("/serve-audio")
def serve_audio():
    import io
    session_id = request.args.get("session", "default")
    audio = audio_cache.pop(session_id, None)
    if not audio:
        return "", 404
    return Response(io.BytesIO(audio).read(), mimetype="audio/mpeg")

# ── REST API (for your website) ──────────────────────

@app.route("/api/inventory", methods=["GET"])
def api_inventory():
    conn = get_db()
    rows = conn.execute("SELECT item, quantity, unit, price_per_unit, last_updated FROM inventory").fetchall()
    conn.close()
    return jsonify({"success": True, "inventory": [dict(r) for r in rows]})

@app.route("/api/orders", methods=["GET"])
def api_orders():
    """List all orders. Filter by status, phone, or item via query params."""
    conn = get_db()
    query = "SELECT * FROM orders WHERE 1=1"
    params = []
    
    if request.args.get("status"):
        query += " AND status = ?"; params.append(request.args["status"])
    if request.args.get("phone"):
        query += " AND caller_phone LIKE ?"; params.append(f"%{request.args['phone']}%")
    if request.args.get("item"):
        query += " AND item LIKE ?"; params.append(f"%{request.args['item']}%")
    
    query += " ORDER BY created_at DESC LIMIT 100"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return jsonify({"success": True, "orders": [dict(r) for r in rows], "count": len(rows)})

@app.route("/api/orders/<order_ref>", methods=["GET"])
def api_order_detail(order_ref):
    """Get full order details + delivery timeline."""
    order, updates = get_order_status(order_ref=order_ref)
    if not order:
        return jsonify({"success": False, "error": "Order not found"}), 404
    return jsonify({"success": True, "order": order, "timeline": updates})

@app.route("/api/orders/<order_ref>/status", methods=["POST"])
def api_update_order_status(order_ref):
    """Update order status and add a delivery note."""
    data = request.get_json()
    new_status = data.get("status")
    note = data.get("note", f"Status updated to {new_status}")
    updated_by = data.get("updated_by", "admin")
    
    valid_statuses = ['pending', 'confirmed', 'dispatched', 'out_for_delivery', 'delivered', 'cancelled']
    if new_status not in valid_statuses:
        return jsonify({"success": False, "error": f"Invalid status. Use: {valid_statuses}"}), 400
    
    add_delivery_update(order_ref, new_status, note, updated_by)
    return jsonify({"success": True, "order_ref": order_ref, "new_status": new_status})

@app.route("/api/orders", methods=["POST"])
def api_create_order():
    """Create an order directly from website (non-voice)."""
    data = request.get_json()
    required = ["phone", "item", "quantity", "price_per_unit"]
    if not all(data.get(k) for k in required):
        return jsonify({"success": False, "error": f"Required fields: {required}"}), 400
    
    try:
        quantity = float(data["quantity"])
        price_per_unit = float(data["price_per_unit"])
    except (ValueError, TypeError):
        return jsonify({"success": False, "error": "quantity and price_per_unit must be numbers"}), 400

    order_ref, est_delivery, total = create_order(
        data["phone"], data["item"], quantity,
        price_per_unit, data.get("address"), data.get("notes")
    )
    return jsonify({
        "success": True, "order_ref": order_ref,
        "estimated_delivery": est_delivery, "total": total
    }), 201

@app.route("/api/inventory/<item>", methods=["PATCH"])
def api_update_inventory(item):
    """Update price or stock quantity."""
    data = request.get_json()
    conn = get_db()
    if "price_per_unit" in data:
        conn.execute("UPDATE inventory SET price_per_unit = ? WHERE item = ?", (data["price_per_unit"], item))
    if "quantity_delta" in data:
        conn.execute("UPDATE inventory SET quantity = quantity + ? WHERE item = ?", (data["quantity_delta"], item))
    if "min_price" in data:
        conn.execute("UPDATE inventory SET min_price = ? WHERE item = ?", (data["min_price"], item))
    conn.execute("UPDATE inventory SET last_updated = CURRENT_TIMESTAMP WHERE item = ?", (item,))
    conn.commit(); conn.close()
    return jsonify({"success": True})

@app.route("/api/calls", methods=["GET"])
def api_calls():
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM call_log ORDER BY timestamp DESC LIMIT 50"
    ).fetchall()
    conn.close()
    return jsonify({"success": True, "calls": [dict(r) for r in rows]})

@app.route("/api/stats", methods=["GET"])
def api_stats():
    """Dashboard summary stats for your website."""
    conn = get_db()
    total_orders = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    pending = conn.execute("SELECT COUNT(*) FROM orders WHERE status='pending'").fetchone()[0]
    revenue = conn.execute("SELECT SUM(total_price) FROM orders WHERE status != 'cancelled'").fetchone()[0] or 0
    calls_today = conn.execute(
        "SELECT COUNT(*) FROM call_log WHERE DATE(timestamp) = DATE('now')"
    ).fetchone()[0]
    top_item = conn.execute(
        "SELECT item, SUM(quantity) as total FROM orders GROUP BY item ORDER BY total DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return jsonify({
        "success": True,
        "stats": {
            "total_orders": total_orders,
            "pending_orders": pending,
            "total_revenue": round(revenue, 2),
            "calls_today": calls_today,
            "top_selling_item": dict(top_item) if top_item else None
        }
    })

# ── HTML DASHBOARDS ──────────────────────────────────

@app.route("/dashboard")
def dashboard():
    conn = get_db()
    inventory = conn.execute("SELECT item, quantity, unit, price_per_unit, last_updated FROM inventory").fetchall()
    calls = conn.execute(
        "SELECT caller, transcript, intent, item, quantity, language, timestamp FROM call_log ORDER BY timestamp DESC LIMIT 20"
    ).fetchall()
    conn.close()
    
    html = """<!DOCTYPE html><html><head><title>Logistey Dashboard</title>
    <style>
    *{box-sizing:border-box;margin:0;padding:0}
    body{font-family:'Segoe UI',sans-serif;background:#0f1117;color:#e1e4e8;min-height:100vh;padding:24px}
    h1{font-size:24px;font-weight:700;color:#58a6ff;margin-bottom:4px}
    .subtitle{color:#8b949e;font-size:13px;margin-bottom:24px}
    .nav{display:flex;gap:12px;margin-bottom:24px}
    .nav a{color:#58a6ff;text-decoration:none;font-size:13px;padding:6px 12px;background:#161b22;border-radius:6px;border:1px solid #30363d}
    .nav a:hover{background:#1f6feb20}
    .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:16px;margin-bottom:24px}
    .card{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:20px}
    .card h3{font-size:12px;color:#8b949e;text-transform:uppercase;letter-spacing:1px;margin-bottom:8px}
    .card .val{font-size:28px;font-weight:700;color:#58a6ff}
    .card .sub{font-size:12px;color:#8b949e;margin-top:4px}
    h2{font-size:16px;font-weight:600;margin-bottom:12px;color:#e1e4e8}
    table{width:100%;border-collapse:collapse;background:#161b22;border-radius:10px;overflow:hidden;border:1px solid #30363d;margin-bottom:24px}
    th{background:#21262d;color:#8b949e;padding:10px 14px;text-align:left;font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:.5px}
    td{padding:10px 14px;border-bottom:1px solid #21262d;font-size:13px}
    tr:last-child td{border-bottom:none}
    .badge{display:inline-block;padding:2px 8px;border-radius:12px;font-size:11px;font-weight:600}
    .stock_arrival{background:#033a16;color:#3fb950}.stock_query{background:#0d2d6b;color:#58a6ff}
    .price_offer{background:#3d1f00;color:#e3b341}.deal_confirm{background:#2d0b4e;color:#bc8cff}
    .order_placed{background:#1a2b00;color:#7ee787}.delivery_query{background:#001f3d;color:#79c0ff}
    .order_status{background:#2d1f00;color:#ffa657}
    </style></head><body>
    <h1>⚡ Logistey</h1>
    <p class="subtitle">Live Warehouse Intelligence Dashboard</p>
    <div class="nav">
      <a href="/dashboard">📊 Dashboard</a>
      <a href="/orders">📦 Orders</a>
      <a href="/api/stats">📈 API Stats</a>
    </div>
    <div class="grid">"""
    
    total_stock = sum(r['quantity'] for r in inventory)
    html += f"""
      <div class="card"><h3>Total Items</h3><div class="val">{len(inventory)}</div><div class="sub">Commodities tracked</div></div>
      <div class="card"><h3>Total Stock</h3><div class="val">{total_stock:,.0f}</div><div class="sub">kg across all items</div></div>
    </div>
    <h2>Current Inventory</h2>
    <table><tr><th>Item</th><th>Stock (kg)</th><th>Unit</th><th>Price/kg</th><th>Last Updated</th></tr>"""
    
    for row in inventory:
        html += f"<tr><td>{row['item'].title()}</td><td>{row['quantity']}</td><td>{row['unit']}</td><td>₹{row['price_per_unit']}</td><td>{row['last_updated']}</td></tr>"
    
    html += """</table><h2>Recent Calls</h2>
    <table><tr><th>Caller</th><th>Said</th><th>Intent</th><th>Item</th><th>Qty</th><th>Lang</th><th>Time</th></tr>"""
    
    for row in calls:
        bc = row['intent'] if row['intent'] else 'unknown'
        transcript = (row['transcript'] or '')[:55] + ('...' if len(row['transcript'] or '') > 55 else '')
        html += f"<tr><td>{row['caller']}</td><td>{transcript}</td><td><span class='badge {bc}'>{bc}</span></td><td>{row['item'] or '—'}</td><td>{row['quantity'] or '—'}</td><td>{row['language'] or '—'}</td><td>{row['timestamp']}</td></tr>"
    
    html += "</table></body></html>"
    return html

@app.route("/orders")
def orders_dashboard():
    conn = get_db()
    orders = conn.execute(
        "SELECT id, order_ref, caller_phone, item, quantity, price_per_unit, total_price, status, estimated_delivery, created_at FROM orders ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    
    status_colors = {
        'pending':'#3d1f00;color:#e3b341',
        'confirmed':'#0d2d6b;color:#58a6ff',
        'dispatched':'#1a2b00;color:#7ee787',
        'out_for_delivery':'#003d1a;color:#3fb950',
        'delivered':'#033a16;color:#3fb950',
        'cancelled':'#2d0b0b;color:#f85149'
    }
    
    html = """<!DOCTYPE html><html><head><title>Logistey — Orders</title>
    <style>
    *{box-sizing:border-box;margin:0;padding:0}
    body{font-family:'Segoe UI',sans-serif;background:#0f1117;color:#e1e4e8;padding:24px}
    h1{font-size:24px;font-weight:700;color:#58a6ff;margin-bottom:20px}
    .nav{display:flex;gap:12px;margin-bottom:24px}
    .nav a{color:#58a6ff;text-decoration:none;font-size:13px;padding:6px 12px;background:#161b22;border-radius:6px;border:1px solid #30363d}
    table{width:100%;border-collapse:collapse;background:#161b22;border-radius:10px;overflow:hidden;border:1px solid #30363d}
    th{background:#21262d;color:#8b949e;padding:10px 14px;text-align:left;font-size:12px;font-weight:600;text-transform:uppercase}
    td{padding:10px 14px;border-bottom:1px solid #21262d;font-size:13px}
    .badge{display:inline-block;padding:3px 9px;border-radius:12px;font-size:11px;font-weight:700;background:}
    form{display:inline} select{background:#21262d;color:#e1e4e8;border:1px solid #30363d;border-radius:4px;padding:4px 6px;font-size:12px}
    button{padding:4px 10px;background:#238636;color:white;border:none;border-radius:4px;cursor:pointer;font-size:12px}
    button:hover{background:#2ea043}
    .order-ref{font-family:monospace;font-size:12px;color:#79c0ff}
    </style></head><body>
    <h1>📦 Orders Management</h1>
    <div class="nav"><a href="/dashboard">← Dashboard</a><a href="/api/orders">API JSON</a></div>
    <table><tr><th>Order ID</th><th>Phone</th><th>Item</th><th>Qty</th><th>Price</th><th>Total</th><th>Status</th><th>Est. Delivery</th><th>Update</th></tr>"""
    
    for row in orders:
        r = dict(row)
        color = status_colors.get(r['status'], '2d2d2d;color:#e1e4e8')
        html += f"""<tr>
          <td class="order-ref">{r['order_ref']}</td>
          <td>{r['caller_phone']}</td>
          <td>{(r['item'] or '').title()}</td>
          <td>{r['quantity']} kg</td>
          <td>₹{r['price_per_unit']}</td>
          <td>₹{r['total_price']}</td>
          <td><span class="badge" style="background:#{color}">{r['status'].replace('_',' ').upper()}</span></td>
          <td>{r['estimated_delivery'] or '—'}</td>
          <td>
            <form action="/update-order/{r['order_ref']}" method="POST">
              <select name="status">
                {''.join(f'<option value="{s}" {"selected" if s==r["status"] else ""}>{s.replace("_"," ").title()}</option>' for s in ["pending","confirmed","dispatched","out_for_delivery","delivered","cancelled"])}
              </select>
              <button type="submit">✓</button>
            </form>
          </td>
        </tr>"""
    
    html += "</table></body></html>"
    return html

@app.route("/update-order/<order_ref>", methods=["POST"])
def update_order(order_ref):
    new_status = request.form.get("status")
    if new_status:
        note_map = {
            'confirmed': 'Order confirmed by warehouse',
            'dispatched': 'Order dispatched from warehouse',
            'out_for_delivery': 'Out for delivery with driver',
            'delivered': 'Successfully delivered to customer',
            'cancelled': 'Cancelled by admin'
        }
        add_delivery_update(order_ref, new_status, note_map.get(new_status, f'Status set to {new_status}'), 'admin')
    return redirect(url_for('orders_dashboard'))

# ── AUTO REORDER ─────────────────────────────────────

def call_supplier(item, quantity, supplier_phone, supplier_name):
    """Make an outbound Twilio call to the supplier requesting stock with confirmation."""
    try:
        from twilio.rest import Client as TwilioClient
        import urllib.parse
        twilio = TwilioClient(os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN"))
        your_number = os.getenv("TWILIO_PHONE_NUMBER")
        base_url = os.getenv("BASE_URL", "http://127.0.0.1:5000")

        # Use interactive confirmation route instead of one-way TwiML
        params = urllib.parse.urlencode({
            "item": item,
            "quantity": quantity,
            "supplier_name": supplier_name or "supplier"
        })
        call = twilio.calls.create(
            url=f"{base_url}/reorder-confirm-call?{params}",
            to=supplier_phone,
            from_=your_number
        )

        # Log the reorder
        conn = get_db()
        trigger_stock = conn.execute(
            "SELECT quantity FROM inventory WHERE item = ?", (item,)
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO reorder_log(item, quantity_ordered, supplier_phone, trigger_stock, status) VALUES(?,?,?,?,?)",
            (item, quantity, supplier_phone, trigger_stock, 'called')
        )
        conn.commit()
        conn.close()
        print(f"[REORDER] Called supplier for {quantity}kg of {item}. Call SID: {call.sid}")

    except Exception as e:
        print(f"[REORDER ERROR] Failed to call supplier for {item}: {e}")

@app.route("/reorder-confirm-call", methods=["POST"])
def reorder_confirm_call():
    """Twilio calls this when supplier picks up for a reorder call."""
    item = request.args.get("item", "goods")
    quantity = request.args.get("quantity", "some")
    supplier_name = request.args.get("supplier_name", "supplier")
    resp = VoiceResponse()
    msg = (
        f"Hello {supplier_name}. This is Logistey automated system. "
        f"Our stock of {item} has dropped critically low. "
        f"We urgently need {quantity} kilograms of {item}. "
        f"Please confirm after the beep — say yes if you can fulfill this order, "
        f"or say no and give us an alternative date or quantity."
    )
    resp.say(msg, voice="alice", language="en-IN")
    resp.record(
        max_length=20,
        action=f"/reorder-confirm-response?item={item}&quantity={quantity}",
        transcribe=False,
        play_beep=True
    )
    return Response(str(resp), mimetype="text/xml")

@app.route("/reorder-confirm-response", methods=["POST"])
def reorder_confirm_response():
    """Handle supplier's response to a reorder request."""
    recording_url = request.form.get("RecordingUrl")
    item = request.args.get("item", "goods")
    quantity = request.args.get("quantity", "0")
    resp = VoiceResponse()

    if not recording_url:
        resp.say("Sorry, I did not catch that. We will follow up shortly. Thank you.", voice="alice", language="en-IN")
        return Response(str(resp), mimetype="text/xml")

    result = process_supplier_audio(recording_url + ".wav")

    conn = get_db()
    if result.get("confirmed") == True:
        conn.execute(
            "INSERT INTO reorder_log(item, quantity_ordered, supplier_phone, trigger_stock, status) VALUES(?,?,?,?,?)",
            (item, quantity, "supplier", 0, "confirmed")
        )
        conn.commit(); conn.close()
        resp.say(
            f"Thank you for confirming! We have noted that {quantity} kilograms of {item} will be delivered. "
            f"Our team will be ready to receive it. Have a good day.",
            voice="alice", language="en-IN"
        )
    elif result.get("confirmed") == False:
        new_date = result.get("new_date")
        reason = result.get("reason") or "supplier unable to fulfill immediately"
        conn.execute(
            "INSERT INTO reorder_log(item, quantity_ordered, supplier_phone, trigger_stock, status) VALUES(?,?,?,?,?)",
            (item, quantity, "supplier", 0, f"delayed: {reason}")
        )
        conn.commit(); conn.close()
        if new_date:
            resp.say(
                f"Understood. We have noted the delay. New expected delivery date is {new_date}. "
                f"Our team will plan accordingly. Thank you.",
                voice="alice", language="en-IN"
            )
        else:
            resp.say(
                f"Understood. Please arrange delivery as soon as possible and call us back to confirm. Thank you.",
                voice="alice", language="en-IN"
            )
    else:
        conn.close()
        resp.say("Sorry, I could not process your response. We will call again shortly. Thank you.", voice="alice", language="en-IN")

    return Response(str(resp), mimetype="text/xml")

def check_stock_levels():
    """Background thread: check inventory every 5 minutes, call supplier if low."""
    # Track which items we already called about to avoid spamming
    already_alerted = set()

    while True:
        try:
            conn = get_db()
            low_items = conn.execute("""
                SELECT item, quantity, reorder_threshold, reorder_quantity, supplier_phone, supplier_name
                FROM inventory
                WHERE quantity <= reorder_threshold
                AND supplier_phone IS NOT NULL
            """).fetchall()
            conn.close()

            for row in low_items:
                item = row['item']
                if item not in already_alerted:
                    print(f"[REORDER] {item} is low ({row['quantity']}kg <= threshold {row['reorder_threshold']}kg). Calling supplier...")
                    call_supplier(item, row['reorder_quantity'], row['supplier_phone'], row['supplier_name'])
                    already_alerted.add(item)

            # Clear alert history for items that have been restocked
            conn = get_db()
            restocked = conn.execute("""
                SELECT item FROM inventory
                WHERE quantity > reorder_threshold
            """).fetchall()
            conn.close()
            for row in restocked:
                already_alerted.discard(row['item'])

        except Exception as e:
            print(f"[STOCK CHECK ERROR] {e}")

        time.sleep(300)  # Check every 5 minutes

# ── SUPPLIER API ROUTES ───────────────────────────────

@app.route("/api/inventory/<item>/supplier", methods=["POST"])
def set_supplier(item):
    """Set supplier details and reorder config for an item."""
    data = request.get_json()
    conn = get_db()
    conn.execute("""
        UPDATE inventory SET
            supplier_phone = ?,
            supplier_name = ?,
            reorder_threshold = ?,
            reorder_quantity = ?,
            last_updated = CURRENT_TIMESTAMP
        WHERE item = ?
    """, (
        data.get("supplier_phone"),
        data.get("supplier_name"),
        data.get("reorder_threshold"),
        data.get("reorder_quantity"),
        item
    ))
    conn.commit()
    conn.close()
    return jsonify({"success": True, "message": f"Supplier set for {item}"})

@app.route("/api/reorder-log", methods=["GET"])
def api_reorder_log():
    """View history of all auto-reorder calls made."""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM reorder_log ORDER BY timestamp DESC LIMIT 50"
    ).fetchall()
    conn.close()
    return jsonify({"success": True, "reorders": [dict(r) for r in rows]})

@app.route("/api/inventory/<item>/test-reorder", methods=["POST"])
def test_reorder(item):
    """Manually trigger a reorder call for testing."""
    conn = get_db()
    row = conn.execute(
        "SELECT reorder_quantity, supplier_phone, supplier_name FROM inventory WHERE item = ?", (item,)
    ).fetchone()
    conn.close()
    if not row or not row['supplier_phone']:
        return jsonify({"success": False, "error": "No supplier configured for this item"}), 400
    call_supplier(item, row['reorder_quantity'], row['supplier_phone'], row['supplier_name'])
    return jsonify({"success": True, "message": f"Test reorder call placed for {item}"})


# ── BILLING HELPERS ───────────────────────────────────

def generate_bill_ref():
    year = datetime.now().year
    suffix = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f"BILL-{year}-{suffix}"

def create_bill(order_ref, buyer_phone, item, quantity, unit_price, gst_rate=5.0):
    subtotal = round(quantity * unit_price, 2)
    gst_amount = round(subtotal * gst_rate / 100, 2)
    total = round(subtotal + gst_amount, 2)
    bill_ref = generate_bill_ref()
    due_date = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")

    conn = get_db()
    # Get buyer details if registered
    buyer = conn.execute(
        "SELECT buyer_name, address, gst_number FROM buyer_credit WHERE buyer_phone = ?",
        (buyer_phone,)
    ).fetchone()
    buyer_name = buyer['buyer_name'] if buyer else None
    buyer_address = buyer['address'] if buyer else None
    gst_number = buyer['gst_number'] if buyer else None

    conn.execute("""
        INSERT INTO bills(bill_ref, order_ref, buyer_phone, buyer_name, buyer_address,
            gst_number, item, quantity, unit_price, subtotal, gst_rate, gst_amount,
            total_amount, due_date)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (bill_ref, order_ref, buyer_phone, buyer_name, buyer_address,
          gst_number, item, quantity, unit_price, subtotal, gst_rate,
          gst_amount, total, due_date))

    # Charge buyer's credit account
    existing = conn.execute(
        "SELECT outstanding_balance FROM buyer_credit WHERE buyer_phone = ?", (buyer_phone,)
    ).fetchone()
    if existing:
        new_balance = round(existing['outstanding_balance'] + total, 2)
        conn.execute(
            "UPDATE buyer_credit SET outstanding_balance = ?, last_updated = CURRENT_TIMESTAMP WHERE buyer_phone = ?",
            (new_balance, buyer_phone)
        )
        conn.execute("""
            INSERT INTO credit_transactions(buyer_phone, order_ref, transaction_type, amount, balance_after, note)
            VALUES(?,?,?,?,?,?)
        """, (buyer_phone, order_ref, 'charge', total, new_balance, f"Order {order_ref} - {quantity}kg {item}"))

    conn.commit()
    conn.close()
    return bill_ref, total, due_date

def get_buyer_credit(buyer_phone):
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM buyer_credit WHERE buyer_phone = ?", (buyer_phone,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None

def check_credit_limit(buyer_phone, order_total):
    """Returns (allowed, reason). Blocks order if over limit."""
    buyer = get_buyer_credit(buyer_phone)
    if not buyer:
        return True, None  # No credit account = cash buyer, always allow
    available = buyer['credit_limit'] - buyer['outstanding_balance']
    if order_total > available:
        return False, f"Credit limit exceeded. Available: ₹{available:.0f}, Order total: ₹{order_total:.0f}"
    return True, None

# ── FOLLOW-UP CALL HELPERS ────────────────────────────

def make_followup_call(buyer_phone, message, order_ref, call_type):
    try:
        from twilio.rest import Client as TwilioClient
        twilio = TwilioClient(os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN"))
        twiml = f'<Response><Say voice="alice" language="en-IN">{message}</Say></Response>'
        call = twilio.calls.create(
            twiml=twiml,
            to=buyer_phone,
            from_=os.getenv("TWILIO_PHONE_NUMBER")
        )
        conn = get_db()
        conn.execute(
            "INSERT INTO followup_log(order_ref, buyer_phone, call_type, call_sid, status) VALUES(?,?,?,?,?)",
            (order_ref, buyer_phone, call_type, call.sid, 'called')
        )
        conn.commit()
        conn.close()
        print(f"[FOLLOWUP] {call_type} call placed to {buyer_phone} for order {order_ref}")
    except Exception as e:
        print(f"[FOLLOWUP ERROR] {e}")

def check_followups():
    """Background thread: delivery reminders + payment overdue calls."""
    already_called = set()

    while True:
        try:
            conn = get_db()
            tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

            # Delivery reminder: call buyer day before estimated delivery
            due_tomorrow = conn.execute("""
                SELECT o.order_ref, o.caller_phone, o.item, o.quantity, o.estimated_delivery
                FROM orders o
                WHERE o.estimated_delivery = ?
                AND o.status NOT IN ('delivered', 'cancelled')
                AND o.caller_phone IS NOT NULL
            """, (tomorrow,)).fetchall()

            for row in due_tomorrow:
                key = f"delivery_{row['order_ref']}"
                if key not in already_called:
                    msg = (
                        f"Hello! This is a reminder from Logistey. "
                        f"Your order of {row['quantity']} kilograms of {row['item']} "
                        f"with order ID {row['order_ref']} is scheduled for delivery tomorrow. "
                        f"Please ensure someone is available to receive it. Thank you."
                    )
                    make_followup_call(row['caller_phone'], msg, row['order_ref'], 'delivery_reminder')
                    already_called.add(key)

            # Payment overdue: call buyers whose credit cycle due date has passed
            overdue_buyers = conn.execute("""
                SELECT buyer_phone, buyer_name, outstanding_balance, cycle_due_date
                FROM buyer_credit
                WHERE outstanding_balance > 0
                AND cycle_due_date < DATE('now')
            """).fetchall()

            for buyer in overdue_buyers:
                key = f"overdue_{buyer['buyer_phone']}_{datetime.now().strftime('%Y-%m')}"
                if key not in already_called:
                    name = buyer['buyer_name'] or 'valued customer'
                    msg = (
                        f"Hello {name}. This is an automated payment reminder from Logistey. "
                        f"Your outstanding balance of rupees {buyer['outstanding_balance']:.0f} "
                        f"was due on {buyer['cycle_due_date']}. "
                        f"Please arrange payment at the earliest to avoid order holds. Thank you."
                    )
                    make_followup_call(buyer['buyer_phone'], msg, 'credit', 'overdue')
                    already_called.add(key)

            # Payment due soon: remind 3 days before cycle end
            due_soon = conn.execute("""
                SELECT buyer_phone, buyer_name, outstanding_balance, cycle_due_date
                FROM buyer_credit
                WHERE outstanding_balance > 0
                AND cycle_due_date = DATE('now', '+3 days')
            """).fetchall()

            for buyer in due_soon:
                key = f"due_soon_{buyer['buyer_phone']}_{datetime.now().strftime('%Y-%m')}"
                if key not in already_called:
                    name = buyer['buyer_name'] or 'valued customer'
                    msg = (
                        f"Hello {name}. Friendly reminder from Logistey. "
                        f"Your payment of rupees {buyer['outstanding_balance']:.0f} "
                        f"is due in 3 days on {buyer['cycle_due_date']}. "
                        f"Please arrange payment on time. Thank you."
                    )
                    make_followup_call(buyer['buyer_phone'], msg, 'credit', 'payment_due')
                    already_called.add(key)

            conn.close()
        except Exception as e:
            print(f"[FOLLOWUP CHECK ERROR] {e}")

        time.sleep(3600)  # Check every hour

# ── CREDIT & BILLING API ROUTES ───────────────────────

@app.route("/api/buyers", methods=["GET"])
def api_buyers():
    conn = get_db()
    rows = conn.execute("SELECT * FROM buyer_credit ORDER BY outstanding_balance DESC").fetchall()
    conn.close()
    return jsonify({"success": True, "buyers": [dict(r) for r in rows]})

@app.route("/api/buyers", methods=["POST"])
def api_create_buyer():
    """Register a buyer with credit account."""
    data = request.get_json()
    if not data.get("phone"):
        return jsonify({"success": False, "error": "phone required"}), 400
    conn = get_db()
    try:
        conn.execute("""
            INSERT INTO buyer_credit(buyer_phone, buyer_name, credit_limit, gst_number, address)
            VALUES(?,?,?,?,?)
        """, (data["phone"], data.get("name"), data.get("credit_limit", 50000),
              data.get("gst_number"), data.get("address")))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"success": False, "error": "Buyer already exists"}), 400
    conn.close()
    return jsonify({"success": True, "message": f"Buyer {data['phone']} registered"})

@app.route("/api/buyers/<phone>", methods=["GET"])
def api_buyer_detail(phone):
    conn = get_db()
    buyer = conn.execute("SELECT * FROM buyer_credit WHERE buyer_phone = ?", (phone,)).fetchone()
    if not buyer:
        return jsonify({"success": False, "error": "Buyer not found"}), 404
    txns = conn.execute(
        "SELECT * FROM credit_transactions WHERE buyer_phone = ? ORDER BY timestamp DESC LIMIT 20", (phone,)
    ).fetchall()
    bills = conn.execute(
        "SELECT * FROM bills WHERE buyer_phone = ? ORDER BY created_at DESC", (phone,)
    ).fetchall()
    conn.close()
    return jsonify({
        "success": True,
        "buyer": dict(buyer),
        "transactions": [dict(t) for t in txns],
        "bills": [dict(b) for b in bills]
    })

@app.route("/api/buyers/<phone>/payment", methods=["POST"])
def api_record_payment(phone):
    """Record a payment received from a buyer."""
    data = request.get_json()
    amount = data.get("amount")
    if not amount:
        return jsonify({"success": False, "error": "amount required"}), 400
    try:
        amount = float(amount)
    except (ValueError, TypeError):
        return jsonify({"success": False, "error": "amount must be a number"}), 400

    conn = get_db()
    buyer = conn.execute(
        "SELECT outstanding_balance FROM buyer_credit WHERE buyer_phone = ?", (phone,)
    ).fetchone()
    if not buyer:
        conn.close()
        return jsonify({"success": False, "error": "Buyer not found"}), 404

    new_balance = round(max(0, buyer['outstanding_balance'] - amount), 2)
    conn.execute(
        "UPDATE buyer_credit SET outstanding_balance = ?, last_updated = CURRENT_TIMESTAMP WHERE buyer_phone = ?",
        (new_balance, phone)
    )
    conn.execute("""
        INSERT INTO credit_transactions(buyer_phone, transaction_type, amount, balance_after, note)
        VALUES(?,?,?,?,?)
    """, (phone, 'payment', amount, new_balance, data.get("note", "Payment received")))
    conn.commit()
    conn.close()
    return jsonify({"success": True, "new_balance": new_balance})

@app.route("/api/bills", methods=["GET"])
def api_bills():
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM bills ORDER BY created_at DESC LIMIT 100"
    ).fetchall()
    conn.close()
    return jsonify({"success": True, "bills": [dict(r) for r in rows]})

@app.route("/api/bills/<bill_ref>", methods=["GET"])
def api_bill_detail(bill_ref):
    conn = get_db()
    bill = conn.execute("SELECT * FROM bills WHERE bill_ref = ?", (bill_ref,)).fetchone()
    conn.close()
    if not bill:
        return jsonify({"success": False, "error": "Bill not found"}), 404
    return jsonify({"success": True, "bill": dict(bill)})

@app.route("/bill/<bill_ref>")
def view_bill(bill_ref):
    """Human-readable printable bill page."""
    conn = get_db()
    bill = conn.execute("SELECT * FROM bills WHERE bill_ref = ?", (bill_ref,)).fetchone()
    conn.close()
    if not bill:
        return "Bill not found", 404
    b = dict(bill)
    html = f"""<!DOCTYPE html><html><head><title>Bill {b['bill_ref']}</title>
    <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{font-family:'Segoe UI',sans-serif;background:#f5f5f5;padding:40px;color:#1a1a1a}}
    .bill{{background:white;max-width:700px;margin:0 auto;padding:40px;border-radius:12px;box-shadow:0 2px 12px rgba(0,0,0,.08)}}
    .header{{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:32px;padding-bottom:24px;border-bottom:2px solid #1d9e75}}
    .logo{{font-size:22px;font-weight:700;color:#1d9e75}}
    .bill-meta{{text-align:right;font-size:13px;color:#555}}
    .bill-meta strong{{display:block;font-size:16px;color:#1a1a1a;margin-bottom:4px}}
    .parties{{display:grid;grid-template-columns:1fr 1fr;gap:24px;margin-bottom:28px}}
    .party h4{{font-size:11px;text-transform:uppercase;letter-spacing:1px;color:#888;margin-bottom:6px}}
    .party p{{font-size:14px;line-height:1.6}}
    table{{width:100%;border-collapse:collapse;margin-bottom:24px}}
    th{{background:#f8f8f8;padding:10px 12px;text-align:left;font-size:12px;text-transform:uppercase;letter-spacing:.5px;color:#555;border-bottom:1px solid #eee}}
    td{{padding:12px;font-size:14px;border-bottom:1px solid #f0f0f0}}
    .totals{{margin-left:auto;width:280px}}
    .totals tr td{{border:none;padding:6px 0}}
    .totals tr td:last-child{{text-align:right;font-weight:500}}
    .grand-total td{{font-size:16px;font-weight:700;color:#1d9e75;border-top:2px solid #1d9e75;padding-top:10px!important}}
    .status-badge{{display:inline-block;padding:4px 12px;border-radius:20px;font-size:12px;font-weight:600;
        background:{'#dcfce7' if b['payment_status']=='paid' else '#fef9c3' if b['payment_status']=='partial' else '#fee2e2'};
        color:{'#166534' if b['payment_status']=='paid' else '#854d0e' if b['payment_status']=='partial' else '#991b1b'}}}
    .footer{{margin-top:32px;padding-top:20px;border-top:1px solid #eee;font-size:12px;color:#888;text-align:center}}
    @media print{{body{{background:white;padding:0}}.bill{{box-shadow:none}}}}
    </style></head><body>
    <div class="bill">
      <div class="header">
        <div>
          <div class="logo">⚡ Logistey</div>
          <p style="font-size:13px;color:#555;margin-top:4px">Warehouse Management System</p>
        </div>
        <div class="bill-meta">
          <strong>{b['bill_ref']}</strong>
          <span>Date: {b['created_at'][:10]}</span><br>
          <span>Due: {b['due_date']}</span><br>
          <span class="status-badge">{b['payment_status'].upper()}</span>
        </div>
      </div>
      <div class="parties">
        <div class="party">
          <h4>From</h4>
          <p><strong>Logistey Warehouse</strong><br>Your Warehouse Address<br>GSTIN: Your GST Number</p>
        </div>
        <div class="party">
          <h4>Bill To</h4>
          <p><strong>{b['buyer_name'] or 'Customer'}</strong><br>
          {b['buyer_address'] or ''}<br>
          {f"GSTIN: {b['gst_number']}" if b['gst_number'] else ''}<br>
          {b['buyer_phone']}</p>
        </div>
      </div>
      <table>
        <tr><th>Item</th><th>Qty (kg)</th><th>Rate (₹/kg)</th><th>Amount (₹)</th></tr>
        <tr><td>{b['item'].title()}</td><td>{b['quantity']}</td><td>₹{b['unit_price']}</td><td>₹{b['subtotal']}</td></tr>
      </table>
      <table class="totals">
        <tr><td>Subtotal</td><td>₹{b['subtotal']}</td></tr>
        <tr><td>GST ({b['gst_rate']}%)</td><td>₹{b['gst_amount']}</td></tr>
        <tr class="grand-total"><td>Total</td><td>₹{b['total_amount']}</td></tr>
      </table>
      <div style="margin-top:16px">
        <p style="font-size:13px;color:#555">Order Reference: <strong>{b['order_ref']}</strong></p>
      </div>
      <div class="footer">
        <p>Thank you for your business. Please make payment by {b['due_date']}.</p>
        <p style="margin-top:4px">Logistey — Automated Warehouse Intelligence</p>
      </div>
    </div>
    <div style="text-align:center;margin-top:20px">
      <button onclick="window.print()" style="padding:10px 24px;background:#1d9e75;color:white;border:none;border-radius:6px;cursor:pointer;font-size:14px">🖨 Print / Save PDF</button>
    </div>
    </body></html>"""
    return html

@app.route("/api/followup-log", methods=["GET"])
def api_followup_log():
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM followup_log ORDER BY timestamp DESC LIMIT 50"
    ).fetchall()
    conn.close()
    return jsonify({"success": True, "followups": [dict(r) for r in rows]})

@app.route("/api/test-followup", methods=["POST"])
def test_followup():
    """Manually fire a delivery reminder call to any number for testing."""
    data = request.get_json()
    phone = data.get("phone")
    item = data.get("item", "onions")
    quantity = data.get("quantity", 500)
    order_ref = data.get("order_ref", "TEST-ORDER")
    delivery_date = data.get("delivery_date", "tomorrow")
    if not phone:
        return jsonify({"success": False, "error": "phone required"}), 400
    msg = (
        f"Hello! This is a reminder from Logistey. "
        f"Your order of {quantity} kilograms of {item} "
        f"with order ID {order_ref} is scheduled for delivery {delivery_date}. "
        f"Please ensure someone is available to receive it. Thank you."
    )
    make_followup_call(phone, msg, order_ref, "delivery_reminder")
    return jsonify({"success": True, "message": f"Follow-up call placed to {phone}"})


# ── CSV EXPORT ROUTES ─────────────────────────────────

@app.route("/export/orders.csv")
def export_orders_csv():
    import csv, io
    conn = get_db()
    rows = conn.execute("SELECT * FROM orders ORDER BY created_at DESC").fetchall()
    conn.close()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Order Ref","Phone","Item","Quantity (kg)","Price/kg","Total","Status","Payment","Delivery Address","Est. Delivery","Actual Delivery","Created At"])
    for r in rows:
        writer.writerow([r["order_ref"],r["caller_phone"],r["item"],r["quantity"],r["price_per_unit"],r["total_price"],r["status"],r["payment_status"],r["delivery_address"],r["estimated_delivery"],r["actual_delivery"],r["created_at"]])
    output.seek(0)
    return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=orders.csv"})

@app.route("/export/inventory.csv")
def export_inventory_csv():
    import csv, io
    conn = get_db()
    rows = conn.execute("SELECT * FROM inventory").fetchall()
    conn.close()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Item","Quantity (kg)","Unit","Price/kg","Min Price","Reorder Threshold","Reorder Quantity","Supplier Phone","Supplier Name","Last Updated"])
    for r in rows:
        writer.writerow([r["item"],r["quantity"],r["unit"],r["price_per_unit"],r["min_price"],r["reorder_threshold"],r["reorder_quantity"],r["supplier_phone"],r["supplier_name"],r["last_updated"]])
    output.seek(0)
    return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=inventory.csv"})

@app.route("/export/buyers.csv")
def export_buyers_csv():
    import csv, io
    conn = get_db()
    rows = conn.execute("SELECT * FROM buyer_credit ORDER BY outstanding_balance DESC").fetchall()
    conn.close()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Phone","Name","Credit Limit","Outstanding Balance","Cycle Due Date","GST Number","Address","Last Updated"])
    for r in rows:
        writer.writerow([r["buyer_phone"],r["buyer_name"],r["credit_limit"],r["outstanding_balance"],r["cycle_due_date"],r["gst_number"],r["address"],r["last_updated"]])
    output.seek(0)
    return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=buyers.csv"})

@app.route("/export/bills.csv")
def export_bills_csv():
    import csv, io
    conn = get_db()
    rows = conn.execute("SELECT * FROM bills ORDER BY created_at DESC").fetchall()
    conn.close()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Bill Ref","Order Ref","Buyer Phone","Buyer Name","Item","Quantity","Unit Price","Subtotal","GST Rate","GST Amount","Total","Payment Status","Due Date","Paid Date","Created At"])
    for r in rows:
        writer.writerow([r["bill_ref"],r["order_ref"],r["buyer_phone"],r["buyer_name"],r["item"],r["quantity"],r["unit_price"],r["subtotal"],r["gst_rate"],r["gst_amount"],r["total_amount"],r["payment_status"],r["due_date"],r["paid_date"],r["created_at"]])
    output.seek(0)
    return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=bills.csv"})

@app.route("/export/calls.csv")
def export_calls_csv():
    import csv, io
    conn = get_db()
    rows = conn.execute("SELECT * FROM call_log ORDER BY timestamp DESC").fetchall()
    conn.close()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Caller","Transcript","Intent","Item","Quantity","Language","Timestamp"])
    for r in rows:
        writer.writerow([r["caller"],r["transcript"],r["intent"],r["item"],r["quantity"],r["language"],r["timestamp"]])
    output.seek(0)
    return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=calls.csv"})

@app.route("/export/all")
def export_all():
    conn = get_db()
    orders = [dict(r) for r in conn.execute("SELECT * FROM orders ORDER BY created_at DESC").fetchall()]
    inventory = [dict(r) for r in conn.execute("SELECT * FROM inventory").fetchall()]
    buyers = [dict(r) for r in conn.execute("SELECT * FROM buyer_credit ORDER BY outstanding_balance DESC").fetchall()]
    bills = [dict(r) for r in conn.execute("SELECT * FROM bills ORDER BY created_at DESC").fetchall()]
    calls = [dict(r) for r in conn.execute("SELECT * FROM call_log ORDER BY timestamp DESC LIMIT 100").fetchall()]
    reorders = [dict(r) for r in conn.execute("SELECT * FROM reorder_log ORDER BY timestamp DESC").fetchall()]
    followups = [dict(r) for r in conn.execute("SELECT * FROM followup_log ORDER BY timestamp DESC").fetchall()]
    conn.close()
    return jsonify({"success": True, "exported_at": datetime.now().isoformat(), "orders": orders, "inventory": inventory, "buyers": buyers, "bills": bills, "calls": calls, "reorders": reorders, "followups": followups})

# ── SUPPLIER CONFIRMATION CALLS ───────────────────────

def process_supplier_audio(audio_url):
    """Process supplier's voice response about delivery confirmation."""
    auth = (os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN"))
    audio_data = requests.get(audio_url, auth=auth).content

    prompt = """You are processing a supplier's voice response about a delivery confirmation.
Listen and return ONLY valid JSON:
{
  "confirmed": true or false,
  "new_date": "YYYY-MM-DD if they mention a new date, else null",
  "reason": "brief reason if they said no or gave new date, else null",
  "transcript": "exact words spoken"
}
Examples:
- "Haan bhai kal aa jayega" → confirmed: true
- "Yes will deliver tomorrow" → confirmed: true  
- "Nahi bhai, parso karunga" → confirmed: false, new_date: day after tomorrow
- "Sorry delay hoga, 3 din aur" → confirmed: false, reason: delay of 3 days"""

    import concurrent.futures
    from google.genai import types
    from datetime import date

    def _call_gemini():
        return client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[prompt, types.Part.from_bytes(data=audio_data, mime_type='audio/wav')]
        )

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_call_gemini)
            response = future.result(timeout=8.0)
        raw = response.text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw.strip())
    except Exception as e:
        print(f"[SUPPLIER AUDIO ERROR] {e}")
        return {"confirmed": None, "new_date": None, "reason": None, "transcript": ""}

@app.route("/supplier-confirm-call/<order_ref>", methods=["POST"])
def supplier_confirm_call(order_ref):
    """Twilio calls this when supplier picks up — plays confirmation request."""
    conn = get_db()
    if order_ref == "TEST-ORDER":
        order = {"item": "onions", "quantity": 500, "estimated_delivery": "tomorrow"}
    else:
        order = conn.execute("SELECT item, quantity, estimated_delivery FROM orders WHERE order_ref = ?", (order_ref,)).fetchone()
    conn.close()

    resp = VoiceResponse()
    if not order:
        resp.say("Sorry, order not found. Goodbye.", voice="alice", language="en-IN")
        return Response(str(resp), mimetype="text/xml")

    msg = (
        f"Hello! This is Logistey automated system calling about order {order_ref}. "
        f"We have a delivery of {order['quantity']} kilograms of {order['item']} "
        f"scheduled for {order['estimated_delivery']}. "
        f"Please confirm after the beep — say yes if delivery is on track, "
        f"or say no and mention the new delivery date if there is a delay."
    )
    resp.say(msg, voice="alice", language="en-IN")
    resp.record(
        max_length=20,
        action=f"/supplier-confirm-response/{order_ref}",
        transcribe=False,
        play_beep=True
    )
    return Response(str(resp), mimetype="text/xml")

@app.route("/supplier-confirm-response/<order_ref>", methods=["POST"])
def supplier_confirm_response(order_ref):
    """Handle supplier's recorded response."""
    recording_url = request.form.get("RecordingUrl")
    resp = VoiceResponse()

    if not recording_url:
        resp.say("Sorry, I didn't catch that. We will follow up again. Thank you.", voice="alice", language="en-IN")
        return Response(str(resp), mimetype="text/xml")

    result = process_supplier_audio(recording_url + ".wav")

    conn = get_db()
    if result.get("confirmed") == True:
        # Log confirmation
        conn.execute(
            "INSERT INTO followup_log(order_ref, buyer_phone, call_type, call_sid, status) VALUES(?,?,?,?,?)",
            (order_ref, "supplier", "supplier_confirmed", "voice", "confirmed")
        )
        conn.commit()
        conn.close()
        resp.say(
            "Thank you for confirming! We have noted the delivery is on track. Have a good day.",
            voice="alice", language="en-IN"
        )

    elif result.get("confirmed") == False:
        new_date = result.get("new_date")
        reason = result.get("reason") or "delay reported by supplier"

        if new_date:
            # Update estimated delivery in orders table
            conn.execute(
                "UPDATE orders SET estimated_delivery = ?, updated_at = CURRENT_TIMESTAMP WHERE order_ref = ?",
                (new_date, order_ref)
            )
            # Add delivery update entry
            conn.execute(
                "INSERT INTO delivery_updates(order_ref, status, note, updated_by) VALUES(?,?,?,?)",
                (order_ref, "pending", f"Supplier reported delay. New delivery date: {new_date}. Reason: {reason}", "supplier_call")
            )
            conn.commit()
            conn.close()
            resp.say(
                f"Thank you for letting us know. We have updated the delivery date to {new_date}. "
                f"Our team will inform the buyer. Goodbye.",
                voice="alice", language="en-IN"
            )
        else:
            conn.execute(
                "INSERT INTO followup_log(order_ref, buyer_phone, call_type, call_sid, status) VALUES(?,?,?,?,?)",
                (order_ref, "supplier", "supplier_delay_no_date", "voice", "needs_followup")
            )
            conn.commit()
            conn.close()
            resp.say(
                "Thank you. Could you please call us back with the new delivery date? We will follow up. Goodbye.",
                voice="alice", language="en-IN"
            )
    else:
        conn.close()
        resp.say("Sorry, I could not process your response. We will call again. Thank you.", voice="alice", language="en-IN")

    return Response(str(resp), mimetype="text/xml")

@app.route("/api/supplier-confirm/<order_ref>", methods=["POST"])
def api_trigger_supplier_confirm(order_ref):
    """Trigger an outbound confirmation call to the supplier for a given order."""
    conn = get_db()
    order = conn.execute(
        "SELECT o.item, o.quantity, o.estimated_delivery, i.supplier_phone, i.supplier_name "
        "FROM orders o LEFT JOIN inventory i ON o.item = i.item "
        "WHERE o.order_ref = ?", (order_ref,)
    ).fetchone()
    conn.close()

    if not order:
        return jsonify({"success": False, "error": "Order not found"}), 404
    if not order['supplier_phone']:
        return jsonify({"success": False, "error": "No supplier phone configured for this item"}), 400

    try:
        from twilio.rest import Client as TwilioClient
        twilio = TwilioClient(os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN"))
        base_url = os.getenv("BASE_URL", "http://127.0.0.1:5000")
        call = twilio.calls.create(
            url=f"{base_url}/supplier-confirm-call/{order_ref}",
            to=order['supplier_phone'],
            from_=os.getenv("TWILIO_PHONE_NUMBER")
        )
        return jsonify({"success": True, "call_sid": call.sid, "message": f"Confirmation call placed to supplier for order {order_ref}"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/test-supplier-confirm", methods=["POST"])
def test_supplier_confirm():
    """Test supplier confirmation call with any phone and order details."""
    data = request.get_json()
    phone = data.get("phone")
    order_ref = data.get("order_ref", "TEST-ORDER")
    if not phone:
        return jsonify({"success": False, "error": "phone required"}), 400
    try:
        from twilio.rest import Client as TwilioClient
        twilio = TwilioClient(os.getenv("TWILIO_ACCOUNT_SID"), os.getenv("TWILIO_AUTH_TOKEN"))
        base_url = os.getenv("BASE_URL", "http://127.0.0.1:5000")
        call = twilio.calls.create(
            url=f"{base_url}/supplier-confirm-call/{order_ref}",
            to=phone,
            from_=os.getenv("TWILIO_PHONE_NUMBER")
        )
        return jsonify({"success": True, "call_sid": call.sid})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# ── ENTRYPOINT ───────────────────────────────────────

if __name__ == "__main__":
    init_db()
    # Start background stock monitor thread
    monitor = threading.Thread(target=check_stock_levels, daemon=True)
    monitor.start()
    print("[REORDER] Stock monitor started — checking every 5 minutes.")
    # Start background follow-up call thread
    followup = threading.Thread(target=check_followups, daemon=True)
    followup.start()
    print("[FOLLOWUP] Follow-up call monitor started — checking every hour.")
    app.run(debug=True, port=5000, use_reloader=False)
