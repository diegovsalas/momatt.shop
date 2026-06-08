# config_pagos.py
# -------------------------------------------------------------------
# AQUÍ VAN TUS DATOS DE PAGO. Edita los dos bloques de abajo.
#
# 1) BANREGIO  -> transferencia directa a tu cuenta (sin comisión).
#                 Solo son los datos que el cliente ve para pagarte.
# 2) OPENPAY   -> SPEI automático. Necesita llaves de tu dashboard.
#
# Por seguridad, las llaves de Openpay se leen de variables de entorno
# si existen; si no, usa los valores que pongas aquí.
# -------------------------------------------------------------------
import os


# --- 1) Banregio (transferencia directa) ---
# Cambia estos datos por los TUYOS reales. No requiere API ni llaves.
BANREGIO = {
    "titular": "Outlet Momatt México",          # <-- nombre del titular de la cuenta
    "banco": "Banregio",
    "clabe": "058000000000000000",              # <-- TU CLABE real (18 dígitos)
    "cuenta": "0000000000",                     # <-- número de cuenta (opcional)
    "whatsapp": "+52 81 0000 0000",             # <-- a dónde mandan el comprobante
    "correo": "ventas@outletmomatt.com",        # <-- correo para el comprobante
}


# --- 2) Openpay (SPEI automático) ---
# Pon tus llaves aquí O pásalas como variables de entorno (recomendado):
#   OPENPAY_MERCHANT_ID, OPENPAY_PRIVATE_KEY, OPENPAY_PRODUCCION
OPENPAY_MERCHANT_ID = os.getenv("OPENPAY_MERCHANT_ID", "")   # <-- Merchant ID
OPENPAY_PRIVATE_KEY = os.getenv("OPENPAY_PRIVATE_KEY", "")   # <-- Llave privada (sk_...)

# False = ambiente de PRUEBAS (sandbox). True = cobros REALES.
OPENPAY_PRODUCCION = os.getenv("OPENPAY_PRODUCCION", "False").lower() == "true"
