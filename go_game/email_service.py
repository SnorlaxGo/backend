import requests
from .loggers import api_logger as logger
from .config import settings

async def send_password_reset_code_email(recipient_email, reset_code):
    """Send password reset email with verification code using Mailgun API"""
    # Configure Mailgun settings
    MAILGUN_API_KEY = settings.MAILGUN_API_KEY
    MAILGUN_DOMAIN = settings.MAILGUN_DOMAIN
    MAILGUN_BASE_URL = f"https://api.mailgun.net/v3/{MAILGUN_DOMAIN}"

    sender = "Go Game <noreply@go-game.com>"
    subject = "Your Password Reset Code"
    
    # Email body
    body = f"""
    Hello,
    
    You requested a password reset for your Go Game account.
    
    Your password reset code is:
    
    {reset_code}
    
    Enter this code in the app to reset your password.
    This code will expire in 1 hour.
    
    If you did not request this reset, please ignore this email.
    
    Regards,
    The Go Game Team
    """
    # For local development, just log the email
    if settings.ENVIRONMENT == "development":
        logger.info(f"[DEV MODE] Would send email to: {recipient_email}")
        logger.info(f"Subject: {subject}")
        logger.info(f"Body: {body}")
        return
    try:
        # Send email via Mailgun API
        response = requests.post(
            f"{MAILGUN_BASE_URL}/messages",
            auth=("api", MAILGUN_API_KEY),
            data={
                "from": sender,
                "to": recipient_email,
                "subject": subject,
                "text": body
            }
        )
        
        response.raise_for_status()
        logger.info(f"Password reset code email sent to {recipient_email}")
        
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to send password reset email: {str(e)}")
        raise