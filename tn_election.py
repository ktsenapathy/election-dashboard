#!/usr/bin/env python3
"""
Tamil Nadu Real-Time Election Results Dashboard
Uses two confirmed ECI endpoints:
  1) election-json-S22-live.json  — live party+candidate per constituency
  2) statewiseS22{1-12}.htm      — margin, round, runner-up, status

Usage:  python3 tn_election.py
Open:   http://localhost:8080
"""

import json, re, sys, time, threading, subprocess
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

# ── Config ──────────────────────────────────────────────────────────────────
BASE       = "https://results.eci.gov.in/ResultAcGenMay2026"
LIVE_JSON  = f"{BASE}/election-json-S22-live.json"
PARTY_PAGE = f"{BASE}/partywiseresult-S22.htm"
PAGES      = 12        # statewiseS221.htm … statewiseS2212.htm
REFRESH    = 30        # seconds between refreshes
PORT       = 8080

# Confirmed party short-name → color from ECI
PARTY_COLOR = {
    "TVK": "#e72bd9", "ADMK": "#A08547", "DMK": "#05F86E",
    "PMK": "#5D672B", "INC":  "#19AAED", "CPI(M)": "#FF1D15",
    "VCK": "#729E31", "BJP":  "#ff944d", "IUML": "#006600",
}

# ── Shared state ─────────────────────────────────────────────────────────────
state = {
    "constituencies": [],   # sorted by ac_no
    "party_tally":    [],   # [{party, won, leading, total, color}]
    "totals":         {},   # {declared, counting, pending}
    "last_updated":   None,
    "status":         "Starting…",
}

# ── HTTP helpers ─────────────────────────────────────────────────────────────
def curl(url, timeout=15):
    r = subprocess.run(["curl", "-s", "--max-time", str(timeout), url],
                       capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""

# ── Parse live JSON ───────────────────────────────────────────────────────────
def parse_live_json():
    """Returns {ac_no: {party, candidate, color}} for all 234 TN constituencies."""
    raw  = curl(LIVE_JSON)
    if not raw:
        return {}
    try:
        d    = json.loads(raw)
        rows = d.get("S22", {}).get("chartData", [])
    except Exception:
        return {}
    result = {}
    for item in rows:
        # [party, "S22", ac_no, candidate_name, color]
        if len(item) >= 5:
            party, _, ac_no, cand, color = item[0], item[1], item[2], item[3], item[4]
            result[int(ac_no)] = {"party": party, "candidate": cand, "color": color}
    return result

# ── Parse statewise HTML pages ────────────────────────────────────────────────
ROW_PAT = re.compile(
    r"<tr><td align='left'>([^<]+)</td>"          # constituency name
    r"<td align='right'>(\d+)</td>"               # ac_no
    r"<td align='left'>([^<]+)</td>"              # leader name
    r"<td align='left'>.*?align='left'>([^<]+)</td>"  # leader party (nested table)
    r".*?<td align='left'>([^<]+)</td>"           # runner name
    r"<td>.*?align='left'>([^<]+)</td>"           # runner party (nested table)
    r".*?<td align='right'>([\d,]+)</td>"         # margin
    r"\s*<td align='right'>([^<]+)</td>"          # round
    r"\s*<td align='left'>([^<]+)</td></tr>",     # status
    re.DOTALL
)

def parse_statewise_pages():
    """Fetches all 12 statewise pages. Returns list of constituency dicts."""
    rows = []
    for page in range(1, PAGES + 1):
        url  = f"{BASE}/statewiseS22{page}.htm"
        html = curl(url)
        for m in ROW_PAT.finditer(html):
            rows.append({
                "name":         m.group(1).strip(),
                "ac_no":        int(m.group(2)),
                "leader":       m.group(3).strip(),
                "leader_party": m.group(4).strip(),
                "runner":       m.group(5).strip(),
                "runner_party": m.group(6).strip(),
                "margin":       int(m.group(7).replace(",", "")),
                "round":        m.group(8).strip(),
                "status":       m.group(9).strip(),
            })
    return rows

# ── Parse party-wise tally ─────────────────────────────────────────────────
PARTY_ROW_PAT = re.compile(
    r"<tr class=['\"]tr['\"]>\s*<td[^>]*>([^<]+)</td>"     # full party name
    r".*?<td[^>]*>\s*(\d+)\s*</td>"                         # won
    r".*?href=['\"][^'\"]+['\"]>(\d+)</a>",                 # leading (has link)
    re.DOTALL
)

def parse_party_tally():
    html  = curl(PARTY_PAGE)
    tally = []
    for m in PARTY_ROW_PAT.finditer(html):
        full_name = m.group(1).strip()
        won       = int(m.group(2))
        leading   = int(m.group(3))
        # Extract short code from "Full Name - CODE"
        code_m = re.search(r'- ([A-Z()\d]+)$', full_name)
        code   = code_m.group(1) if code_m else full_name[:8]
        tally.append({
            "party":    full_name,
            "code":     code,
            "won":      won,
            "leading":  leading,
            "total":    won + leading,
            "color":    PARTY_COLOR.get(code, "#888"),
        })
    tally.sort(key=lambda x: x["total"], reverse=True)
    return tally

# ── Build full constituency list ───────────────────────────────────────────
def build_data():
    print(f"[{datetime.now():%H:%M:%S}] Fetching live JSON…", flush=True)
    live  = parse_live_json()

    print(f"[{datetime.now():%H:%M:%S}] Fetching 12 statewise pages…", flush=True)
    pages = parse_statewise_pages()
    print(f"[{datetime.now():%H:%M:%S}] Got {len(pages)} constituencies from HTML", flush=True)

    print(f"[{datetime.now():%H:%M:%S}] Fetching party tally…", flush=True)
    tally = parse_party_tally()

    # Merge live JSON into statewise data
    for r in pages:
        lv = live.get(r["ac_no"], {})
        r["color"]        = lv.get("color", "#888")
        # live JSON has party short code; use full name from statewise if available
        r["leader_code"]  = lv.get("party", "")

    pages.sort(key=lambda x: x["ac_no"])

    # Summary counts
    declared = sum(1 for r in pages if "Result Declared" in r["status"] or "WON" in r["status"].upper())
    counting = sum(1 for r in pages if "Progress" in r["status"] or "Counting" in r["status"])
    pending  = len(pages) - declared - counting

    return pages, tally, {"declared": declared, "counting": counting, "pending": pending}

# ── Background refresh ─────────────────────────────────────────────────────
def refresh_loop():
    while True:
        try:
            state["status"] = "Fetching data…"
            cs, tally, totals = build_data()
            state["constituencies"] = cs
            state["party_tally"]    = tally
            state["totals"]         = totals
            state["last_updated"]   = datetime.now()
            state["status"]         = f"OK — {len(cs)} constituencies loaded"
            print(f"[{datetime.now():%H:%M:%S}] Refresh done. Next in {REFRESH}s", flush=True)
        except Exception as e:
            state["status"] = f"Error: {e}"
            print(f"[ERROR] {e}", flush=True)
        time.sleep(REFRESH)

# ── HTML Dashboard ─────────────────────────────────────────────────────────
DASHBOARD = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Tamil Nadu Live Election Results 2026</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#0d1117;--card:#161b22;--border:#30363d;--text:#e6edf3;--muted:#8b949e;
  --green:#3fb950;--yellow:#d29922;--blue:#58a6ff;--red:#f85149}
body{background:var(--bg);color:var(--text);font-family:'Segoe UI',system-ui,sans-serif;font-size:14px;min-height:100vh}

/* Header */
header{background:linear-gradient(135deg,#0d2137,#0d1117);padding:18px 24px;
  border-bottom:1px solid var(--border);display:flex;justify-content:space-between;
  align-items:center;flex-wrap:wrap;gap:12px;position:sticky;top:0;z-index:100}
header h1{font-size:1.25rem;font-weight:700}
header h1 em{color:var(--blue);font-style:normal}
.live-pill{display:flex;align-items:center;gap:6px;padding:4px 12px;
  background:#3fb95022;border:1px solid #3fb95044;border-radius:99px;font-size:.78rem;color:var(--green)}
.live-dot{width:7px;height:7px;border-radius:50%;background:var(--green);
  animation:pulse 1.5s ease-in-out infinite}
@keyframes pulse{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.5;transform:scale(.8)}}
.meta{font-size:.75rem;color:var(--muted)}

/* Summary bar */
.summary{display:flex;gap:10px;padding:14px 24px;flex-wrap:wrap;
  border-bottom:1px solid var(--border);background:#0d1117}
.stat{background:var(--card);border:1px solid var(--border);border-radius:8px;
  padding:10px 18px;flex:1;min-width:130px}
.stat .l{font-size:.7rem;color:var(--muted);text-transform:uppercase;letter-spacing:.06em}
.stat .v{font-size:1.5rem;font-weight:700;margin-top:3px}

/* Party tally */
.section{padding:14px 24px}
.section h2{font-size:.72rem;color:var(--muted);text-transform:uppercase;
  letter-spacing:.08em;margin-bottom:10px}
.party-grid{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:14px}
.party-chip{display:flex;align-items:center;gap:8px;padding:7px 14px;
  background:var(--card);border:1px solid var(--border);border-radius:8px;
  font-size:.82rem;min-width:160px;cursor:pointer;transition:border-color .15s}
.party-chip:hover{border-color:var(--blue)}
.party-chip.selected{border-color:var(--blue);background:#192a3e}
.party-dot{width:10px;height:10px;border-radius:50%;flex-shrink:0}
.party-chip .pname{flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.party-chip .ptotal{font-weight:700;font-size:.95rem}
.party-chip .pwon{color:var(--green);font-size:.75rem}
.party-chip .plead{color:var(--yellow);font-size:.75rem}

/* Table */
table{width:100%;border-collapse:collapse;font-size:.83rem}
th{text-align:left;padding:8px 10px;color:var(--muted);font-weight:600;
  font-size:.7rem;text-transform:uppercase;border-bottom:2px solid var(--border);
  white-space:nowrap;cursor:pointer;user-select:none}
th:hover{color:var(--text)}
th.sorted-asc::after{content:" ▲"}
th.sorted-desc::after{content:" ▼"}
td{padding:8px 10px;border-bottom:1px solid #21262d;vertical-align:middle}
tr:hover td{background:#161b22}
.badge{display:inline-block;padding:2px 8px;border-radius:99px;font-size:.7rem;font-weight:600;white-space:nowrap}
.badge-progress{background:#d2992222;color:var(--yellow);border:1px solid #d2992244}
.badge-declared{background:#3fb95022;color:var(--green);border:1px solid #3fb95044}
.badge-pending{background:#58a6ff22;color:var(--blue);border:1px solid #58a6ff44}
.party-tag{display:inline-block;padding:1px 7px;border-radius:4px;font-size:.72rem;
  color:#fff;font-weight:600;white-space:nowrap}
.margin-val{font-weight:700;color:var(--blue)}
.round-val{color:var(--muted);font-size:.78rem}
.cand-name{font-weight:500}
.runner-name{color:var(--muted);font-size:.78rem}

/* Controls */
.controls{display:flex;gap:10px;padding:0 24px 12px;flex-wrap:wrap;align-items:center}
input[type=search],select{
  background:#161b22;border:1px solid var(--border);color:var(--text);
  border-radius:6px;padding:6px 12px;font-size:.83rem;outline:none}
input[type=search]{min-width:240px}
input[type=search]:focus,select:focus{border-color:var(--blue)}
.btn{background:#1f6feb;color:#fff;border:none;padding:6px 14px;
  border-radius:6px;cursor:pointer;font-size:.83rem;font-weight:600}
.btn:hover{background:#388bfd}
.btn-sm{padding:4px 10px;font-size:.75rem;background:#21262d;color:var(--text);border:1px solid var(--border)}
.btn-sm:hover{border-color:var(--blue)}

/* Countdown */
#countdown{font-size:.72rem;color:var(--muted)}
</style>
</head>
<body>

<header>
  <div>
    <h1>Tamil Nadu — <em>Live Election Results 2026</em></h1>
    <div class="meta" id="meta">Loading data from ECI…</div>
  </div>
  <div style="display:flex;gap:10px;align-items:center">
    <span id="countdown"></span>
    <div class="live-pill"><span class="live-dot"></span> LIVE</div>
  </div>
</header>

<div class="summary">
  <div class="stat"><div class="l">Total Seats</div><div class="v">234</div></div>
  <div class="stat"><div class="l">Results Declared</div><div class="v" id="s-dec" style="color:var(--green)">–</div></div>
  <div class="stat"><div class="l">Counting in Progress</div><div class="v" id="s-cnt" style="color:var(--yellow)">–</div></div>
  <div class="stat"><div class="l">Yet to Count</div><div class="v" id="s-pend" style="color:var(--blue)">–</div></div>
  <div class="stat"><div class="l">Majority Mark</div><div class="v" style="color:var(--red)">118</div></div>
</div>

<div class="section">
  <h2>Party-wise Tally <span style="font-weight:400;text-transform:none;color:var(--muted)">(click to filter)</span></h2>
  <div class="party-grid" id="party-grid"></div>
</div>

<div class="controls">
  <input type="search" id="search" placeholder="Search constituency / candidate / party…" oninput="renderTable()">
  <select id="filter-status" onchange="renderTable()">
    <option value="">All Status</option>
    <option value="Progress">Counting in Progress</option>
    <option value="Declared">Result Declared</option>
    <option value="Pending">Pending</option>
  </select>
  <button class="btn btn-sm" onclick="clearFilters()">Clear</button>
  <button class="btn" onclick="forceRefresh()">↺ Refresh Now</button>
  <span id="row-count" style="color:var(--muted);font-size:.78rem"></span>
</div>

<div class="section" style="padding-top:0">
  <table>
    <thead>
      <tr>
        <th onclick="sortBy('ac_no')">#</th>
        <th onclick="sortBy('name')">Constituency</th>
        <th onclick="sortBy('leader')">Leader / Runner-up</th>
        <th onclick="sortBy('leader_party')">Party</th>
        <th onclick="sortBy('margin')">Margin</th>
        <th onclick="sortBy('round')">Round</th>
        <th onclick="sortBy('status')">Status</th>
      </tr>
    </thead>
    <tbody id="tbody"></tbody>
  </table>
</div>

<script>
let ALL = [], SORT_KEY = 'ac_no', SORT_DIR = 1, SELECTED_PARTY = '';
let countdown = 30, countdownTimer;

function fmt(n){ return Number(n).toLocaleString('en-IN'); }

function partyColor(code, full){
  const MAP={TVK:'#e72bd9',ADMK:'#A08547',DMK:'#05F86E',PMK:'#5D672B',
    INC:'#19AAED','CPI(M)':'#FF1D15',VCK:'#729E31',BJP:'#ff944d',IUML:'#006600'};
  for(const [k,v] of Object.entries(MAP)){
    if((code||'').includes(k)||(full||'').includes(k)) return v;
  }
  return '#888';
}

function statusBadge(s){
  if(!s||s==='—') return '<span class="badge badge-pending">⏳ Pending</span>';
  if(/declared|won/i.test(s)) return '<span class="badge badge-declared">✓ Declared</span>';
  if(/progress|counting/i.test(s)) return '<span class="badge badge-progress">⟳ Counting</span>';
  return `<span class="badge badge-pending">${s}</span>`;
}

function renderPartyGrid(tally){
  const grid = document.getElementById('party-grid');
  grid.innerHTML = tally.map(p=>`
    <div class="party-chip ${SELECTED_PARTY===p.code?'selected':''}"
         onclick="filterParty('${p.code}')"
         title="${p.party}">
      <span class="party-dot" style="background:${p.color}"></span>
      <span class="pname">${p.code||p.party.split(' - ')[0]}</span>
      <span class="ptotal">${p.total}</span>
      <span class="pwon">${p.won>0?'✓'+p.won:''}</span>
      <span class="plead">${p.leading>0?'▲'+p.leading:''}</span>
    </div>`).join('');
}

function filterParty(code){
  SELECTED_PARTY = SELECTED_PARTY===code ? '' : code;
  renderPartyGrid(window._tally||[]);
  renderTable();
}

function clearFilters(){
  document.getElementById('search').value='';
  document.getElementById('filter-status').value='';
  SELECTED_PARTY='';
  renderPartyGrid(window._tally||[]);
  renderTable();
}

function sortBy(key){
  if(SORT_KEY===key) SORT_DIR=-SORT_DIR;
  else { SORT_KEY=key; SORT_DIR=1; }
  document.querySelectorAll('th').forEach(th=>{
    th.classList.remove('sorted-asc','sorted-desc');
  });
  const idx=['ac_no','name','leader','leader_party','margin','round','status'].indexOf(key);
  if(idx>=0){
    const th=document.querySelectorAll('th')[idx];
    th.classList.add(SORT_DIR===1?'sorted-asc':'sorted-desc');
  }
  renderTable();
}

function renderTable(){
  const q  = (document.getElementById('search').value||'').toLowerCase();
  const sf = document.getElementById('filter-status').value.toLowerCase();

  let list = ALL.filter(r=>{
    if(sf){
      const s=(r.status||'').toLowerCase();
      if(sf==='pending' && (s.includes('progress')||s.includes('declared')||s.includes('won'))) return false;
      if(sf!=='pending' && !s.includes(sf)) return false;
    }
    if(SELECTED_PARTY){
      const code=(r.leader_code||r.leader_party||'');
      if(!code.toUpperCase().includes(SELECTED_PARTY)) return false;
    }
    if(q){
      const hay=(r.name+' '+r.leader+' '+r.runner+' '+r.leader_party+' '+r.runner_party).toLowerCase();
      if(!hay.includes(q)) return false;
    }
    return true;
  });

  list.sort((a,b)=>{
    let av=a[SORT_KEY]||'', bv=b[SORT_KEY]||'';
    if(SORT_KEY==='margin'||SORT_KEY==='ac_no'){av=+av||0;bv=+bv||0;}
    else if(SORT_KEY==='round'){
      av=av?parseInt(av.split('/')[0])||0:0;
      bv=bv?parseInt(bv.split('/')[0])||0:0;
    } else {
      av=String(av).toLowerCase(); bv=String(bv).toLowerCase();
    }
    if(av<bv) return -SORT_DIR;
    if(av>bv) return SORT_DIR;
    return 0;
  });

  document.getElementById('row-count').textContent=`Showing ${list.length} of 234`;

  const tbody=document.getElementById('tbody');
  if(!list.length){
    tbody.innerHTML='<tr><td colspan="7" style="text-align:center;padding:40px;color:var(--muted)">No results match your filter</td></tr>';
    return;
  }

  tbody.innerHTML = list.map(r=>{
    const color  = r.color || partyColor(r.leader_code, r.leader_party);
    const lp     = r.leader_code||r.leader_party.split(' - ').pop()||r.leader_party;
    const rp     = r.runner_party.split(' - ').pop()||r.runner_party;
    return `<tr>
      <td style="color:var(--muted);font-size:.8rem">${r.ac_no}</td>
      <td><strong>${r.name}</strong></td>
      <td>
        <div class="cand-name">${r.leader||'—'}</div>
        ${r.runner?`<div class="runner-name">vs ${r.runner}</div>`:''}
      </td>
      <td>
        <span class="party-tag" style="background:${color}">${lp}</span>
        ${r.runner_party?`<br><span class="runner-name">${rp}</span>`:''}
      </td>
      <td class="margin-val">${r.margin>0?fmt(r.margin):'—'}</td>
      <td class="round-val">${r.round||'—'}</td>
      <td>${statusBadge(r.status)}</td>
    </tr>`;
  }).join('');
}

let _refreshing = false;
async function loadData(){
  if(_refreshing) return;
  _refreshing=true;
  try{
    const res  = await fetch('/api/data');
    const data = await res.json();
    ALL            = data.constituencies || [];
    window._tally  = data.party_tally    || [];

    const t = data.totals||{};
    document.getElementById('s-dec').textContent  = t.declared??'–';
    document.getElementById('s-cnt').textContent  = t.counting??'–';
    document.getElementById('s-pend').textContent = t.pending??'–';

    const ts = data.last_updated
      ? new Date(data.last_updated*1000).toLocaleTimeString('en-IN')
      : '—';
    document.getElementById('meta').textContent =
      `Source: results.eci.gov.in  ·  Last updated: ${ts}  ·  ${data.status}`;

    renderPartyGrid(window._tally);
    renderTable();
    countdown=30;
  }catch(e){
    document.getElementById('meta').textContent='Error: '+e;
  }finally{
    _refreshing=false;
  }
}

function forceRefresh(){ countdown=0; loadData(); }

// Countdown ticker
function tick(){
  countdown--;
  document.getElementById('countdown').textContent=
    countdown>0?`Refreshing in ${countdown}s`:'Refreshing…';
  if(countdown<=0){ loadData(); countdown=30; }
}

loadData();
sortBy('ac_no');
countdownTimer=setInterval(tick,1000);
</script>
</body>
</html>"""

# ── HTTP Handler ──────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_): pass

    def do_GET(self):
        if self.path == "/api/data":
            self._json()
        elif self.path in ("/", "/index.html"):
            self._html()
        else:
            self.send_error(404)

    def _html(self):
        b = DASHBOARD.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(b))
        self.end_headers()
        self.wfile.write(b)

    def _json(self):
        lu = state["last_updated"]
        payload = json.dumps({
            "constituencies": state["constituencies"],
            "party_tally":    state["party_tally"],
            "totals":         state["totals"],
            "last_updated":   lu.timestamp() if lu else None,
            "status":         state["status"],
        }, ensure_ascii=False)
        b = payload.encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", len(b))
        self.end_headers()
        self.wfile.write(b)

# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 60)
    print("  Tamil Nadu Election Results — Live Dashboard")
    print("  Source  : results.eci.gov.in")
    print(f"  Refresh : every {REFRESH} seconds")
    print(f"  Open    : http://localhost:{PORT}")
    print("=" * 60)

    t = threading.Thread(target=refresh_loop, daemon=True)
    t.start()

    try:
        server = HTTPServer(("", PORT), Handler)
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        sys.exit(0)
