"""Configuração do Sistema de Auxílio para Eventos Acadêmicos (IFAM)."""

import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _env(nome, padrao=""):
    return os.environ.get(nome, padrao)


def _bool(nome, padrao=False):
    return _env(nome, "1" if padrao else "0").strip().lower() in ("1", "true", "sim", "yes")


# Tudo que muda entre a sua máquina e o servidor vem de variáveis de ambiente (veja .env.example).
EM_TESTE = "test" in sys.argv
DEBUG = _bool("DJANGO_DEBUG", False)
SECRET_KEY = _env("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    if not (DEBUG or EM_TESTE):
        raise RuntimeError("Defina DJANGO_SECRET_KEY (veja o README).")
    SECRET_KEY = "somente-para-desenvolvimento"
ALLOWED_HOSTS = [h.strip() for h in _env("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()]
CSRF_TRUSTED_ORIGINS = [o.strip() for o in _env("DJANGO_CSRF_TRUSTED_ORIGINS").split(",") if o.strip()]

# Pasta onde ficam o banco (SQLite) e os arquivos enviados. Faça backup dela.
DATA_DIR = Path(_env("DJANGO_DATA_DIR", str(BASE_DIR / "dados")))
DATA_DIR.mkdir(parents=True, exist_ok=True)

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "auxilio",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "auxilio.middleware.VoltarAoItem",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": DATA_DIR / "db.sqlite3",
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
]

LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Manaus"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_ROOT = DATA_DIR / "media"  # arquivos servidos apenas por views com checagem de acesso
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "entrar"
LOGIN_REDIRECT_URL = "inicio"
LOGOUT_REDIRECT_URL = "entrar"

FILE_UPLOAD_MAX_MEMORY_SIZE = 20 * 1024 * 1024

# Pasta com os modelos oficiais do edital (docx dos anexos e PDF do requerimento).
MODELOS_DIR = BASE_DIR / "modelos"

# Se verdadeiro, o upload de um PDF sem assinatura digital detectável é recusado.
# Padrão falso: a conferência é aproximada, então o sistema aceita e mostra um alerta.
ASSINATURA_ESTRITA = _bool("ASSINATURA_ESTRITA", False)

if not (DEBUG or EM_TESTE):
    # Os dados dos estudantes (CPF, RG, endereço) só devem trafegar por HTTPS.
    SECURE_SSL_REDIRECT = _bool("DJANGO_FORCAR_HTTPS", True)
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = SECURE_SSL_REDIRECT
    CSRF_COOKIE_SECURE = SECURE_SSL_REDIRECT
    SECURE_HSTS_SECONDS = 31536000 if SECURE_SSL_REDIRECT else 0
