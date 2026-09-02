from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from app.database import engine, SessionLocal
from app.models import user
from app.models import recruitment 
from app.models import attendance
from app.models import payroll  # noqa: F401  (so the tables get registered)
from app.models import chat  # noqa: F401  (so the tables get registered)
from app.routes import auth, admin, ceo, recruitment as recruitment_routes
from app.utils.security import hash_password
from app.routes import settings as settings_routes
from app.routes import attendance as attendance_routes
from app.routes import leave as leave_routes
from app.routes import payroll as payroll_routes
from app.routes import chat as chat_routes
from app.routes import hr as hr_routes

app = FastAPI()

# ──── CORS ────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ──── Tables create ────
user.Base.metadata.create_all(bind=engine)
recruitment.Base.metadata.create_all(bind=engine) 
attendance.Base.metadata.create_all(bind=engine)


# SUPER ADMIN CREATE FUNCTION
def create_super_admin():

    db: Session = SessionLocal()

    admin_user = db.query(user.User).filter(user.User.role == "superadmin").first()

    if not admin_user:

        new_admin = user.User(
            full_name="Super Admin",
            email="admin@agentra.com",
            password=hash_password("admin123"),
            role="superadmin",
            status="active"
        )

        db.add(new_admin)
        db.commit()

    db.close()


# run function when server starts
create_super_admin()


# ──── Background scheduler ────
# Auto-approve and reminders no longer wait for someone to open the app
@app.on_event("startup")
def start_background_jobs():
    import os
    if os.getenv("SCHEDULER_ENABLED", "true").strip().lower() in ("false", "0", "no"):
        print("[scheduler] SCHEDULER_ENABLED=false — disabled")
        return

    from app.utils.scheduler import register_jobs
    register_jobs().start()


@app.on_event("shutdown")
def stop_background_jobs():
    from app.utils.scheduler import scheduler
    scheduler.stop()


@app.get("/scheduler/status")
def scheduler_status():
    """Whether the jobs are running — for debugging"""
    from app.utils.scheduler import scheduler
    return {"jobs": scheduler.status()}


# ──── Routers ────
app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(ceo.router)
app.include_router(recruitment_routes.router)
app.include_router(settings_routes.router)
app.include_router(attendance_routes.router)
app.include_router(leave_routes.router)
app.include_router(payroll_routes.router)
app.include_router(chat_routes.router)
app.include_router(hr_routes.router)


@app.get("/")
def home():
    return {"message": "Backend running"}