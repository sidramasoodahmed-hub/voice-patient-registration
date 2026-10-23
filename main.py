import json
import logging
import os
import secrets
from datetime import date, datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from db import CallLog, Patient, get_db, init_db
from schemas import PatientCreate, PatientUpdate, normalize_phone, parse_dob

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("patient-agent")

VAPI_SECRET = os.getenv("VAPI_SECRET", "")  # optional shared secret for the webhook
init_db()
app = FastAPI(title="Patient Registration API")


# ---------- helpers & error envelope ----------
def respond(data=None, status=200, error=None):
    return JSONResponse({"data": data, "error": error}, status_code=status)


def to_dict(p: Patient) -> dict:
    out = {}
    for c in Patient.__table__.columns:
        v = getattr(p, c.name)
        if isinstance(v, datetime):
            v = (v if v.tzinfo else v.replace(tzinfo=timezone.utc)).isoformat()
        elif isinstance(v, date):
            v = v.isoformat()
        out[c.name] = v
    return out


def clean_msg(m: str) -> str:
    return m.replace("Value error, ", "")


@app.exception_handler(RequestValidationError)
async def on_validation(request, exc):
    details = [{"field": ".".join(str(x) for x in e["loc"][1:]), "message": clean_msg(e["msg"])}
               for e in exc.errors()]
    return respond(None, 422, {"code": "validation_error", "message": "Invalid input", "details": details})


@app.exception_handler(StarletteHTTPException)
async def on_http(request, exc):
    return respond(None, exc.status_code, {"code": "http_error", "message": str(exc.detail)})


@app.exception_handler(Exception)
async def on_error(request, exc):
    log.exception("Unhandled error")
    return respond(None, 500, {"code": "internal_error", "message": "Internal server error"})


def get_patient_or_404(db: Session, pid: str) -> Patient:
    try:
        pid = str(UUID(pid))
    except ValueError:
        raise HTTPException(400, "patient_id must be a valid UUID")
    p = db.get(Patient, pid)
    if not p or p.deleted_at:
        raise HTTPException(404, "Patient not found")
    return p


# ---------- REST API ----------
@app.get("/health")
def health():
    return respond({"status": "ok"})


@app.get("/patients")
def list_patients(last_name: Optional[str] = None, date_of_birth: Optional[str] = None,
                  phone_number: Optional[str] = None, db: Session = Depends(get_db)):
    q = select(Patient).where(Patient.deleted_at.is_(None))
    try:
        if last_name:
            q = q.where(func.lower(Patient.last_name) == last_name.strip().lower())
        if date_of_birth:
            q = q.where(Patient.date_of_birth == parse_dob(date_of_birth))
        if phone_number:
            q = q.where(Patient.phone_number == normalize_phone(phone_number))
    except ValueError as e:
        raise HTTPException(400, f"Invalid filter: {e}")
    rows = db.scalars(q.order_by(Patient.created_at.desc())).all()
    return respond([to_dict(p) for p in rows])


@app.get("/patients/{patient_id}")
def get_patient(patient_id: str, db: Session = Depends(get_db)):
    return respond(to_dict(get_patient_or_404(db, patient_id)))


@app.get("/patients/{patient_id}/calls")
def get_calls(patient_id: str, db: Session = Depends(get_db)):
    p = get_patient_or_404(db, patient_id)
    rows = db.scalars(select(CallLog).where(CallLog.patient_id == p.patient_id)
                      .order_by(CallLog.created_at.desc())).all()
    return respond([{"call_id": r.call_id, "summary": r.summary, "transcript": r.transcript,
                     "ended_reason": r.ended_reason} for r in rows])


@app.post("/patients")
def create_patient(body: PatientCreate, db: Session = Depends(get_db)):
    p = Patient(**body.model_dump())
    db.add(p)
    db.commit()
    return respond(to_dict(p), 201)


@app.put("/patients/{patient_id}")
def update_patient(patient_id: str, body: PatientUpdate, db: Session = Depends(get_db)):
    p = get_patient_or_404(db, patient_id)
    for k, v in body.model_dump(exclude_unset=True).items():
        if v is not None:
            setattr(p, k, v)
    db.commit()
    return respond(to_dict(p))


@app.delete("/patients/{patient_id}")
def delete_patient(patient_id: str, db: Session = Depends(get_db)):
    p = get_patient_or_404(db, patient_id)
    p.deleted_at = datetime.now(timezone.utc)  # soft delete
    db.commit()
    return respond({"patient_id": p.patient_id, "deleted_at": p.deleted_at.isoformat()})


# ---------- Voice agent tools (called by Vapi) ----------
def tool_lookup_patient(db: Session, args: dict) -> dict:
    try:
        phone = normalize_phone(args.get("phone_number", ""))
    except ValueError as e:
        return {"found": False, "error": f"phone_number {e}. Ask the caller to repeat it."}
    p = db.scalar(select(Patient).where(Patient.phone_number == phone, Patient.deleted_at.is_(None)))
    if not p:
        return {"found": False}
    return {"found": True, "patient_id": p.patient_id, "first_name": p.first_name, "last_name": p.last_name}


def tool_save_patient(db: Session, args: dict, call_id: Optional[str]) -> dict:
    args = {k: v for k, v in args.items() if v not in (None, "")}
    pid = args.pop("patient_id", None)
    log.info("FINAL_PAYLOAD call=%s patient_id=%s data=%s", call_id, pid, json.dumps(args, default=str))
    try:
        if pid:
            p = db.get(Patient, pid)
            if not p or p.deleted_at:
                return {"success": False, "error": "Existing record not found. Register as a new patient."}
            for k, v in PatientUpdate(**args).model_dump(exclude_unset=True).items():
                if v is not None:
                    setattr(p, k, v)
        else:
            p = Patient(**PatientCreate(**args).model_dump())
            db.add(p)
        db.flush()
        if call_id:
            row = db.get(CallLog, call_id) or CallLog(call_id=call_id)
            row.patient_id = p.patient_id
            db.add(row)
        db.commit()
        return {"success": True, "patient_id": p.patient_id, "first_name": p.first_name}
    except ValidationError as e:
        db.rollback()
        errs = [{"field": ".".join(str(x) for x in er["loc"]), "problem": clean_msg(er["msg"])}
                for er in e.errors()]
        return {"success": False, "errors": errs,
                "instruction": "Tell the caller naturally what was wrong with ONLY these fields and re-ask for them."}
    except Exception:
        db.rollback()
        log.exception("DB write failed")
        return {"success": False, "system_error": True,
                "instruction": "Apologize, retry once. If it fails again, ask the caller to call back shortly."}


@app.post("/vapi/webhook")
async def vapi_webhook(request: Request, x_vapi_secret: Optional[str] = Header(None),
                       db: Session = Depends(get_db)):
    if VAPI_SECRET and not secrets.compare_digest(x_vapi_secret or "", VAPI_SECRET):
        raise HTTPException(401, "Unauthorized")
    body = await request.json()
    msg = body.get("message", {})
    call_id = (msg.get("call") or {}).get("id")

    if msg.get("type") == "tool-calls":
        results = []
        for c in msg.get("toolCallList") or msg.get("toolCalls") or []:
            fn = c.get("function") or {}
            name = c.get("name") or fn.get("name")
            args = c.get("arguments") or fn.get("arguments") or {}
            if isinstance(args, str):
                args = json.loads(args or "{}")
            if name == "lookup_patient":
                result = tool_lookup_patient(db, args)
            elif name == "save_patient":
                result = tool_save_patient(db, args, call_id)
            else:
                result = {"error": f"unknown tool {name}"}
            results.append({"toolCallId": c.get("id"), "result": json.dumps(result)})
        return {"results": results}

    if msg.get("type") == "end-of-call-report" and call_id:
        art = msg.get("artifact") or {}
        row = db.get(CallLog, call_id) or CallLog(call_id=call_id)
        row.transcript = art.get("transcript") or msg.get("transcript")
        row.summary = (msg.get("analysis") or {}).get("summary") or msg.get("summary")
        row.ended_reason = msg.get("endedReason")
        db.add(row)
        db.commit()
        log.info("CALL_END call=%s reason=%s summary=%s", call_id, row.ended_reason, row.summary)
    return {}


# ---------- Dashboard ----------
@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/dashboard")


DASHBOARD = """<!doctype html><html><head><meta charset="utf-8"><title>Patients</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
body{font-family:system-ui,sans-serif;margin:2rem;color:#222}
table{border-collapse:collapse;width:100%;margin-top:1rem}
th,td{padding:8px 10px;border-bottom:1px solid #ddd;text-align:left;font-size:14px}
th{background:#f4f4f6} tr.row{cursor:pointer} tr.row:hover{background:#f0f6ff}
input{padding:8px;width:260px} pre{background:#f7f7f9;padding:1rem;white-space:pre-wrap;overflow:auto}
</style></head><body>
<h1>Registered Patients</h1>
<input id="q" placeholder="Search name or phone..." oninput="render()">
<span id="count"></span>
<table><thead><tr id="head"></tr></thead><tbody id="rows"></tbody></table>
<h3>Details (click a row)</h3><pre id="detail">-</pre>
<script>
const cols=["first_name","last_name","date_of_birth","sex","phone_number","city","state","created_at"];
let all=[];
const head=document.getElementById("head");
cols.forEach(c=>{const th=document.createElement("th");th.textContent=c.replace(/_/g," ");head.appendChild(th)});
async function load(){
  try{const r=await fetch("/patients");const j=await r.json();all=j.data||[];render();}catch(e){}
}
function render(){
  const f=document.getElementById("q").value.toLowerCase();
  const tb=document.getElementById("rows");tb.replaceChildren();
  const rows=all.filter(p=>(p.first_name+" "+p.last_name+" "+p.phone_number).toLowerCase().includes(f));
  rows.forEach(p=>{
    const tr=document.createElement("tr");tr.className="row";
    cols.forEach(c=>{const td=document.createElement("td");td.textContent=p[c]??"";tr.appendChild(td)});
    tr.onclick=()=>show(p);tb.appendChild(tr);
  });
  document.getElementById("count").textContent=" "+rows.length+" patient(s)";
}
async function show(p){
  const r=await fetch("/patients/"+p.patient_id+"/calls");const j=await r.json();
  document.getElementById("detail").textContent=JSON.stringify({patient:p,calls:j.data},null,2);
}
load();setInterval(load,5000);
</script></body></html>"""


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    return DASHBOARD