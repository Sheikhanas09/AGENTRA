# Agentra — Backend

```
Backend/
├── app/                    chalti hui application
│   ├── main.py             FastAPI app, routers, startup
│   ├── database.py         engine, session, admin_engine()
│   ├── models/             SQLAlchemy tables (8)
│   ├── routes/             HTTP endpoints (11)
│   ├── schemas/            Pydantic request/response shapes
│   ├── crud/               user create/read helpers
│   ├── agents/             LangGraph agents (12)
│   ├── utils/              domain logic — tenancy, crypto, payroll, chat
│   └── mcp_servers/        stdio MCP subprocess (calendar + mail)
│
├── tests/                  har da'wa ka chalta hua saboot (15)
├── migrations/             schema tabdeeliyan (8)
├── tools/                  operator scripts (3)
└── requirements.txt
```

Sab kuch **`Backend/` se** chalta hai:

```
py tests/check_tenancy.py
py migrations/migrate_rls.py --apply
py tools/set_org_chart.py
```

> Har move ki hui script ke sar par ek chhota bootstrap hai jo `Backend/`
> ko `sys.path` par daalta hai aur wahan `chdir` karta hai. Iske baghair
> `py tests/x.py` par Python sirf `tests/` ko raaste par rakhta hai (cwd
> ko nahi), aur `import app` nakaam ho jata.

---

## tests/ — kaun sa suite kya sabit karta hai

| Suite | Kya parkhta hai |
| --- | --- |
| `check_tenancy.py` | 11 sections; aakhri teen ASLI HTTP se doosri company ka data lene ki koshish karte hain |
| `check_tampering.py` | `company_id` aur token ke saath chher-chhar |
| `check_companies_rls.py` | `companies` table ki apni hadd, aur kya connected role waqai RLS ke taabe hai |
| `check_offer_token.py` | public offer link — guessing hi yahan hamla hai |
| `check_scheduler.py` | background jobs, do companies ek saath |
| `check_integrations.py` | per-company Google, PKCE, connectivity |
| `check_encryption.py` | **raw SQL se** parhta hai — jo ek DB dump dekhta hai |
| `check_cv.py` | CV kis application ki hai, naam kis ka hai, ginti sach hai ya nahi |
| `check_scope.py` | employee ka data CEO tak ja sakta hai, ulta kabhi nahi |
| `check_chat.py` | 34 cases — employee help desk (**asli model**, minutes + tokens) |
| `check_console.py` | 75 cases — CEO console (**asli model**) |
| `check_llm.py` | configured key aur model waqai jawab dete hain |
| `_e2e_newcompany.py` | signup se payroll tak, ek nayi company par |
| `_e2e_many.py` | chaar companies, har ordered jori |
| `_cleanup_probe.py` | probe companies hataata hai (`--apply`) |

Do usool jo in suites se nikle:

> Jo check khud ko **skip** kar sakta hai, wo check pass hai.
> Jo check kisi **haalat** ka da'wa kare, wo us din girega jab koi jaiz
> kaam karega — data se milao.
> Aur jo **ginti** kare, wo check nahi, report hai.

---

## migrations/

Tarteeb se chali hui hain. Har ek **idempotent** hai aur `--apply` ke
baghair sirf dikhati hai ke kya karegi.

```
migrate_attendance.py        attendance/leave columns aur indexes
migrate_multitenant.py       company_id har table par + backfill
migrate_rls.py               37 RLS policies + agentra_app role
migrate_companies_rls.py     companies table ki apni hadd
migrate_integrations.py      per-company Google connection
migrate_offer_tokens.py      offer link ke token columns
migrate_application_cv.py    CV candidate se application par
migrate_encrypt_chat.py      transcripts at-rest encrypted
```

⚠ Migrations `ADMIN_DATABASE_URL` istemal karti hain, `DATABASE_URL`
nahi — app `agentra_app` par chalta hai jo DDL nahi kar sakta. Ye
jaan-boojh kar hai: jo app apni hifazat karne wali policies khud gira
sake, wo mehfooz nahi.

---

## tools/

Operator ke liye, application ka hissa nahi:

```
set_org_chart.py              job titles ko department column se nikalna
regenerate_payslips.py        joining-date bug se kharab payslips dobara
fix_prejoining_payslips.py    joining se pehle ke mahinon ki payslips cancel
```

Pichli do ek dafa ka kaam thin aur chal chuki hain; tareekh ke liye
rakhi hain.

---

## `.env`

```
DATABASE_URL              agentra_app (non-superuser — warna RLS bekar)
ADMIN_DATABASE_URL        postgres — sirf migrations ke liye
SECRET_KEY                JWT              ghoome -> sab logout
INTEGRATION_SECRET_KEY    Google tokens    khoye  -> sab dobara connect
CHAT_SECRET_KEY           transcripts      khoye  -> hamesha ke liye gaye
LLM_PROVIDER / LLM_MODEL / LLM_API_KEY
```

Teen secrets alag hain kyunke teenon ka nuqsan alag hai. Tafseel:
`docs/AGENTRA_CONTEXT.md` → `## CRYPTO`.

Poora nizam samajhna ho to `docs/AGENTRA_CONTEXT.md` →
`## MULTI-TENANCY` (section 0 se shuru karein).
