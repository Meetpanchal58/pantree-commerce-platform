"""Pantree storefront — FastAPI backend.
SQLite is the live OLTP source of truth. Every user activity is written to SQLite
and mirrored to DuckDB for analytics/model training.
"""
import sqlite3, uuid, json, datetime as dt, hashlib, hmac, secrets
from pathlib import Path
from fastapi import FastAPI, Request, Form, Cookie, Response
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
try:
    from .analytics_sync import (mirror_event, mirror_customer, mirror_order,
                                 mirror_transaction, mirror_inventory,
                                 mirror_inventory_movement, mirror_return)
except ImportError:
    from analytics_sync import (mirror_event, mirror_customer, mirror_order,
                                mirror_transaction, mirror_inventory,
                                mirror_inventory_movement, mirror_return)

BASE = Path(__file__).resolve().parent
DB = BASE / "pantree_app.db"
app = FastAPI(title="Pantree Storefront")
static_dir = BASE / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
tpl = Jinja2Templates(directory=str(BASE/"templates"))


def db():
    con = sqlite3.connect(DB, timeout=60)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=60000;")
    con.execute("PRAGMA foreign_keys=ON;")
    _ensure_customer_schema(con)
    return con


def now(): return dt.datetime.now().isoformat(timespec="seconds")
def uid(p): return f"{p}_{uuid.uuid4().hex[:12]}"


def _ensure_customer_schema(con):
    columns = {
        "first_name": "TEXT", "last_name": "TEXT", "phone": "TEXT",
        "home_address_line1": "TEXT", "home_address_line2": "TEXT",
        "home_postal_code": "TEXT", "home_country": "TEXT",
    }
    existing = {row[1] for row in con.execute("PRAGMA table_info(customers)")}
    for name, kind in columns.items():
        if name not in existing:
            con.execute(f"ALTER TABLE customers ADD COLUMN {name} {kind}")
    con.execute("ALTER TABLE transactions ADD COLUMN ship_address_line1 TEXT") if "ship_address_line1" not in {row[1] for row in con.execute("PRAGMA table_info(transactions)")} else None
    con.execute("ALTER TABLE transactions ADD COLUMN ship_address_line2 TEXT") if "ship_address_line2" not in {row[1] for row in con.execute("PRAGMA table_info(transactions)")} else None
    con.execute("ALTER TABLE transactions ADD COLUMN ship_postal_code TEXT") if "ship_postal_code" not in {row[1] for row in con.execute("PRAGMA table_info(transactions)")} else None
    con.execute("ALTER TABLE transactions ADD COLUMN ship_country TEXT") if "ship_country" not in {row[1] for row in con.execute("PRAGMA table_info(transactions)")} else None
    con.execute("ALTER TABLE transactions ADD COLUMN ship_phone TEXT") if "ship_phone" not in {row[1] for row in con.execute("PRAGMA table_info(transactions)")} else None

def hash_password(password: str) -> str:
    salt=secrets.token_bytes(16)
    digest=hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 210_000)
    return f"pbkdf2_sha256$210000${salt.hex()}${digest.hex()}"

def verify_password(password: str, stored: str) -> bool:
    if stored.startswith("pbkdf2_sha256$"):
        try:
            _,iters,salt_hex,digest_hex=stored.split("$",3)
            got=hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(iters))
            return hmac.compare_digest(got.hex(),digest_hex)
        except Exception:
            return False
    return hmac.compare_digest(password, stored)  # backward-compatible migration path for demo databases


def log_event(con, customer_id, session_id, device_type, event_type, page_type,
              product_id=None, search_query=None, traffic_source="direct"):
    ts = now()
    event_id = uid("evt")
    row = (event_id, session_id, customer_id, None, ts, event_type, page_type,
           product_id, search_query, device_type, traffic_source, None)
    con.execute("""INSERT INTO clickstream
        (event_id, session_id, customer_id, order_id, event_timestamp, event_type, page_type,
         product_id, search_query, device_type, traffic_source, campaign_id)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", row)
    mirror_event(dict(zip(["event_id","session_id","customer_id","order_id","event_timestamp","event_type","page_type","product_id","search_query","device_type","traffic_source","campaign_id"], row)))
    return event_id


def table_exists(con, name):
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def ensure_sessions(con):
    con.execute("""CREATE TABLE IF NOT EXISTS sessions (
        session_id TEXT PRIMARY KEY, customer_id TEXT, device_type TEXT, created_at TEXT)""")
    con.commit()


def ensure_wishlist(con):
    con.execute("""CREATE TABLE IF NOT EXISTS wishlist (
        customer_id TEXT, product_id TEXT, added_at TEXT,
        PRIMARY KEY (customer_id, product_id)
    )""")
    con.commit()


def get_session(con, sid):
    ensure_sessions(con)
    if not sid: return None
    return con.execute("SELECT * FROM sessions WHERE session_id=?", (sid,)).fetchone()


def cart_count(con, cid):
    r = con.execute("SELECT COALESCE(SUM(quantity),0) n FROM cart WHERE customer_id=?", (cid,)).fetchone()
    return r["n"] if r else 0


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, msg: str = ""):
    return tpl.TemplateResponse(request, "login.html", {"msg": msg})


@app.post("/signup")
def signup(response: Response, email: str = Form(...), password: str = Form(...),
           first_name: str = Form(...), last_name: str = Form(...), phone: str = Form(...),
           address_line1: str = Form(...), address_line2: str = Form(""),
           city: str = Form(...), state: str = Form(...), region: str = Form(...),
           postal_code: str = Form(...), country: str = Form(...),
           age_bracket: str = Form(...), gender: str = Form(...),
           email_opt_in: bool = Form(False), sms_opt_in: bool = Form(False),
           device: str = Form("Desktop Web")):
    con = db(); ensure_sessions(con)
    if con.execute("SELECT 1 FROM auth WHERE email=?", (email,)).fetchone():
        con.close(); return RedirectResponse("/login?msg=Email+already+registered", status_code=303)
    cid = uid("cust"); created = now()
    vals = (cid, created[:10], city, state, region, "Direct", None,
            age_bracket, gender, 0, None, "Non-Prime", int(email_opt_in), int(sms_opt_in), "active",
            first_name, last_name, phone, address_line1, address_line2, postal_code, country)
    con.execute("""INSERT INTO customers
        (customer_id, signup_date, signup_city, signup_state, signup_region,
         acquisition_channel, acquisition_campaign, age_bracket, gender,
         is_prime_member, prime_start_date, prime_tier, email_opt_in, sms_opt_in, account_status,
         first_name, last_name, phone, home_address_line1, home_address_line2, home_postal_code, home_country)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", vals)
    con.execute("INSERT INTO auth (customer_id,email,password) VALUES (?,?,?)", (cid,email,hash_password(password)))
    mirror_customer(dict(zip(["customer_id","signup_date","signup_city","signup_state","signup_region","acquisition_channel","acquisition_campaign","age_bracket","gender","is_prime_member","prime_start_date","prime_tier","email_opt_in","sms_opt_in","account_status","first_name","last_name","phone","home_address_line1","home_address_line2","home_postal_code","home_country"], vals)))
    sid = uid("sess")
    con.execute("INSERT INTO sessions VALUES (?,?,?,?)", (sid,cid,device,created))
    log_event(con,cid,sid,device,"app_open","home")
    con.commit(); con.close()
    resp = RedirectResponse("/", status_code=303); resp.set_cookie("sid",sid); return resp


@app.post("/login")
def login(email: str = Form(...), password: str = Form(...), device: str = Form("Desktop Web")):
    con = db(); ensure_sessions(con)
    row = con.execute("SELECT * FROM auth WHERE email=?", (email,)).fetchone()
    if not row or not verify_password(password, row["password"]):
        con.close(); return RedirectResponse("/login?msg=Invalid+credentials", status_code=303)
    cid=row["customer_id"]
    if not str(row["password"]).startswith("pbkdf2_sha256$"):
        con.execute("UPDATE auth SET password=? WHERE email=?", (hash_password(password),email))
    sid=uid("sess"); created=now()
    con.execute("INSERT INTO sessions VALUES (?,?,?,?)", (sid,cid,device,created))
    log_event(con,cid,sid,device,"app_open","home")
    con.commit(); con.close()
    resp=RedirectResponse("/",status_code=303); resp.set_cookie("sid",sid); return resp


@app.get("/logout")
def logout():
    resp=RedirectResponse("/login",status_code=303); resp.delete_cookie("sid"); return resp


@app.get("/account", response_class=HTMLResponse)
def account_page(request: Request, sid: str = Cookie(None)):
    con = db(); s = get_session(con, sid)
    if not s:
        con.close(); return RedirectResponse("/login", status_code=303)
    customer = con.execute("SELECT c.*, a.email FROM customers c JOIN auth a USING(customer_id) WHERE c.customer_id=?", (s["customer_id"],)).fetchone()
    orders = con.execute("""SELECT o.*, t.ship_address_line1, t.ship_address_line2,
        t.ship_city, t.ship_state, t.ship_region, t.ship_postal_code, t.ship_country
        FROM orders o LEFT JOIN transactions t ON t.order_id=o.order_id
        WHERE o.customer_id=? GROUP BY o.order_id ORDER BY o.order_timestamp DESC""", (s["customer_id"],)).fetchall()
    ctx = base_ctx(con, s, request, cats=_departments(con), title="Your account")
    ctx.update({"customer": customer, "orders": orders})
    con.close()
    return tpl.TemplateResponse(request, "account.html", ctx)


@app.get("/", response_class=HTMLResponse)
def home(request: Request, sid: str = Cookie(None)):
    con=db(); s=get_session(con,sid)
    if not s: con.close(); return RedirectResponse("/login",status_code=303)
    log_event(con,s["customer_id"],sid,s["device_type"],"page_view","home")
    ctx=base_ctx(con,s,request,prods=con.execute("""SELECT p.*, i.current_stock FROM products p JOIN inventory i USING(product_id)
        WHERE i.current_stock > 0 ORDER BY p.avg_rating DESC, p.review_count DESC LIMIT 24""").fetchall(),
        cats=_departments(con), title="Home")
    ctx.update({"departments": _departments(con), "subcategories": _subcategories(con), "home_stats": _catalog_stats(con)})
    con.commit(); con.close()
    return tpl.TemplateResponse(request,"home.html",ctx)


@app.get("/category/{cat}", response_class=HTMLResponse)
def plp(cat: str, request: Request, sid: str = Cookie(None)):
    return browse(request, sid, department=cat)


@app.get("/browse", response_class=HTMLResponse)
def browse(request: Request, sid: str = Cookie(None), department: str = "", subcategory: str = "",
           gender: str = "", brand: str = "", colour: str = "", usage: str = "",
        min_price: str = "", max_price: str = "", min_rating: str = "", stock: str = "", sort: str = "relevance",
           price_range: str = "", page: int = 1):
    con=db(); s=get_session(con,sid)
    if not s: con.close(); return RedirectResponse("/login",status_code=303)
    min_price_value=_parse_optional_float(min_price)
    max_price_value=_parse_optional_float(max_price)
    min_rating_value=_parse_optional_float(min_rating)
    range_min, range_max = _price_range_bounds(price_range)
    if range_min is not None: min_price_value=range_min
    if range_max is not None: max_price_value=range_max
    page=max(1,int(page)); limit=36; offset=(page-1)*limit
    where=["1=1"]; params=[]
    filters={"department":department,"subcategory":subcategory,"gender":gender,"brand":brand,"colour":colour,"usage":usage,
             "price_range":price_range,"min_price":min_price_value,"max_price":max_price_value,"min_rating":min_rating_value,"stock":stock,"sort":sort}
    for value,column in [(department,"h1_category"),(subcategory,"h2_category"),(gender,"gender"),(brand,"brand_name"),(colour,"h4_colour"),(usage,"usage")]:
        if value: where.append(f"p.{column}=?"); params.append(value)
    if min_price_value is not None: where.append("p.current_price>=?"); params.append(min_price_value)
    if max_price_value is not None: where.append("p.current_price<=?"); params.append(max_price_value)
    if min_rating_value is not None: where.append("COALESCE(p.avg_rating,0)>=?"); params.append(min_rating_value)
    if stock == "in": where.append("i.current_stock>0")
    order_by={"price_asc":"p.current_price ASC","price_desc":"p.current_price DESC","rating":"p.avg_rating DESC","newest":"p.launch_date DESC","relevance":"p.avg_rating DESC, p.review_count DESC"}.get(sort,"p.avg_rating DESC")
    base=" FROM products p JOIN inventory i USING(product_id) WHERE " + " AND ".join(where)
    total=con.execute("SELECT COUNT(*)"+base,params).fetchone()[0]
    prods=con.execute("SELECT p.*, i.current_stock"+base+f" ORDER BY {order_by} LIMIT ? OFFSET ?",params+[limit,offset]).fetchall()
    pages=max(1,(total+limit-1)//limit)
    title=next((x for x in [subcategory,department,gender,brand] if x),"Shop all")
    if not any(filters.values()): title="Shop all"
    log_event(con,s["customer_id"],sid,s["device_type"],"category_view","plp",search_query=title)
    ctx=base_ctx(con,s,request,prods=prods,cats=_departments(con),title=title,heading=title)
    ctx.update({"departments":_departments(con),"subcategories":_subcategories(con),"filter_options":_filter_options(con),
                "price_bounds":_catalog_price_bounds(con),"filters":filters,"result_count":total,"page":page,"pages":pages})
    con.commit(); con.close(); return tpl.TemplateResponse(request,"plp.html",ctx)


@app.get("/search", response_class=HTMLResponse)
def search(q: str, request: Request, sid: str = Cookie(None)):
    con=db(); s=get_session(con,sid)
    if not s: con.close(); return RedirectResponse("/login",status_code=303)
    log_event(con,s["customer_id"],sid,s["device_type"],"search","search_results",search_query=q)
    like=f"%{q}%"
    prods=con.execute("""SELECT p.*, i.current_stock FROM products p JOIN inventory i USING(product_id)
        WHERE p.product_name LIKE ? OR p.brand_name LIKE ? OR p.h3_category LIKE ? OR p.h4_colour LIKE ?
        ORDER BY p.avg_rating DESC, p.review_count DESC LIMIT 48""",(like,like,like,like)).fetchall()
    ctx=base_ctx(con,s,request,prods=prods,cats=_departments(con),title=f"Search: {q}",heading=f'Results for "{q}"')
    ctx.update({"departments":_departments(con),"subcategories":_subcategories(con),"filter_options":_filter_options(con),"price_bounds":_catalog_price_bounds(con),"filters":{"q":q},"result_count":len(prods),"page":1,"pages":1})
    con.commit(); con.close()
    return tpl.TemplateResponse(request,"plp.html",ctx)


@app.get("/product/{pid}", response_class=HTMLResponse)
def pdp(pid: str, request: Request, sid: str = Cookie(None)):
    con=db(); s=get_session(con,sid)
    if not s: con.close(); return RedirectResponse("/login",status_code=303)
    log_event(con,s["customer_id"],sid,s["device_type"],"pdp_view","pdp",product_id=pid)
    p=con.execute("""SELECT p.*, i.current_stock FROM products p JOIN inventory i USING(product_id)
        WHERE p.product_id=?""",(pid,)).fetchone()
    if not p: con.commit(); con.close(); return RedirectResponse("/",status_code=303)
    try: attrs=json.loads(p["product_attributes"]) if p["product_attributes"] else {}
    except Exception: attrs={}
    cats=[r["h1_category"] for r in con.execute("SELECT DISTINCT h1_category FROM products ORDER BY 1")]
    ctx=base_ctx(con,s,request,cats=cats,title=p["product_name"]); ctx.update({"p":p,"attrs":attrs}); con.commit(); con.close()
    return tpl.TemplateResponse(request,"pdp.html",ctx)


@app.post("/cart/add")
def cart_add(product_id: str=Form(...), qty: int=Form(1), sid: str=Cookie(None)):
    con=db(); s=get_session(con,sid)
    if not s: con.close(); return JSONResponse({"ok":False},status_code=401)
    cid=s["customer_id"]; qty=max(1,min(int(qty),20))
    stock=con.execute("SELECT current_stock FROM inventory WHERE product_id=?",(product_id,)).fetchone()
    if not stock or stock["current_stock"]<=0 or qty>stock["current_stock"]:
        con.close(); return JSONResponse({"ok":False,"message":"Not enough stock"},status_code=409)
    con.execute("""INSERT INTO cart (customer_id,product_id,quantity,added_at) VALUES (?,?,?,?)
        ON CONFLICT(customer_id,product_id) DO UPDATE SET quantity=MIN(quantity+excluded.quantity, ?)""",(cid,product_id,qty,now(),int(stock["current_stock"])))
    log_event(con,cid,sid,s["device_type"],"add_to_cart","pdp",product_id=product_id)
    con.commit(); n=cart_count(con,cid)
    quantity=con.execute("SELECT quantity FROM cart WHERE customer_id=? AND product_id=?",(cid,product_id)).fetchone()["quantity"]
    con.close(); return JSONResponse({"ok":True,"cart_count":n,"quantity":quantity})


@app.post("/wishlist/toggle")
def wishlist_toggle(product_id: str=Form(...), sid: str=Cookie(None)):
    con=db(); s=get_session(con,sid)
    if not s:
        con.close(); return JSONResponse({"ok":False,"message":"Authentication required"},status_code=401)
    ensure_wishlist(con)
    cid=s["customer_id"]
    existing=con.execute("SELECT 1 FROM wishlist WHERE customer_id=? AND product_id=?",(cid,product_id)).fetchone()
    if existing:
        con.execute("DELETE FROM wishlist WHERE customer_id=? AND product_id=?",(cid,product_id))
        event="wishlist_remove"; wished=False
    else:
        con.execute("INSERT INTO wishlist (customer_id,product_id,added_at) VALUES (?,?,?)",(cid,product_id,now()))
        event="wishlist_add"; wished=True
    log_event(con,cid,sid,s["device_type"],event,"pdp",product_id=product_id)
    con.commit(); con.close(); return JSONResponse({"ok":True,"wishlisted":wished})


@app.post("/cart/remove")
def cart_remove(product_id: str=Form(...), sid: str=Cookie(None)):
    con=db(); s=get_session(con,sid)
    if not s: con.close(); return RedirectResponse("/login",status_code=303)
    con.execute("DELETE FROM cart WHERE customer_id=? AND product_id=?",(s["customer_id"],product_id))
    log_event(con,s["customer_id"],sid,s["device_type"],"remove_from_cart","cart",product_id=product_id)
    con.commit(); con.close(); return RedirectResponse("/cart",status_code=303)


@app.post("/cart/update")
def cart_update(product_id: str=Form(...), delta: int=Form(...), sid: str=Cookie(None)):
    con=db(); s=get_session(con,sid)
    if not s:
        con.close(); return JSONResponse({"ok":False,"message":"Authentication required"},status_code=401)
    cid=s["customer_id"]
    item=con.execute("SELECT quantity FROM cart WHERE customer_id=? AND product_id=?",(cid,product_id)).fetchone()
    stock=con.execute("SELECT current_stock FROM inventory WHERE product_id=?",(product_id,)).fetchone()
    if not item or not stock:
        con.close(); return JSONResponse({"ok":False,"message":"Cart item not found"},status_code=404)
    quantity=int(item["quantity"])+int(delta)
    if quantity <= 0:
        con.execute("DELETE FROM cart WHERE customer_id=? AND product_id=?",(cid,product_id))
        quantity=0
    elif quantity > int(stock["current_stock"]):
        con.close(); return JSONResponse({"ok":False,"message":f"Only {stock['current_stock']} available","quantity":item["quantity"]},status_code=409)
    else:
        con.execute("UPDATE cart SET quantity=? WHERE customer_id=? AND product_id=?",(quantity,cid,product_id))
    log_event(con,cid,sid,s["device_type"],"cart_quantity_update","cart",product_id=product_id)
    con.commit(); count=cart_count(con,cid); con.close()
    return JSONResponse({"ok":True,"quantity":quantity,"cart_count":count})


@app.get("/cart", response_class=HTMLResponse)
def cart_page(request: Request, sid: str=Cookie(None)):
    con=db(); s=get_session(con,sid)
    if not s: con.close(); return RedirectResponse("/login",status_code=303)
    log_event(con,s["customer_id"],sid,s["device_type"],"page_view","cart")
    items=con.execute("""SELECT c.quantity,p.*,i.current_stock FROM cart c JOIN products p USING(product_id)
        JOIN inventory i USING(product_id) WHERE c.customer_id=?""",(s["customer_id"],)).fetchall()
    total=sum(r["current_price"]*r["quantity"] for r in items)
    cats=[r["h1_category"] for r in con.execute("SELECT DISTINCT h1_category FROM products ORDER BY 1")]
    ctx=base_ctx(con,s,request,cats=cats,title="Cart"); ctx.update({"items":items,"total":total}); con.commit(); con.close()
    return tpl.TemplateResponse(request,"cart.html",ctx)


@app.get("/checkout", response_class=HTMLResponse)
def checkout_page(request: Request, sid: str=Cookie(None), msg: str=""):
    con=db(); s=get_session(con,sid)
    if not s: con.close(); return RedirectResponse("/login",status_code=303)
    items=con.execute("""SELECT c.quantity,p.*,i.current_stock FROM cart c JOIN products p USING(product_id)
        JOIN inventory i USING(product_id) WHERE c.customer_id=?""",(s["customer_id"],)).fetchall()
    if not items: con.close(); return RedirectResponse("/cart",status_code=303)
    log_event(con,s["customer_id"],sid,s["device_type"],"checkout_start","checkout")
    total=sum(min(r["quantity"],max(r["current_stock"],0))*r["current_price"] for r in items)
    address=con.execute("SELECT * FROM customers WHERE customer_id=?", (s["customer_id"],)).fetchone()
    ctx=base_ctx(con,s,request,cats=_departments(con),title="Checkout"); ctx.update({"items":items,"total":total,"msg":msg,"address":address})
    con.commit(); con.close(); return tpl.TemplateResponse(request,"checkout.html",ctx)


@app.post("/checkout/pay")
def checkout(payment_method: str=Form(...), address_line1: str=Form(...), address_line2: str=Form(""),
             city: str=Form(...), state: str=Form(...), region: str=Form(...), postal_code: str=Form(...),
             country: str=Form(...), phone: str=Form(...), sid: str=Cookie(None)):
    con=db(); s=get_session(con,sid)
    if not s: con.close(); return RedirectResponse("/login",status_code=303)
    cid=s["customer_id"]; items=con.execute("""SELECT c.quantity,p.* FROM cart c JOIN products p USING(product_id)
        WHERE c.customer_id=?""",(cid,)).fetchall()
    if not items: con.close(); return RedirectResponse("/cart",status_code=303)
    ts=now(); oid=uid("ord"); total=0.0; tx_rows=[]; inv_rows=[]
    payment_methods={"UPI","Credit Card","Debit Card","Net Banking","COD","Amazon Pay"}
    if payment_method not in payment_methods:
        con.close(); return RedirectResponse("/checkout?msg=Select+a+valid+payment+method",status_code=303)
    for it in items:
        stock=con.execute("SELECT current_stock FROM inventory WHERE product_id=?",(it["product_id"],)).fetchone()
        buy=min(int(it["quantity"]),max(int(stock["current_stock"]),0)) if stock else 0
        if buy<=0: continue
        price=float(it["current_price"]); gross=round(price*buy,2); discount=round(max(float(it["mrp"])-price,0)*buy,2); net=round(gross,2)
        txid=uid("txn")
        vals=(txid,oid,cid,it["product_id"],it["seller_id"],ts,buy,float(it["mrp"]),price,price,float(it["unit_cost"]),discount,None,"BAU",payment_method,"Placed","Success",city,state,region,address_line1,address_line2,postal_code,country,phone,None,gross,net,round(net-float(it["unit_cost"])*buy,2),None)
        con.execute(f"""INSERT INTO transactions
            (transaction_id,order_id,customer_id,product_id,seller_id,order_timestamp,quantity,mrp,listed_price,
             unit_selling_price,unit_cost,discount_amount,coupon_code,campaign_type,payment_method,order_status,
               payment_status,ship_city,ship_state,ship_region,ship_address_line1,ship_address_line2,ship_postal_code,
               ship_country,ship_phone,delivered_timestamp,gross_item_value,net_item_value,gross_margin,
             replacement_for_transaction_id) VALUES ({','.join('?' for _ in vals)})""",vals)
        newstock=int(stock["current_stock"])-buy
        con.execute("UPDATE inventory SET current_stock=? WHERE product_id=?",(newstock,it["product_id"]))
        tx_rows.append(dict(zip(["transaction_id","order_id","customer_id","product_id","seller_id","order_timestamp","quantity","mrp","listed_price","unit_selling_price","unit_cost","discount_amount","coupon_code","campaign_type","payment_method","order_status","payment_status","ship_city","ship_state","ship_region","ship_address_line1","ship_address_line2","ship_postal_code","ship_country","ship_phone","delivered_timestamp","gross_item_value","net_item_value","gross_margin","replacement_for_transaction_id"],vals)))
        inv_rows.append({"product_id":it["product_id"],"current_stock":newstock,"base_stock":None})
        mirror_transaction(tx_rows[-1]); mirror_inventory({"product_id":it["product_id"],"current_stock":newstock,"base_stock":None})
        movement={"movement_id":uid("mov"),"movement_timestamp":ts,"product_id":it["product_id"],"quantity_delta":-buy,"movement_type":"SALE","order_id":oid,"transaction_id":txid,"reason":"website checkout"}
        con.execute("INSERT INTO inventory_movements VALUES (?,?,?,?,?,?,?,?)",tuple(movement.values())); mirror_inventory_movement(movement)
        log_event(con,cid,sid,s["device_type"],"payment_attempt","payment",product_id=it["product_id"])
        log_event(con,cid,sid,s["device_type"],"purchase","confirmation",product_id=it["product_id"])
        total+=net
    if not tx_rows:
        con.close(); return RedirectResponse("/cart?msg=Out+of+stock",status_code=303)
    order_vals=(oid,cid,ts,"Placed","Success",round(total,2),ts,None,ts)
    con.execute("INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?)",order_vals)
    mirror_order(dict(zip(["order_id","customer_id","order_timestamp","order_status","payment_status","total_amount","created_at","delivered_timestamp","updated_at"],order_vals)))
    con.execute("DELETE FROM cart WHERE customer_id=?",(cid,))
    con.commit(); con.close(); return RedirectResponse("/orders",status_code=303)


@app.get("/orders", response_class=HTMLResponse)
def orders_page(request: Request, sid: str=Cookie(None)):
    con=db(); s=get_session(con,sid)
    if not s: con.close(); return RedirectResponse("/login",status_code=303)
    orders=con.execute("SELECT * FROM orders WHERE customer_id=? ORDER BY order_timestamp DESC",(s["customer_id"],)).fetchall()
    lines=con.execute("""SELECT t.*,p.product_name,p.image_url,p.h3_category FROM transactions t JOIN products p USING(product_id)
        WHERE t.customer_id=? ORDER BY t.order_timestamp DESC""",(s["customer_id"],)).fetchall()
    grouped={}
    for row in lines: grouped.setdefault(row["order_id"],[]).append(row)
    cats=[r["h1_category"] for r in con.execute("SELECT DISTINCT h1_category FROM products ORDER BY 1")]
    ctx=base_ctx(con,s,request,cats=cats,title="Your Orders"); ctx.update({"orders":orders,"lines":grouped}); con.commit(); con.close()
    return tpl.TemplateResponse(request,"orders.html",ctx)


@app.post("/orders/{order_id}/status")
def update_order_status(order_id: str, action: str=Form(...), sid: str=Cookie(None)):
    con=db(); s=get_session(con,sid)
    if not s: con.close(); return RedirectResponse("/login",status_code=303)
    order=con.execute("SELECT * FROM orders WHERE order_id=? AND customer_id=?",(order_id,s["customer_id"])).fetchone()
    if not order: con.close(); return RedirectResponse("/orders",status_code=303)
    allowed={"deliver":"Delivered","cancel":"Cancelled","return":"Return Requested","replace":"Replacement Requested"}
    if action not in allowed: con.close(); return RedirectResponse("/orders",status_code=303)
    lines=con.execute("SELECT * FROM transactions WHERE order_id=? AND customer_id=?",(order_id,s["customer_id"])).fetchall()
    ts=now()
    if action=="deliver":
        if order["order_status"] != "Placed":
            con.close(); return RedirectResponse("/orders",status_code=303)
        for x in lines:
            if x["order_status"] != "Placed": continue
            con.execute("UPDATE transactions SET order_status='Delivered', delivered_timestamp=? WHERE transaction_id=?",(ts,x["transaction_id"]))
            mirror_transaction(dict(con.execute("SELECT * FROM transactions WHERE transaction_id=?",(x["transaction_id"],)).fetchone()))
            log_event(con,s["customer_id"],sid,s["device_type"],"order_delivered","orders",x["product_id"])
        con.execute("UPDATE orders SET order_status='Delivered',delivered_timestamp=?,updated_at=? WHERE order_id=?",(ts,ts,order_id))
        mirror_order(dict(con.execute("SELECT * FROM orders WHERE order_id=?",(order_id,)).fetchone()))
    elif action=="cancel":
        if order["order_status"] != "Placed":
            con.close(); return RedirectResponse("/orders",status_code=303)
        changed=0
        for x in lines:
            if x["order_status"] != "Placed": continue
            con.execute("UPDATE transactions SET order_status='Cancelled', payment_status='Refunded', net_item_value=0, gross_margin=0 WHERE transaction_id=?",(x["transaction_id"],))
            mirror_transaction(dict(con.execute("SELECT * FROM transactions WHERE transaction_id=?",(x["transaction_id"],)).fetchone()))
            stock=con.execute("SELECT current_stock FROM inventory WHERE product_id=?",(x["product_id"],)).fetchone(); newstock=int(stock["current_stock"])+int(x["quantity"])
            con.execute("UPDATE inventory SET current_stock=? WHERE product_id=?",(newstock,x["product_id"]))
            mirror_inventory({"product_id":x["product_id"],"current_stock":newstock,"base_stock":None})
            movement={"movement_id":uid("mov"),"movement_timestamp":ts,"product_id":x["product_id"],"quantity_delta":int(x["quantity"]),"movement_type":"CANCELLATION","order_id":order_id,"transaction_id":x["transaction_id"],"reason":"customer order cancelled"}
            con.execute("INSERT INTO inventory_movements VALUES (?,?,?,?,?,?,?,?)",tuple(movement.values())); mirror_inventory_movement(movement)
            log_event(con,s["customer_id"],sid,s["device_type"],"order_cancelled","orders",x["product_id"]); changed+=1
        if changed:
            con.execute("UPDATE orders SET order_status='Cancelled',payment_status='Refunded',updated_at=? WHERE order_id=?",(ts,order_id))
            mirror_order(dict(con.execute("SELECT * FROM orders WHERE order_id=?",(order_id,)).fetchone()))
    elif action=="return":
        if order["order_status"] not in {"Delivered","Partially Returned"}:
            con.close(); return RedirectResponse("/orders",status_code=303)
        changed=0
        for x in lines:
            if x["order_status"] not in {"Delivered","Partially Returned"}: continue
            con.execute("UPDATE transactions SET order_status='Returned' WHERE transaction_id=?",(x["transaction_id"],))
            mirror_transaction(dict(con.execute("SELECT * FROM transactions WHERE transaction_id=?",(x["transaction_id"],)).fetchone()))
            rid=uid("ret"); rv=(rid,x["transaction_id"],order_id,x["customer_id"],x["product_id"],x["seller_id"],ts,ts,"Customer Return","refund",x["net_item_value"],x["quantity"],"completed")
            con.execute("INSERT INTO returns VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",rv); mirror_return(dict(zip(["return_id","transaction_id","order_id","customer_id","product_id","seller_id","return_requested_at","return_received_at","return_reason","return_type","refund_amount","returned_quantity","return_status"],rv)))
            stock=con.execute("SELECT current_stock FROM inventory WHERE product_id=?",(x["product_id"],)).fetchone(); newstock=int(stock["current_stock"])+int(x["quantity"])
            con.execute("UPDATE inventory SET current_stock=? WHERE product_id=?",(newstock,x["product_id"])); mirror_inventory({"product_id":x["product_id"],"current_stock":newstock,"base_stock":None})
            movement={"movement_id":uid("mov"),"movement_timestamp":ts,"product_id":x["product_id"],"quantity_delta":int(x["quantity"]),"movement_type":"RETURN","order_id":order_id,"transaction_id":x["transaction_id"],"reason":"customer return accepted"}
            con.execute("INSERT INTO inventory_movements VALUES (?,?,?,?,?,?,?,?)",tuple(movement.values())); mirror_inventory_movement(movement)
            log_event(con,s["customer_id"],sid,s["device_type"],"return_completed","orders",x["product_id"]); changed+=1
        if changed:
            status="Returned"
            con.execute("UPDATE orders SET order_status=?,updated_at=? WHERE order_id=?",(status,ts,order_id)); mirror_order(dict(con.execute("SELECT * FROM orders WHERE order_id=?",(order_id,)).fetchone()))
    else:
        if order["order_status"] not in {"Delivered","Partially Returned"}:
            con.close(); return RedirectResponse("/orders",status_code=303)
        # Same-SKU replacement: return the old unit into stock, then consume a replacement unit.
        changed=0
        for x in lines:
            if x["order_status"]!="Delivered": continue
            stock=con.execute("SELECT current_stock FROM inventory WHERE product_id=?",(x["product_id"],)).fetchone(); available=int(stock["current_stock"])
            if available < int(x["quantity"]): continue
            con.execute("UPDATE transactions SET order_status='Replaced' WHERE transaction_id=?",(x["transaction_id"],))
            mirror_transaction(dict(con.execute("SELECT * FROM transactions WHERE transaction_id=?",(x["transaction_id"],)).fetchone()))
            # Return old unit first, then issue replacement: net inventory is unchanged.
            returned=available+int(x["quantity"])
            con.execute("UPDATE inventory SET current_stock=? WHERE product_id=?",(returned,x["product_id"]))
            mirror_inventory({"product_id":x["product_id"],"current_stock":returned,"base_stock":None})
            m1={"movement_id":uid("mov"),"movement_timestamp":ts,"product_id":x["product_id"],"quantity_delta":int(x["quantity"]),"movement_type":"REPLACEMENT_RETURN","order_id":order_id,"transaction_id":x["transaction_id"],"reason":"old unit returned for replacement"}
            con.execute("INSERT INTO inventory_movements VALUES (?,?,?,?,?,?,?,?)",tuple(m1.values())); mirror_inventory_movement(m1)
            newstock=returned-int(x["quantity"]); con.execute("UPDATE inventory SET current_stock=? WHERE product_id=?",(newstock,x["product_id"]))
            mirror_inventory({"product_id":x["product_id"],"current_stock":newstock,"base_stock":None})
            m2={"movement_id":uid("mov"),"movement_timestamp":ts,"product_id":x["product_id"],"quantity_delta":-int(x["quantity"]),"movement_type":"REPLACEMENT_ISSUE","order_id":order_id,"transaction_id":x["transaction_id"],"reason":"replacement unit issued"}
            con.execute("INSERT INTO inventory_movements VALUES (?,?,?,?,?,?,?,?)",tuple(m2.values())); mirror_inventory_movement(m2)
            log_event(con,s["customer_id"],sid,s["device_type"],"replacement_completed","orders",x["product_id"]); changed+=1
        if changed:
            con.execute("UPDATE orders SET order_status='Replaced',updated_at=? WHERE order_id=?",(ts,order_id)); mirror_order(dict(con.execute("SELECT * FROM orders WHERE order_id=?",(order_id,)).fetchone()))
    con.commit(); con.close(); return RedirectResponse("/orders",status_code=303)


@app.post("/inventory/refresh")
def refresh_inventory(sid: str=Cookie(None)):
    con=db(); s=get_session(con,sid)
    if not s:
        con.close()
        return JSONResponse({"ok":False,"message":"Authentication required"},status_code=401)
    rows=con.execute("SELECT product_id,base_stock FROM inventory").fetchall(); ts=now()
    for r in rows:
        con.execute("UPDATE inventory SET current_stock=? WHERE product_id=?",(r["base_stock"],r["product_id"]))
        mirror_inventory({"product_id":r["product_id"],"current_stock":r["base_stock"],"base_stock":r["base_stock"]})
    con.commit(); con.close(); return JSONResponse({"ok":True,"message":"Inventory restocked to base levels"})


def _departments(con):
    return [r["h1_category"] for r in con.execute("SELECT DISTINCT h1_category FROM products ORDER BY 1")]


def _subcategories(con):
    return [r["h2_category"] for r in con.execute("SELECT DISTINCT h2_category FROM products ORDER BY 1")]


def _filter_options(con):
    def values(column, limit=None):
        sql=f"SELECT {column} value, COUNT(*) n FROM products WHERE {column} IS NOT NULL AND TRIM({column})<>'' GROUP BY {column} ORDER BY n DESC, value"
        if limit: sql += f" LIMIT {int(limit)}"
        return [r["value"] for r in con.execute(sql)]
    return {"genders":values("gender"),"brands":values("brand_name",40),"colours":values("h4_colour"),"usages":values("usage")}


def _catalog_stats(con):
    row=con.execute("SELECT COUNT(*) products, COUNT(DISTINCT brand_name) brands, COUNT(DISTINCT h1_category) departments FROM products").fetchone()
    return dict(row)


def _catalog_price_bounds(con):
    row=con.execute("SELECT FLOOR(MIN(current_price)) minimum, CEIL(MAX(current_price)) maximum FROM products").fetchone()
    return {"minimum":int(row["minimum"] or 0),"maximum":int(row["maximum"] or 25000)}


def _parse_optional_float(value):
    if value is None or not str(value).strip():
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _price_range_bounds(value):
    ranges={"under-1000":(None,1000),"1000-2500":(1000,2500),"2500-5000":(2500,5000),
            "5000-10000":(5000,10000),"10000-plus":(10000,None)}
    return ranges.get(str(value).strip(),(None,None))


def base_ctx(con,s,request,prods=None,cats=None,title="Pantree",heading=None):
    customer = con.execute("SELECT first_name FROM customers WHERE customer_id=?", (s["customer_id"],)).fetchone()
    cart_quantities = {row["product_id"]: row["quantity"] for row in con.execute("SELECT product_id,quantity FROM cart WHERE customer_id=?", (s["customer_id"],))}
    return {"title":title,"heading":heading,"prods":prods or [],"cats":cats or [],"cart_n":cart_count(con,s["customer_id"]),"customer_id":s["customer_id"],"first_name":(customer["first_name"] if customer and customer["first_name"] else "Customer"),"cart_quantities":cart_quantities}
