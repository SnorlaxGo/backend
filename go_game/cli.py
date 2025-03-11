import click
from .database import SessionLocal, engine
from . import models

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

if __name__ == '__main__':
    cli() 