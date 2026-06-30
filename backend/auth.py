import os
import logging
from typing import Any, Dict

import jwt
from jwt import PyJWKClient
from jwt.exceptions import (
    DecodeError,
    ExpiredSignatureError,
    InvalidAudienceError,
    InvalidIssuerError,
    InvalidTokenError,
    MissingRequiredClaimError,
    PyJWKError,
)
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logger = logging.getLogger(__name__)

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET", "")

EXPECTED_ISSUER = f"{SUPABASE_URL}/auth/v1"
EXPECTED_AUDIENCE = "authenticated"
LEEWAY_SECONDS = 10

bearer_scheme = HTTPBearer(auto_error=True)

_jwks_client: PyJWKClient | None = None


def _get_jwks_client() -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        url = f"{SUPABASE_URL}/auth/v1/.well-known/jwks.json"
        _jwks_client = PyJWKClient(url, cache_keys=True, lifespan=600)
    return _jwks_client


def decode_supabase_token(token: str) -> Dict[str, Any]:
    if not SUPABASE_URL:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="SUPABASE_URL is not configured on the server.",
        )

    header = jwt.get_unverified_header(token)
    alg = header.get("alg")

    if alg == "HS256" and SUPABASE_JWT_SECRET:
        key = SUPABASE_JWT_SECRET
        algorithms = ["HS256"]
    elif alg in {"ES256", "RS256", "PS256", "EdDSA"}:
        signing_key = _get_jwks_client().get_signing_key_from_jwt(token)
        key = signing_key.key
        algorithms = [alg]
    else:
        raise InvalidTokenError(f"Unsupported alg {alg!r}")

    return jwt.decode(
        token,
        key=key,
        algorithms=algorithms,
        audience=EXPECTED_AUDIENCE,
        issuer=EXPECTED_ISSUER,
        leeway=LEEWAY_SECONDS,
        options={
            "require": ["exp", "iat", "iss", "aud", "sub"],
            "verify_signature": True,
            "verify_exp": True,
            "verify_aud": True,
            "verify_iss": True,
        },
    )


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> Dict[str, Any]:
    token = credentials.credentials
    try:
        claims = decode_supabase_token(token)
    except ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except (InvalidAudienceError, InvalidIssuerError) as exc:
        logger.warning("JWT claim validation failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token audience/issuer is not valid for this service.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except (MissingRequiredClaimError, DecodeError, InvalidTokenError, PyJWKError) as exc:
        logger.warning("Invalid Supabase JWT: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if claims.get("role") != "authenticated":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This endpoint requires an authenticated user token.",
        )

    return claims


def get_current_user_id(user: Dict[str, Any] = Depends(get_current_user)) -> str:
    return user["sub"]