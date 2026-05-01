from slowapi import Limiter
from slowapi.util import get_remote_address

# Module-level Limiter so handlers can decorate routes (e.g. @limiter.limit("10/minute"))
# without importing from app.main. main.py registers the exception handler.
limiter = Limiter(key_func=get_remote_address)
