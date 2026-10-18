"""Explicit deployment configuration; no credentials are returned by APIs."""
import os
import json
import ipaddress
from dataclasses import dataclass
from urllib.parse import urlsplit
from uuid import UUID
from sqlalchemy.engine import URL


@dataclass
class Settings:
    database_url: str
    auth_mode: str
    app_url: str
    session_secret: str
    allow_live_models: bool
    demo_seed: bool
    core_issuer: str = ""
    core_browser_client_id: str = ""
    core_resource_client_id: str = ""
    core_resource_client_secret: str = ""
    core_organization_id: str = ""
    core_service_id: str = ""
    core_deployment_id: str = ""
    core_environment: str = "production"

    @property
    def tenant_id(self):
        return self.core_organization_id if self.auth_mode == "core" else "local-academy"

    @classmethod
    def load(cls):
        mode = os.getenv("AUTH_MODE", "local")
        app_url = os.getenv("APP_URL", "http://localhost:8090").rstrip("/")
        secret = os.getenv("SESSION_SECRET", "local-demo-only-change-before-production")
        db_url = os.getenv("DATABASE_URL", "")
        if not db_url and os.getenv("DB_HOST"):
            db_url = URL.create("postgresql+psycopg", username=os.getenv("DB_USERNAME"), password=os.getenv("DB_PASSWORD"), host=os.environ["DB_HOST"], port=int(os.getenv("DB_PORT", "5432")), database=os.getenv("DB_NAME", "academy"), query={"sslmode": os.getenv("DB_SSLMODE", "verify-full"), "sslrootcert": os.getenv("DB_SSLROOTCERT", "/etc/ssl/certs/rds-global-bundle.pem")}).render_as_string(hide_password=False)
        db_url = db_url or "sqlite:///./var/academy.db"
        bundle = os.getenv("CORE_RESOURCE_CREDENTIALS_JSON", "")
        creds = json.loads(bundle) if bundle else {}
        if bundle and (set(creds) != {"client_id", "client_secret"} or any(os.getenv(k) for k in ("CORE_RESOURCE_CLIENT_ID", "CORE_RESOURCE_CLIENT_SECRET"))):
            raise ValueError("Conflicting or malformed Core resource credentials")
        value = cls(db_url, mode, app_url, secret, os.getenv("ALLOW_LIVE_MODELS", "false").lower() == "true", os.getenv("DEMO_SEED", "true").lower() == "true", **{field: os.getenv(field.upper(), "production" if field == "core_environment" else "").rstrip("/") for field in ("core_issuer", "core_browser_client_id", "core_organization_id", "core_service_id", "core_deployment_id", "core_environment")}, core_resource_client_id=creds.get("client_id", os.getenv("CORE_RESOURCE_CLIENT_ID", "")), core_resource_client_secret=creds.get("client_secret", os.getenv("CORE_RESOURCE_CLIENT_SECRET", "")))
        origin = urlsplit(app_url)
        if mode not in {"local", "core"} or len(secret) < 32 or origin.path or origin.query or origin.fragment or not origin.hostname or origin.scheme not in {"http", "https"} or origin.username is not None or origin.password is not None:
            raise ValueError("Invalid authentication configuration")
        if mode == "local":
            try:
                loopback = origin.hostname == "localhost" or ipaddress.ip_address(origin.hostname).is_loopback
            except ValueError:
                loopback = False
            if not loopback or value.allow_live_models:
                raise ValueError("Local demo authentication requires a loopback application origin and disabled live models")
        else:
            if origin.scheme != "https" or secret == "local-demo-only-change-before-production" or not db_url.startswith("postgresql"):
                raise ValueError("Core mode requires HTTPS, an external session secret and PostgreSQL")
            issuer = urlsplit(value.core_issuer)
            if issuer.scheme != "https" or not issuer.hostname or issuer.path or issuer.query or issuer.fragment or issuer.username is not None or issuer.password is not None:
                raise ValueError("Core issuer must be an exact HTTPS origin")
            for name in ("core_organization_id", "core_service_id", "core_deployment_id"):
                if str(UUID(getattr(value, name))) != getattr(value, name):
                    raise ValueError("Core registration IDs must be canonical UUIDs")
            if not all((value.core_browser_client_id, value.core_resource_client_id, value.core_resource_client_secret)):
                raise ValueError("Complete Core clients are required")
            value.demo_seed = False
        return value
