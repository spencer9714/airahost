class ScraperForbiddenError(RuntimeError):
    """Raised when a scraper is blocked by Airbnb (403/challenge/auth wall)."""
