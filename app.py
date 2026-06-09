# app.py — Backend FastAPI con Openpay (SPEI) + Banregio (transferencia directa)
# -------------------------------------------------------------------
# Flujo de pago (distinto a Stripe: el cliente NO sale de tu sitio):
#   1. Cliente arma su carrito.
#   2. En /checkout llena un formulario corto (nombre, correo) y elige método.
#   3a. Si elige SPEI Openpay  -> creamos un "cargo bank_account" y Openpay
#       nos devuelve una CLABE + referencia que le mostramos al cliente.
#   3b. Si elige Banregio      -> le mostramos TU CLABE de Banregio y las
#       instrucciones para que transfiera y mande comprobante.
# -------------------------------------------------------------------
import os
import secrets
import requests
from requests.auth import HTTPBasicAuth
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

import catalogo
import config_pagos as cfg
import seo

# Llave para firmar las cookies de sesión. En producción DEBE venir de
# SESSION_SECRET_KEY (env var). Si falta, generamos una aleatoria en
# memoria: las sesiones existentes se invalidan al reiniciar el proceso.
SESSION_SECRET_KEY = os.getenv("SESSION_SECRET_KEY")
if not SESSION_SECRET_KEY:
    SESSION_SECRET_KEY = secrets.token_urlsafe(32)
    print("⚠  SESSION_SECRET_KEY no está configurada. Usando una llave aleatoria en memoria — "
          "las sesiones se invalidan al reiniciar. Configúrala como env var en producción.")

app = FastAPI(title="Tienda de Patines Hidraulicos")
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET_KEY)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# --- Configurar Openpay (API REST) desde config_pagos.py ---
# El SDK oficial de Python está abandonado (no instala en Python 3.12),
# así que llamamos la API REST directo con requests. Es más limpio.
OPENPAY_BASE = (
    "https://api.openpay.mx/v1" if cfg.OPENPAY_PRODUCCION
    else "https://sandbox-api.openpay.mx/v1"
)
OPENPAY_AUTH = HTTPBasicAuth(cfg.OPENPAY_PRIVATE_KEY, "")  # llave privada como usuario, sin password


# --- Helpers del carrito (viven en la sesion como {producto_id: cantidad}) ---
def obtener_carrito(request):
    return request.session.get("carrito", {})

def guardar_carrito(request, carrito):
    request.session["carrito"] = carrito

IVA = 0.16  # 16% IVA en México

def carrito_detallado(carrito):
    """Devuelve los items, subtotal (sin IVA), IVA y total (con IVA)."""
    items, subtotal = [], 0
    for pid, cant in carrito.items():
        p = catalogo.buscar_producto(pid)
        if not p or p["precio"] is None:
            continue
        sub = p["precio"] * cant
        subtotal += sub
        items.append({"producto": p, "cantidad": cant, "subtotal": sub})
    iva = round(subtotal * IVA)
    total = subtotal + iva
    return items, subtotal, iva, total


# --- RUTAS ---
@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    carrito = obtener_carrito(request)
    return templates.TemplateResponse(request, "index.html", {
        "productos": catalogo.listar_productos(),
        "categorias": catalogo.categorias(),
        "total_items": sum(carrito.values()),
        "meta": seo.meta_home(),
        "schema_org": seo.schema_organizacion(),
        "sitio": seo.SITIO,
    })


@app.get("/producto/{producto_id}", response_class=HTMLResponse)
def pagina_producto(request: Request, producto_id: str):
    """Página individual de producto — clave para SEO y conversión."""
    p = catalogo.buscar_producto(producto_id)
    if not p:
        return RedirectResponse(url="/", status_code=303)
    carrito = obtener_carrito(request)
    # Productos relacionados: misma categoría, excluyendo el actual.
    relacionados = [
        x for x in catalogo.listar_productos()
        if x["categoria"] == p["categoria"] and x["id"] != p["id"]
    ][:3]
    return templates.TemplateResponse(request, "producto.html", {
        "p": p,
        "relacionados": relacionados,
        "total_items": sum(carrito.values()),
        "meta": seo.meta_producto(p),
        "schema_producto": seo.schema_producto(p),
        "schema_breadcrumb": seo.schema_breadcrumb([
            ("Inicio", "/"),
            (p["categoria"], "/"),
            (p["nombre"], f"/producto/{p['id']}"),
        ]),
        "sitio": seo.SITIO,
        "keywords_cat": seo.KEYWORDS_LONG_TAIL.get(p["categoria"], []),
    })

@app.post("/agregar")
def agregar(request: Request, producto_id: str = Form(...)):
    carrito = obtener_carrito(request)
    carrito[producto_id] = carrito.get(producto_id, 0) + 1
    guardar_carrito(request, carrito)
    return RedirectResponse(url="/", status_code=303)

@app.get("/carrito", response_class=HTMLResponse)
def ver_carrito(request: Request):
    items, subtotal, iva, total = carrito_detallado(obtener_carrito(request))
    return templates.TemplateResponse(request, "carrito.html", {
        "items": items, "subtotal": subtotal, "iva": iva, "total": total,
        "sitio": seo.SITIO})

@app.post("/eliminar")
def eliminar(request: Request, producto_id: str = Form(...)):
    carrito = obtener_carrito(request)
    carrito.pop(producto_id, None)
    guardar_carrito(request, carrito)
    return RedirectResponse(url="/carrito", status_code=303)


@app.get("/checkout", response_class=HTMLResponse)
def checkout_form(request: Request):
    """Muestra el formulario de datos del cliente + elección de método."""
    items, subtotal, iva, total = carrito_detallado(obtener_carrito(request))
    if not items:
        return RedirectResponse(url="/carrito", status_code=303)
    return templates.TemplateResponse(request, "checkout.html", {
        "items": items, "subtotal": subtotal, "iva": iva, "total": total,
        "sitio": seo.SITIO})


@app.post("/pagar", response_class=HTMLResponse)
def procesar_pago(
    request: Request,
    nombre: str = Form(...),
    correo: str = Form(...),
    telefono: str = Form(""),
    metodo: str = Form(...),  # "spei" o "banregio"
):
    items, subtotal, iva, total = carrito_detallado(obtener_carrito(request))
    if not items:
        return RedirectResponse(url="/carrito", status_code=303)

    # Generamos un número de pedido simple basado en la sesión.
    import random
    pedido = "PED-" + str(random.randint(10000, 99999))

    if metodo == "banregio":
        # --- Transferencia directa: solo mostramos TUS datos. Sin API. ---
        guardar_carrito(request, {})
        return templates.TemplateResponse(request, "pago_banregio.html", {
            "banregio": cfg.BANREGIO,
            "total": total,
            "pedido": pedido,
            "nombre": nombre,
            "sitio": seo.SITIO,
        })

    elif metodo == "spei":
        # --- SPEI vía Openpay: creamos un cargo bank_account por API REST. ---
        if not cfg.OPENPAY_PRIVATE_KEY or not cfg.OPENPAY_MERCHANT_ID:
            return templates.TemplateResponse(request, "error_pago.html", {
                "mensaje": "Faltan las llaves de Openpay. Configúralas en config_pagos.py o como variables de entorno (OPENPAY_MERCHANT_ID y OPENPAY_PRIVATE_KEY)."})
        try:
            # POST a /{merchant_id}/charges con method=bank_account
            url = f"{OPENPAY_BASE}/{cfg.OPENPAY_MERCHANT_ID}/charges"
            payload = {
                "method": "bank_account",
                "amount": float(total),       # Openpay usa pesos con decimales
                "description": f"Pedido {pedido} - Outlet Momatt México",
                "order_id": pedido,
                "customer": {
                    "name": nombre,
                    "email": correo,
                    "phone_number": telefono or "0000000000",
                },
            }
            resp = requests.post(url, json=payload, auth=OPENPAY_AUTH,
                                 headers={"Content-Type": "application/json"},
                                 timeout=20)
            if resp.status_code not in (200, 201):
                return templates.TemplateResponse(request, "error_pago.html", {
                    "mensaje": f"Openpay respondió {resp.status_code}: {resp.text}"})

            data = resp.json()
            # Los datos de la transferencia vienen en payment_method.
            pm = data.get("payment_method", {})
            datos_spei = {
                "clabe": pm.get("clabe", ""),
                "banco": pm.get("bank", "") or pm.get("name", ""),
                "referencia": pm.get("reference", ""),
                "agreement": pm.get("agreement", ""),
            }
            guardar_carrito(request, {})
            return templates.TemplateResponse(request, "pago_spei.html", {
                "datos": datos_spei,
                "total": total,
                "pedido": pedido,
                "nombre": nombre,
            })
        except Exception as e:
            return templates.TemplateResponse(request, "error_pago.html", {
                "mensaje": f"Error al conectar con Openpay: {e}"})

    else:
        return RedirectResponse(url="/checkout", status_code=303)


# --- SEO técnico: sitemap y robots ---
from fastapi.responses import Response, PlainTextResponse

@app.get("/sitemap.xml")
def sitemap():
    """Mapa del sitio: lista todas las URLs para que Google las indexe."""
    base = seo.SITIO["dominio"]
    urls = [f"{base}/"]
    for p in catalogo.listar_productos():
        urls.append(f"{base}/producto/{p['id']}")
    items = "".join(
        f"<url><loc>{u}</loc><changefreq>weekly</changefreq><priority>0.8</priority></url>"
        for u in urls
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{items}</urlset>"
    )
    return Response(content=xml, media_type="application/xml")

@app.get("/robots.txt")
def robots():
    """Permite a los buscadores rastrear todo y apunta al sitemap."""
    contenido = (
        "User-agent: *\n"
        "Allow: /\n"
        f"Sitemap: {seo.SITIO['dominio']}/sitemap.xml\n"
    )
    return PlainTextResponse(content=contenido)
