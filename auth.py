# auth.py — Hashing de passwords y helpers de sesión
# -------------------------------------------------------------------
# - bcrypt (librería nativa) para hashes — sin passlib porque su
#   versión 1.7.4 tiene incompatibilidad con bcrypt 4.x.
# - El usuario logueado se identifica por user_id guardado en la sesión
#   (cookie firmada con SESSION_SECRET_KEY).
# - current_user(request) devuelve el dict del usuario o None.
# -------------------------------------------------------------------
import bcrypt

import db


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def login_session(request, usuario: dict) -> None:
    """Guarda el id del usuario en la sesión."""
    request.session["user_id"] = usuario["id"]


def logout_session(request) -> None:
    request.session.pop("user_id", None)


def current_user(request):
    """Devuelve el dict del usuario logueado, o None si es invitado."""
    uid = request.session.get("user_id")
    if not uid or not db.disponible():
        return None
    return db.buscar_usuario_por_id(uid)
