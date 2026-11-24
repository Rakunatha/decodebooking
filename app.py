"""Decode Detailing Studio - booking site (/) + dashboard (/dashboard). One file.
Run:  pip install flask && python app.py   (env: DASHBOARD_PASSWORD, SECRET_KEY)
"""
import os, sqlite3
from datetime import date
from functools import wraps
from flask import Flask, request, jsonify, session, g

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-change-me")
ADMIN_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "decode123")
DB = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "decode.db"))
if not os.path.isdir(os.path.dirname(DB)):  # e.g. DB_PATH set but no disk attached: fall back to a local file
    DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "decode.db")
BAYS = 2  # cars per hourly slot
SLOTS = [f"{h:02d}:00" for h in range(9, 18)]
STATUSES = ["pending", "confirmed", "in progress", "completed", "cancelled"]
VEHICLES = {"hatch": "Hatchback", "sedan": "Sedan", "suv": "Compact SUV", "muv": "MUV / High-end"}


def P(h, s, m, c=None):  # compact SUV falls back to sedan price unless the catalogue lists it
    return {"hatch": h, "sedan": s, "suv": c if c else s, "muv": m}


# ---- Catalogue (from the uploaded PDFs). Edit prices here. ----
SERVICES = [
    {"id": "wash", "cat": "Wash", "name": "Decode Wash", "gst": False, "price": P(599, 699, 899, 799),
     "info": "Foam wash, interior vacuum, tyre dressing, spot-free drying, treated water."},
    {"id": "dtail-i", "cat": "Wash", "name": "D-Tail Interior", "gst": False, "price": P(1999, 2499, 2999),
     "info": "Seat deep cleaning, leather conditioning, roof lining, A/C vents, glass."},
    {"id": "dtail", "cat": "Wash", "name": "Decode D-Tail", "gst": False, "price": P(2499, 2999, 3599),
     "info": "Everything in Wash and D-Tail, plus iron/tar removal, underchassis, engine bay."},
    {"id": "nexus", "cat": "Correction", "name": "Decode Nexus", "gst": False, "price": P(6499, 7499, 8499),
     "info": "Clay bar, one-step DA compound polish, quick gloss coat for 6-12 months."},
    {"id": "ayicon", "cat": "Coating", "name": "Ayicon V4 Ceramic, 2 years", "gst": True, "price": P(25000, 30000, 35000),
     "info": "Silica-rich ceramic with deep gloss. Safe on paint, PPF, matte and vinyl."},
    {"id": "hybrid", "cat": "Coating", "name": "Hybrid+ Graphene, 3 years", "gst": True, "price": P(28000, 33000, 38000),
     "info": "Greater resilience, improved slickness and water repellency."},
    {"id": "ayp", "cat": "Coating", "name": "Ayicon Pro Ceramic, 5 years", "gst": True, "price": P(30000, 35000, 40000),
     "info": "Superior resistance to harsh conditions, built for studio application."},
    {"id": "ngc", "cat": "Coating", "name": "Nano Graphene, 7 years", "gst": True, "price": P(35000, 40000, 45000),
     "info": "Graphene oxide + CNT. Our hardest coating, fast curing, maximum gloss."},
    {"id": "wheel", "cat": "Coating", "name": "Wheel+ Graphene, 2 years", "gst": True, "price": P(3000, 3500, 4000),
     "info": "Heat-resistant coating that repels brake dust and road grime."},
    {"id": "window", "cat": "Coating", "name": "Window+ Graphene, 2 years", "gst": True, "price": P(5000, 6000, 7000),
     "info": "Rain-repellent glass coating that resists water marks."},
    {"id": "pass6", "cat": "Pass", "name": "Annual Pass, 6 months", "gst": True, "price": P(5999, 6499, 8999),
     "info": "8 Decode Washes and 1 D-Tail Interior."},
    {"id": "pass12", "cat": "Pass", "name": "Annual Pass, 1 year", "gst": True, "price": P(13999, 15999, 19999),
     "info": "15 Decode Washes, 2 D-Tail Interiors, 1 three-step paint correction."},
]
ADDONS = [
    ("under", "Underchassis cleaning", 100, 150, 200), ("head", "Headlight restoration", 300, 500, 700),
    ("dash", "Dashboard and trims dressing", 50, 100, 150), ("iron", "Iron removal", 100, 100, 150),
    ("tar", "Tar removal", 100, 100, 150), ("stain", "Stain removal", 150, 150, 150),
    ("engine", "Engine bay cleaning", 300, 350, 400), ("odour", "Odour elimination", 1000, 1000, 1500),
    ("wind", "Windshield polishing", 1500, 1500, 2000), ("wax", "Spray wax", 300, 400, 500),
    ("idress", "Interior dressing", 200, 250, 300), ("paint", "External paint removal", 500, 500, 500),
    ("water", "Waterspot removal", 300, 300, 300), ("sticker", "Sticker removal (per sticker)", 100, 100, 100),
]
ADDON_MAP = {a[0]: {"id": a[0], "name": a[1], "price": P(a[2], a[3], a[4])} for a in ADDONS}
SVC_MAP = {s["id"]: s for s in SERVICES}


# ---- Database ----
def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_):
    d = g.pop("db", None)
    if d:
        d.close()


def init_db():
    with sqlite3.connect(DB) as c:
        c.execute("""CREATE TABLE IF NOT EXISTS bookings(
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, phone TEXT, email TEXT, vehicle_model TEXT,
            vehicle_type TEXT, service_id TEXT, service_name TEXT, addons TEXT, date TEXT, time TEXT,
            notes TEXT, total INTEGER, gst INTEGER, status TEXT DEFAULT 'pending',
            created TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")


def admin_only(f):
    @wraps(f)
    def w(*a, **k):
        if not session.get("admin"):
            return jsonify(error="Login required"), 401
        return f(*a, **k)
    return w


def taken(day):
    rows = db().execute("SELECT time, COUNT(*) n FROM bookings WHERE date=? AND status!='cancelled' GROUP BY time", (day,))
    return {r["time"]: r["n"] for r in rows}


# ---- Public API ----
@app.get("/api/catalog")
def catalog():
    return jsonify(services=SERVICES, addons=list(ADDON_MAP.values()), vehicles=VEHICLES, today=date.today().isoformat())


@app.get("/api/slots")
def slots():
    t = taken(request.args.get("date", ""))
    return jsonify(slots=[{"time": s, "open": t.get(s, 0) < BAYS} for s in SLOTS])


@app.post("/api/bookings")
def create_booking():
    d = request.get_json(force=True, silent=True) or {}
    need = ["name", "phone", "vehicle_model", "vehicle_type", "service_id", "date", "time"]
    if any(not str(d.get(k, "")).strip() for k in need):
        return jsonify(error="Please fill in all required fields."), 400
    svc = SVC_MAP.get(d["service_id"])
    if not svc or d["vehicle_type"] not in VEHICLES or d["time"] not in SLOTS:
        return jsonify(error="Invalid selection."), 400
    if d["date"] < date.today().isoformat():
        return jsonify(error="Choose today or a later date."), 400
    if taken(d["date"]).get(d["time"], 0) >= BAYS:
        return jsonify(error="That slot was just taken. Pick another time."), 409
    vt = d["vehicle_type"]
    adds = [ADDON_MAP[a] for a in d.get("addons", []) if a in ADDON_MAP]
    total = svc["price"][vt] + sum(a["price"][vt] for a in adds)
    cur = db().execute(
        "INSERT INTO bookings(name,phone,email,vehicle_model,vehicle_type,service_id,service_name,addons,date,time,notes,total,gst)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (d["name"].strip(), d["phone"].strip(), d.get("email", "").strip(), d["vehicle_model"].strip(), vt, svc["id"],
         svc["name"], ", ".join(a["name"] for a in adds), d["date"], d["time"], d.get("notes", "").strip(), total,
         int(svc["gst"])))
    db().commit()
    return jsonify(ref=f"DD-{cur.lastrowid:04d}", total=total, gst=svc["gst"]), 201


# ---- Admin API ----
@app.post("/api/admin/login")
def login():
    if (request.get_json(silent=True) or {}).get("password") == ADMIN_PASSWORD:
        session["admin"] = True
        return jsonify(ok=True)
    return jsonify(error="Wrong password."), 401


@app.post("/api/admin/logout")
def logout():
    session.clear()
    return jsonify(ok=True)


@app.get("/api/admin/bookings")
@admin_only
def list_bookings():
    q, args = "SELECT * FROM bookings WHERE 1=1", []
    for col in ("date", "status"):
        if request.args.get(col):
            q += f" AND {col}=?"
            args.append(request.args[col])
    rows = db().execute(q + " ORDER BY date, time", args).fetchall()
    return jsonify([dict(r, ref=f"DD-{r['id']:04d}") for r in rows])


@app.patch("/api/admin/bookings/<int:bid>")
@admin_only
def update_booking(bid):
    s = (request.get_json(silent=True) or {}).get("status")
    if s not in STATUSES:
        return jsonify(error="Invalid status"), 400
    db().execute("UPDATE bookings SET status=? WHERE id=?", (s, bid))
    db().commit()
    return jsonify(ok=True)


@app.get("/api/admin/stats")
@admin_only
def stats():
    t = date.today().isoformat()
    one = lambda q, a=(): db().execute(q, a).fetchone()[0] or 0
    return jsonify(
        today=one("SELECT COUNT(*) FROM bookings WHERE date=? AND status!='cancelled'", (t,)),
        pending=one("SELECT COUNT(*) FROM bookings WHERE status='pending'"),
        upcoming=one("SELECT COUNT(*) FROM bookings WHERE date>=? AND status IN('pending','confirmed')", (t,)),
        revenue=one("SELECT SUM(total) FROM bookings WHERE status='completed'"))


# ---- Pages ----
CSS = r"""
:root{--red:#C8202F;--ink:#1B1F27;--slate:#6B727C;--paper:#FAFBFB;--tint:#F0F1F3;--white:#fff;
--s1:8px;--s2:16px;--s3:24px;--s4:40px;--s5:64px;--s6:104px;--r:14px;--ease:cubic-bezier(.2,.7,.2,1)}
*{box-sizing:border-box;margin:0}html{scroll-behavior:smooth}
body{font:400 16px/1.6 'Manrope',sans-serif;color:var(--ink);background:var(--paper);-webkit-font-smoothing:antialiased}
h1,h2,h3,.disp{font-family:'Oswald',sans-serif;font-weight:600;text-transform:uppercase;line-height:1.02;letter-spacing:.01em}
h1{font-size:clamp(3rem,10vw,8rem)}h2{font-size:clamp(2rem,4.5vw,3.5rem)}h3{font-size:1.35rem}
p.lead{font-size:1.15rem;color:var(--slate);max-width:46ch}.muted{color:var(--slate);font-size:.92rem}
.wrap{max-width:1040px;margin:0 auto;padding:0 var(--s3)}section{padding:var(--s6) 0}
button,input,textarea,select{font:inherit;color:inherit}
.btn{border:0;cursor:pointer;background:var(--red);color:#fff;font-weight:600;padding:14px 28px;border-radius:999px;
transition:transform .25s var(--ease),box-shadow .25s var(--ease),background .2s}
@media(hover:hover){.btn:hover{transform:translateY(-2px);box-shadow:0 10px 24px -10px var(--red)}}.btn:active{transform:scale(.97)}
.btn.ghost{background:var(--tint);color:var(--ink)}.btn.ghost:hover{box-shadow:none;background:#e5e7ea}
.btn:disabled{opacity:.4;pointer-events:none}
:focus-visible{outline:2px solid var(--red);outline-offset:3px}
.chip{border:0;cursor:pointer;background:var(--tint);padding:10px 18px;border-radius:999px;font-weight:500;
transition:background .2s,color .2s,transform .2s var(--ease)}
@media(hover:hover){.chip:hover{transform:translateY(-1px)}}.chip.on{background:var(--ink);color:#fff}
.field{width:100%;border:0;background:var(--tint);padding:16px 18px;border-radius:var(--r);transition:background .2s,box-shadow .2s}
.field:focus{outline:0;background:#fff;box-shadow:0 0 0 2px var(--red)}
.row{display:flex;flex-wrap:wrap;gap:var(--s1)}.grid{display:grid;gap:var(--s2);grid-template-columns:repeat(auto-fit,minmax(240px,1fr))}
.toast{position:fixed;left:50%;bottom:var(--s3);transform:translate(-50%,120px);background:var(--ink);color:#fff;
padding:14px 24px;border-radius:999px;transition:transform .4s var(--ease);z-index:20}.toast.show{transform:translate(-50%,0)}
html{-webkit-text-size-adjust:100%;color-scheme:light}body{-webkit-tap-highlight-color:transparent}.chip,.btn{min-height:44px}body{overflow-x:hidden}img,svg,input,select,textarea{max-width:100%}
input[type=date]{min-width:0;-webkit-appearance:none;appearance:none;min-height:56px}
.toast{max-width:calc(100vw - 32px);text-align:center;border-radius:20px}
@media(max-width:640px){
 :root{--s3:20px;--s4:32px;--s5:48px;--s6:64px}
 .wrap{padding:0 20px}h2{font-size:clamp(1.8rem,8.5vw,2.4rem)}
 p.lead{font-size:1.02rem}.btn{padding:14px 22px}.chip{padding:10px 14px;font-size:.92rem}
 .field{padding:15px 16px}}
@media(prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
"""

HEAD = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="theme-color" content="#FAFBFB">
<title>%s</title><link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Manrope:wght@400;500;600&family=Oswald:wght@600&display=swap" rel="stylesheet">
<style>/*CSS*/</style>"""

BOOKING = HEAD % "Book a detail | Decode Detailing Studio" + r"""
<style>
.hero{min-height:92vh;display:flex;flex-direction:column;justify-content:center;gap:var(--s3);padding:var(--s5) 0}
.hero h1 span{display:block;animation:rise 1s var(--ease) both}.hero h1 span:nth-child(2){color:var(--red);animation-delay:.12s}
.hero>*:not(h1){animation:rise 1s .3s var(--ease) both}@keyframes rise{from{opacity:0;transform:translateY(28px)}}
nav{display:flex;justify-content:space-between;align-items:center;padding:var(--s3) 0}nav b{font:600 1.3rem 'Oswald';letter-spacing:.04em}
.svc{display:grid;grid-template-columns:1fr auto;gap:var(--s1) var(--s3);padding:var(--s3);border-radius:var(--r);background:var(--white);
text-align:left;border:0;cursor:pointer;width:100%;transition:transform .3s var(--ease),background .25s,color .25s}
@media(hover:hover){.svc:hover{transform:translateX(6px)}}.svc .p{font:600 1.5rem 'Oswald';grid-row:span 2;align-self:center}
.svc.on{background:var(--ink);color:#fff}.svc.on .muted{color:#b8bec7}.svc.on .p{color:#fff}
.stack{display:grid;gap:var(--s1);margin-top:var(--s3)}.step{max-width:720px}.step>h2{margin-bottom:var(--s3)}
.slot{min-width:84px}.slot:disabled{opacity:.3;text-decoration:line-through;pointer-events:none}
.bar{position:fixed;left:0;right:0;bottom:0;background:var(--ink);color:#fff;transform:translateY(100%);transition:transform .5s var(--ease);z-index:10}
.bar.show{transform:none}.bar .wrap{display:flex;justify-content:space-between;align-items:center;padding:var(--s2) var(--s3);gap:var(--s2)}
.bar .t{font:600 1.8rem 'Oswald'}.done{text-align:center;padding:var(--s6) 0;display:none}.done h2{margin:var(--s2) 0}
.tint{background:var(--tint)}.tint .svc{background:var(--paper)}.tint .svc.on{background:var(--ink)}
.hero h1{font-size:clamp(2.3rem,10vw,8rem)}
@media(max-width:640px){
 nav{gap:var(--s2)}nav b{font-size:1rem;line-height:1.15;min-width:0}nav .btn{padding:10px 16px;font-size:.9rem;white-space:nowrap}
 .hero{min-height:calc(100vh - 80px);min-height:calc(100svh - 80px);padding:var(--s4) 0;gap:var(--s2)}
 .svc{grid-template-columns:1fr;padding:20px}.svc .p{grid-row:auto;order:2}.svc:hover{transform:none}
 .slot{min-width:0;flex:1 0 calc(33.333% - 8px);text-align:center}
 .bar .wrap{padding:12px 20px calc(12px + env(safe-area-inset-bottom));gap:12px}.bar .t{font-size:1.4rem}
 #sum{font-size:.8rem;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
 #jump{padding:12px 18px;white-space:nowrap}#submit{width:100%}
 footer.wrap{padding-bottom:150px!important;font-size:.85rem}.done h1{font-size:clamp(2.2rem,12vw,4rem)}}
</style></head><body>
<div class="wrap"><nav><b>DECODE <span style="color:var(--red)">DETAILING STUDIO</span></b><a href="#book" class="btn ghost" style="text-decoration:none">Book now</a></nav>
<header class="hero"><h1><span>Where others wash,</span><span>we decode the detail.</span></h1>
<p class="lead">Treated-water hand washes, paint correction and graphene coatings, by appointment in Nolambur, Chennai.</p>
<div><a class="btn" href="#book" style="text-decoration:none;display:inline-block">Choose a service</a></div></header></div>

<main id="flow">
<section class="tint" id="book"><div class="wrap step"><h2>What does your car need?</h2>
<div class="row" id="cats"></div><div class="stack" id="svcs"></div></div></section>
<section id="car"><div class="wrap step"><h2>Tell us about the car</h2>
<div class="row" id="veh"></div>
<p class="muted" style="margin:var(--s4) 0 var(--s2)">Add-ons are optional and priced by vehicle size.</p><div class="row" id="adds"></div></div></section>
<section class="tint" id="time"><div class="wrap step"><h2>Pick a time</h2>
<input class="field" type="date" id="date" style="max-width:260px;margin-bottom:var(--s3)"><div class="row" id="slots"></div>
<p class="muted" id="slotnote" style="margin-top:var(--s2)">Choose a date to see open slots.</p></div></section>
<section id="details"><div class="wrap step"><h2>Your details</h2><div class="grid">
<input class="field" id="name" placeholder="Full name" autocomplete="name"><input class="field" id="phone" placeholder="Phone number" inputmode="tel" autocomplete="tel">
<input class="field" id="email" placeholder="Email (optional)" autocomplete="email"><input class="field" id="model" placeholder="Car make and model"></div>
<textarea class="field" id="notes" rows="3" placeholder="Anything we should know? Scratches, stains, odours." style="margin-top:var(--s2)"></textarea>
<p class="muted" id="err" style="color:var(--red);margin-top:var(--s2);min-height:1.6em"></p>
<button class="btn" id="submit" style="margin-top:var(--s2)">Confirm booking</button></div></section></main>

<section class="done wrap" id="done"><p class="muted">Booking reference</p><h1 id="ref" style="color:var(--red)"></h1>
<h2>See you at the studio.</h2><p class="lead" id="doneMsg" style="margin:0 auto"></p>
<button class="btn ghost" style="margin-top:var(--s4)" onclick="location.reload()">Book another car</button></section>
<footer class="wrap muted" style="padding-bottom:140px">No. 22, Bypass Service Rd, Nolambur, Chennai 95 &nbsp; +91 97873 32633 &nbsp; decodedetailingstudio@gmail.com</footer>
<div class="bar" id="bar"><div class="wrap"><div><div class="muted" id="sum" style="color:#b8bec7"></div><div class="t" id="total"></div></div>
<button class="btn" id="jump" type="button">Continue</button></div></div><div class="toast" id="toast"></div>
<script>
const $=s=>document.querySelector(s),inr=n=>'₹'+n.toLocaleString('en-IN');
const S={cat:'Wash',svc:null,veh:'sedan',adds:new Set(),date:'',time:''};let C;
const el=(t,c,h,f)=>{const e=document.createElement(t);e.className=c||'';e.innerHTML=h||'';if(f)e.onclick=f;return e};
function toast(m){const t=$('#toast');t.textContent=m;t.classList.add('show');setTimeout(()=>t.classList.remove('show'),3200)}
function total(){if(!S.svc)return 0;return S.svc.price[S.veh]+[...S.adds].reduce((a,id)=>a+C.addons.find(x=>x.id===id).price[S.veh],0)}
function draw(){
 const cats=[...new Set(C.services.map(s=>s.cat))];
 $('#cats').replaceChildren(...cats.map(c=>el('button','chip'+(c===S.cat?' on':''),c==='Pass'?'Annual passes':c==='Correction'?'Paint correction':c==='Coating'?'Coatings':'Washes',()=>{S.cat=c;draw()})));
 $('#svcs').replaceChildren(...C.services.filter(s=>s.cat===S.cat).map(s=>el('button','svc'+(S.svc?.id===s.id?' on':''),
  `<h3>${s.name}</h3><span class="p">${inr(s.price[S.veh])}${s.gst?'<small class="muted" style="font:500 .8rem Manrope"> +GST</small>':''}</span><span class="muted">${s.info}</span>`,()=>{S.svc=s;draw()})));
 $('#veh').replaceChildren(...Object.entries(C.vehicles).map(([k,v])=>el('button','chip'+(k===S.veh?' on':''),v,()=>{S.veh=k;draw()})));
 $('#adds').replaceChildren(...C.addons.map(a=>el('button','chip'+(S.adds.has(a.id)?' on':''),`${a.name} &nbsp;${inr(a.price[S.veh])}`,()=>{S.adds.has(a.id)?S.adds.delete(a.id):S.adds.add(a.id);draw()})));
 const bar=$('#bar');bar.classList.toggle('show',!!S.svc);
 if(S.svc){$('#sum').textContent=`${S.svc.name}, ${C.vehicles[S.veh]}${S.adds.size?` + ${S.adds.size} add-on`+(S.adds.size>1?'s':''):''}`;
  $('#total').textContent=inr(total())+(S.svc.gst?' + GST':'')}
 $('#submit').disabled=!(S.svc&&S.date&&S.time);
 document.querySelectorAll('.chip,.svc').forEach(e=>e.setAttribute('aria-pressed',e.classList.contains('on')))}
async function loadSlots(){
 if(!S.date)return;S.time='';const r=await (await fetch('/api/slots?date='+S.date)).json();
 $('#slotnote').textContent=r.slots.some(s=>s.open)?'Each slot holds up to two cars.':'This day is fully booked. Try another date.';
 $('#slots').replaceChildren(...r.slots.map(s=>{const b=el('button','chip slot'+(S.time===s.time?' on':''),s.time,()=>{S.time=s.time;[...$('#slots').children].forEach(x=>x.classList.toggle('on',x===b));draw()});b.disabled=!s.open;return b}));draw()}
async function init(){C=await (await fetch('/api/catalog')).json();$('#date').min=$('#date').value=C.today;S.date=C.today;draw();loadSlots()}
$('#date').onchange=e=>{S.date=e.target.value;loadSlots()};
$('#submit').onclick=async()=>{
 const body={name:$('#name').value,phone:$('#phone').value,email:$('#email').value,vehicle_model:$('#model').value,notes:$('#notes').value,
  vehicle_type:S.veh,service_id:S.svc.id,addons:[...S.adds],date:S.date,time:S.time};
 $('#err').textContent='';const b=$('#submit');b.disabled=true;b.textContent='Booking…';
 const r=await fetch('/api/bookings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const d=await r.json();
 b.textContent='Confirm booking';
 if(!r.ok){$('#err').textContent=d.error;b.disabled=false;if(r.status===409)loadSlots();return}
 $('#flow').style.display=$('#bar').style.display='none';$('#done').style.display='block';$('#ref').textContent=d.ref;
 $('#doneMsg').textContent=`${S.svc.name} on ${S.date} at ${S.time}. Total ${inr(d.total)}${d.gst?' plus GST':''}, payable at the studio. We will call to confirm.`;scrollTo({top:0,behavior:'smooth'})};
const STEPS=['book','car','time','details'];
const at=()=>{let k=0;STEPS.forEach((id,i)=>{if($('#'+id).getBoundingClientRect().top<=innerHeight*.45)k=i});return k};
const label=()=>{$('#jump').textContent=at()===3?'Confirm booking':'Continue'};
addEventListener('scroll',label,{passive:true});addEventListener('resize',label);
$('#jump').onclick=()=>{const k=at(),go=id=>$('#'+id).scrollIntoView({behavior:'smooth',block:'start'});
 if(k>=2&&!S.time){go('time');toast('Pick a time slot first');return}
 if(k===3){$('#submit').click();return}
 go(STEPS[k+1])};
init().then(label);
</script></body></html>"""

DASH = HEAD % "Dashboard | Decode Detailing Studio" + r"""
<style>
body{background:var(--tint)}.field,.chip,.btn.ghost{background:var(--white)}.field::placeholder{color:#8a9099}.top{display:flex;justify-content:space-between;align-items:center;padding:var(--s4) 0 var(--s3)}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:var(--s2);margin-bottom:var(--s4)}
.stat{background:var(--white);padding:var(--s3);border-radius:var(--r)}.stat b{display:block;font:600 3rem/1.1 'Oswald'}
.stat.red{background:var(--red);color:#fff}.bk{background:var(--white);border-radius:var(--r);padding:var(--s3);display:grid;
grid-template-columns:110px 1fr auto;gap:var(--s2) var(--s3);align-items:center;margin-top:var(--s2);animation:in .4s var(--ease) both}
@keyframes in{from{opacity:0;transform:translateY(8px)}}.bk .tm{font:600 1.6rem 'Oswald'}.bk .tm small{display:block;font:500 .8rem Manrope;color:var(--slate)}
.bk .sel{background:var(--tint);border:0;padding:10px 14px;border-radius:999px;cursor:pointer;font-weight:600}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:8px;background:var(--slate)}
.pending .dot{background:#E0A100}.confirmed .dot{background:#2B7BE4}.in-progress .dot{background:#8A4FD6}.completed .dot{background:#1FA463}.cancelled .dot{background:var(--red)}
.login{min-height:100vh;display:grid;place-content:center;gap:var(--s2);width:min(360px,90vw);margin:0 auto}
.filters{display:flex;flex-wrap:wrap;gap:var(--s1);align-items:center;margin-bottom:var(--s2)}.empty{padding:var(--s5) 0;text-align:center}
@media(max-width:640px){.bk{grid-template-columns:1fr}}
.top{flex-wrap:wrap;gap:var(--s2)}.filters .field{min-width:0}
@media(max-width:640px){
 .top{padding:var(--s3) 0 var(--s2)}.top .row{width:100%}.top .btn{flex:1;text-align:center}
 .stats{grid-template-columns:1fr 1fr;gap:12px}.stat{padding:16px}.stat b{font-size:2rem;overflow-wrap:anywhere}
 .bk{grid-template-columns:1fr;padding:20px;gap:12px}.bk .sel{width:100%;padding:14px}
 .bk>div:last-child{display:flex;align-items:center}.filters{gap:8px}#fd{flex:1 1 100%}
 #fs{flex-wrap:nowrap;overflow-x:auto;width:100%;padding-bottom:4px;-webkit-overflow-scrolling:touch}#fs .chip{flex:none}
 .login h1{font-size:2.6rem}}
</style></head><body>
<div class="login" id="login"><h1 style="font-size:3.5rem">Studio dashboard</h1><input class="field" type="password" id="pw" placeholder="Password" aria-label="Password" autocomplete="current-password">
<p class="muted" id="lerr" style="color:var(--red);min-height:1.6em"></p><button class="btn" id="go">Sign in</button></div>
<div class="wrap" id="app" style="display:none;padding-bottom:var(--s6)">
<div class="top"><h2>Bookings</h2><div class="row"><a class="btn ghost" href="/" style="text-decoration:none">View site</a><button class="btn ghost" id="out">Sign out</button></div></div>
<div class="stats" id="stats"></div>
<div class="filters"><input class="field" type="date" id="fd" style="width:auto"><button class="chip" id="all">All dates</button><span id="fs" class="row"></span></div>
<div id="list"></div></div><div class="toast" id="toast"></div>
<script>
const $=s=>document.querySelector(s),inr=n=>'₹'+Number(n).toLocaleString('en-IN');
const ST=['pending','confirmed','in progress','completed','cancelled'];let F={date:'',status:''};
const api=(u,o)=>fetch(u,o).then(async r=>{if(r.status===401){show(false);throw 0}return r.json()});
const post=(u,b,m='POST')=>api(u,{method:m,headers:{'Content-Type':'application/json'},body:JSON.stringify(b||{})});
function show(on){$('#login').style.display=on?'none':'grid';$('#app').style.display=on?'block':'none'}
function toast(m){const t=$('#toast');t.textContent=m;t.classList.add('show');setTimeout(()=>t.classList.remove('show'),2400)}
async function load(){
 const s=await api('/api/admin/stats');
 $('#stats').innerHTML=`<div class="stat red"><b>${s.today}</b>Cars today</div><div class="stat"><b>${s.pending}</b>Awaiting confirmation</div>
 <div class="stat"><b>${s.upcoming}</b>Upcoming</div><div class="stat"><b>${inr(s.revenue)}</b>Completed revenue</div>`;
 const q=new URLSearchParams(Object.fromEntries(Object.entries(F).filter(e=>e[1])));
 const rows=await api('/api/admin/bookings?'+q);
 $('#fs').replaceChildren(...['',...ST].map(x=>{const b=document.createElement('button');b.className='chip'+(F.status===x?' on':'');b.textContent=x||'Any status';b.onclick=()=>{F.status=x;load()};return b}));
 $('#all').classList.toggle('on',!F.date);$('#fd').value=F.date;
 $('#list').innerHTML=rows.length?rows.map((b,i)=>`<div class="bk" style="animation-delay:${i*30}ms">
  <div class="tm">${b.time}<small>${b.date}</small></div>
  <div><h3>${b.name} <span class="muted" style="font:400 .9rem Manrope;text-transform:none">${b.ref}</span></h3>
  <div class="muted">${b.service_name}, ${b.vehicle_model} (${b.vehicle_type})${b.addons?'<br>+ '+b.addons:''}${b.notes?'<br>“'+b.notes+'”':''}<br>${b.phone}${b.email?' · '+b.email:''}</div>
  <div style="margin-top:8px"><b>${inr(b.total)}</b>${b.gst?' <span class="muted">+ GST</span>':''}</div></div>
  <div class="${b.status.replace(' ','-')}"><span class="dot"></span><select class="sel" data-id="${b.id}">${ST.map(x=>`<option${x===b.status?' selected':''}>${x}</option>`).join('')}</select></div></div>`).join('')
  :'<div class="empty"><h3>No bookings here</h3><p class="muted">Change the date or status filter.</p></div>';
 document.querySelectorAll('.sel').forEach(s=>s.onchange=async()=>{await post('/api/admin/bookings/'+s.dataset.id,{status:s.value},'PATCH');toast('Status updated');load()})}
$('#go').onclick=async()=>{const r=await fetch('/api/admin/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:$('#pw').value})});
 if(r.ok){show(true);load()}else $('#lerr').textContent='Wrong password.'};
$('#pw').onkeydown=e=>e.key==='Enter'&&$('#go').click();
$('#out').onclick=async()=>{await post('/api/admin/logout');show(false)};
$('#fd').onchange=e=>{F.date=e.target.value;load()};$('#all').onclick=()=>{F.date='';load()};
api('/api/admin/stats').then(()=>{show(true);load()}).catch(()=>{});setInterval(()=>$('#app').style.display==='block'&&load(),30000);
</script></body></html>"""


@app.get("/")
def home():
    return BOOKING.replace("/*CSS*/", CSS)


@app.get("/dashboard")
def dashboard():
    return DASH.replace("/*CSS*/", CSS)


init_db()
if __name__ == "__main__":
    app.run(debug=True, port=5000)
