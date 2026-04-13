import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.database import SessionLocal
from app.models.user import User
from sqlalchemy import select
from app.core.security import get_password_hash

def update_admin():
    with SessionLocal() as db:
        admin = db.execute(select(User).where(User.username == "admin")).scalar_one_or_none()
        if admin:
            admin.hashed_password = get_password_hash("admin123")
            db.commit()
            print("Admin password updated successfully to 'admin123' with valid hash.")
        else:
            print("Admin user not found.")

if __name__ == "__main__":
    update_admin()
