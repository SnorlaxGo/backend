import click
from .database import SessionLocal, engine
from . import models
from .auth import get_password_hash
@click.group()
def cli():
    pass

@cli.command()
def init_db():
    """Initialize the database tables"""
    models.Base.metadata.create_all(bind=engine)
    click.echo("Database initialized!")

@cli.command()
def seed_db():
    """Seed the database with test data"""
    from .seed import seed_users
    seed_users()
    click.echo("Database seeded!")

@cli.command()
def reset_db():
    """Reset the database (drop all tables and recreate)"""
    if click.confirm('Are you sure you want to reset the database?'):
        models.Base.metadata.drop_all(bind=engine)
        models.Base.metadata.create_all(bind=engine)
        click.echo("Database reset complete!")

@cli.command()
@click.argument('email')
@click.argument('username')
@click.argument('password')
def add_user(email, username, password):
    """Add a new user with the given username and password"""
    db = SessionLocal()
    try:
        user = models.User(
            email=email,
            username=username,
            role=models.UserRole.USER,
            hashed_password=get_password_hash(password)
        )
        db.add(user)
        db.commit()
        click.echo(f"User {username} created successfully!")
    except Exception as e:
        db.rollback()
        click.echo(f"Error creating user: {str(e)}")
    finally:
        db.close()


if __name__ == '__main__':
    cli() 