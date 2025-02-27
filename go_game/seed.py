from sqlalchemy.orm import Session
from . import models, auth
from .database import SessionLocal, engine

def seed_users():
    db = SessionLocal()
    try:
        # Check if admin already exists
        admin = db.query(models.User).filter(models.User.email == "admin@example.com").first()
        if not admin:
            # Create admin user
            admin = models.User(
                email="admin@example.com",
                username="admin",
                role=models.UserRole.ADMIN,
                hashed_password=auth.get_password_hash("admin123")  # Change this!
            )
            db.add(admin)

        # Add some test users
        test_users = [
            {
                "email": "mod@example.com",
                "username": "moderator",
                "password": "mod123",  # Change this!
                "role": models.UserRole.MODERATOR
            },
            {
                "email": "user@example.com",
                "username": "testuser",
                "password": "user123",  # Change this!
                "role": models.UserRole.USER
            }
        ]

        for user_data in test_users:
            if not db.query(models.User).filter(models.User.email == user_data["email"]).first():
                user = models.User(
                    email=user_data["email"],
                    username=user_data["username"],
                    role=user_data["role"],
                    hashed_password=auth.get_password_hash(user_data["password"])
                )
                db.add(user)

        db.commit()
    finally:
        db.close()

if __name__ == "__main__":
    seed_users() 