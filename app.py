import os
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

import pyodbc
from flask import Flask, request, jsonify
import xml.etree.ElementTree as ET
from xml.dom import minidom
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

IVA_PORCENTAJE = Decimal("0.15")

# =====================================================
# Conexión a SQL Server (LocalDB por defecto)
# Se puede cambiar con variables de entorno:
#   DB_SERVER, DB_DATABASE, DB_UID, DB_PWD, DB_DRIVER
# Si no hay DB_UID se usa autenticación de Windows.
# =====================================================
DB_DRIVER = os.environ.get("DB_DRIVER", "ODBC Driver 17 for SQL Server")
DB_SERVER = os.environ.get("DB_SERVER", r"(localdb)\MSSQLLocalDB")
DB_DATABASE = os.environ.get("DB_DATABASE", "AppDistribuidas_2026_DB")
DB_UID = os.environ.get("DB_UID")
DB_PWD = os.environ.get("DB_PWD")


def get_conexion():
    cadena = (
        f"DRIVER={{{DB_DRIVER}}};"
        f"SERVER={DB_SERVER};"
        f"DATABASE={DB_DATABASE};"
        "TrustServerCertificate=yes;"
    )
    if DB_UID:
        cadena += f"UID={DB_UID};PWD={DB_PWD or ''};"
    else:
        cadena += "Trusted_Connection=yes;"
    return pyodbc.connect(cadena, autocommit=False)


def version_a_hex(valor):
    """rowversion (bytes) -> '0x0000000000000FA1'"""
    return "0x" + bytes(valor).hex().upper() if valor is not None else ""


def hex_a_version(texto):
    """'0x0000000000000FA1' -> bytes para comparar con la columna Versions"""
    texto = (texto or "").strip()
    if texto.lower().startswith("0x"):
        texto = texto[2:]
    return bytes.fromhex(texto)


def fila_a_producto(fila):
    return {
        "id": fila.ID,
        "nombre": fila.NAMES or "",
        "precio": float(fila.PRICE or 0),
        "stock": int(fila.STOCK or 0),
        "descripcion": fila.Description or "",
        "imagen_url": fila.ImageUrl or "",
        "version": version_a_hex(fila.Versions),
    }


SELECT_PRODUCTOS = """
    SELECT ID, NAMES, PRICE, STOCK, Description, ImageUrl, IsActive, Versions
    FROM dbo.PRODUCTS
"""


@app.route("/")
def inicio():
    return "Servicio de Facturación TechStore 360 funcionando"


@app.route("/test-db", methods=["GET"])
def test_db():
    try:
        conn = get_conexion()
        nombre = conn.cursor().execute("SELECT DB_NAME()").fetchone()[0]
        conn.close()
        return jsonify({"mensaje": "Conexión exitosa", "base_datos": nombre}), 200
    except Exception as e:
        return jsonify({"mensaje": "Error de conexión", "error": str(e)}), 500


@app.route("/api/productos", methods=["GET"])
def obtener_productos():
    try:
        conn = get_conexion()
        cursor = conn.cursor()
        cursor.execute(SELECT_PRODUCTOS + " WHERE IsActive = 1 ORDER BY ID")
        productos = [fila_a_producto(f) for f in cursor.fetchall()]
        conn.close()
        return jsonify(productos), 200
    except Exception as e:
        return jsonify({"error": "No se pudo leer la base de datos", "detalle": str(e)}), 500


# =====================================================
# Validaciones
# =====================================================
def validar_datos(datos):
    errores = []
    if datos is None:
        return [{"campo": "body", "error": "No se recibió un JSON válido."}]

    if not datos.get("fecha"):
        errores.append({"campo": "fecha", "error": "La fecha es obligatoria."})

    cliente = datos.get("cliente")
    if not isinstance(cliente, dict):
        errores.append({"campo": "cliente", "error": "Los datos del cliente son obligatorios."})
    else:
        for campo, texto in [
            ("nombre", "El nombre del cliente es obligatorio."),
            ("cedula_ruc", "La cédula o RUC es obligatorio."),
            ("correo", "El correo es obligatorio."),
            ("direccion", "La dirección es obligatoria."),
        ]:
            if not cliente.get(campo):
                errores.append({"campo": f"cliente.{campo}", "error": texto})

    productos = datos.get("productos")
    if not isinstance(productos, list) or len(productos) == 0:
        errores.append({"campo": "productos", "error": "Debe existir al menos un producto."})
        return errores

    for i, producto in enumerate(productos, start=1):
        if not isinstance(producto, dict):
            errores.append({"campo": f"productos[{i}]", "error": "El producto debe ser un objeto JSON."})
            continue
        if not isinstance(producto.get("id"), int):
            errores.append({"campo": f"productos[{i}].id", "error": "El id del producto es obligatorio."})
        try:
            if int(producto.get("cantidad", 0)) <= 0:
                errores.append({"campo": f"productos[{i}].cantidad",
                                "error": "La cantidad debe ser mayor a 0."})
        except (ValueError, TypeError):
            errores.append({"campo": f"productos[{i}].cantidad", "error": "La cantidad debe ser numérica."})
    return errores


# =====================================================
# XML
# =====================================================
def formatear_xml(elemento):
    xml_sin_formato = ET.tostring(elemento, encoding="utf-8")
    return minidom.parseString(xml_sin_formato).toprettyxml(indent="    ")


def crear_factura_xml(factura_dict):
    factura = ET.Element("Factura")
    cabecera = ET.SubElement(factura, "Cabecera")
    ET.SubElement(cabecera, "Numero").text = factura_dict["numero"]
    ET.SubElement(cabecera, "Fecha").text = factura_dict["fecha"]

    c = factura_dict["cliente"]
    cliente = ET.SubElement(factura, "Cliente")
    ET.SubElement(cliente, "Nombre").text = c["nombre"]
    ET.SubElement(cliente, "CedulaRUC").text = c["cedula_ruc"]
    ET.SubElement(cliente, "Correo").text = c["correo"]
    ET.SubElement(cliente, "Direccion").text = c["direccion"]

    detalle = ET.SubElement(factura, "Detalle")
    for item in factura_dict["productos"]:
        p = ET.SubElement(detalle, "Producto")
        ET.SubElement(p, "Id").text = str(item["id"])
        ET.SubElement(p, "Nombre").text = item["nombre"]
        ET.SubElement(p, "ImagenUrl").text = item["imagen_url"]
        ET.SubElement(p, "Cantidad").text = str(item["cantidad"])
        ET.SubElement(p, "PrecioUnitario").text = f"{item['precio_unitario']:.2f}"
        ET.SubElement(p, "Subtotal").text = f"{item['subtotal']:.2f}"

    t = factura_dict["totales"]
    totales = ET.SubElement(factura, "Totales")
    ET.SubElement(totales, "Subtotal").text = f"{t['subtotal']:.2f}"
    ET.SubElement(totales, "IVA").text = f"{t['iva']:.2f}"
    ET.SubElement(totales, "Total").text = f"{t['total']:.2f}"
    ET.SubElement(factura, "Estado").text = "Generada"

    # Guardar el XML en disco (un archivo por factura)
    carpeta = os.path.join(os.path.dirname(os.path.abspath(__file__)), "facturas")
    os.makedirs(carpeta, exist_ok=True)
    ET.ElementTree(factura).write(
        os.path.join(carpeta, f"{factura_dict['numero']}.xml"),
        encoding="UTF-8", xml_declaration=True,
    )
    return formatear_xml(factura)


def dos_decimales(valor):
    return valor.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# =====================================================
# Facturación con CONTROL DE CONCURRENCIA
# 1. Se bloquea la fila del producto (UPDLOCK): si dos clientes compran el
#    mismo producto al mismo tiempo, el segundo ESPERA a que el primero
#    termine y luego ve el stock ya descontado.
# 2. Se valida el stock real: si no alcanza -> 409 "ya no hay stock" y
#    ROLLBACK de toda la factura.
# 3. El UPDATE usa la columna Versions (rowversion) como guardia: solo
#    descuenta si nadie modificó la fila entre la lectura y la escritura.
# =====================================================
def respuesta_conflicto(motivo, mensaje, producto):
    return jsonify({"error": mensaje, "motivo": motivo, "producto": producto}), 409


@app.route("/api/facturas", methods=["POST"])
def generar_factura():
    datos = request.get_json(silent=True)
    errores = validar_datos(datos)
    if errores:
        return jsonify({"errores": errores}), 400

    # Agrupar por id por si el mismo producto viene repetido
    pedidos = {}
    for p in datos["productos"]:
        pid = p["id"]
        pedidos.setdefault(pid, {"cantidad": 0, "version": p.get("version", "")})
        pedidos[pid]["cantidad"] += int(p["cantidad"])

    conn = None
    try:
        conn = get_conexion()
        cursor = conn.cursor()
        lineas = []
        subtotal = Decimal("0")

        # Orden fijo de bloqueo (por ID) para evitar interbloqueos
        for pid in sorted(pedidos):
            cantidad = pedidos[pid]["cantidad"]

            actual = cursor.execute(
                SELECT_PRODUCTOS.replace("dbo.PRODUCTS", "dbo.PRODUCTS WITH (UPDLOCK, ROWLOCK)")
                + " WHERE ID = ?",
                pid,
            ).fetchone()

            if actual is None or not getattr(actual, "IsActive", True):
                conn.rollback()
                return respuesta_conflicto(
                    "no_existe", f"El producto {pid} ya no está disponible.", None)

            producto_actual = fila_a_producto(actual)
            if producto_actual["stock"] < cantidad:
                conn.rollback()
                if producto_actual["stock"] == 0:
                    mensaje = f"Ya no hay stock de '{producto_actual['nombre']}'. Otro cliente lo compró."
                else:
                    mensaje = (f"Ya no hay stock suficiente de '{producto_actual['nombre']}': "
                               f"pidió {cantidad}, quedan {producto_actual['stock']}.")
                return respuesta_conflicto("stock", mensaje, producto_actual)

            cursor.execute(
                """
                UPDATE dbo.PRODUCTS
                   SET STOCK = STOCK - ?
                OUTPUT inserted.ID, inserted.NAMES, inserted.PRICE, inserted.STOCK,
                       inserted.Description, inserted.ImageUrl, inserted.Versions
                 WHERE ID = ?
                   AND Versions = ?
                   AND STOCK >= ?
                """,
                cantidad, pid, actual.Versions, cantidad,
            )
            fila = cursor.fetchone()
            if fila is None:
                conn.rollback()
                return respuesta_conflicto(
                    "concurrencia",
                    f"'{producto_actual['nombre']}' fue modificado por otro usuario. Intente de nuevo.",
                    producto_actual)

            precio = Decimal(fila.PRICE or 0)
            sub = dos_decimales(precio * cantidad)
            subtotal += sub
            lineas.append({
                "id": fila.ID,
                "nombre": fila.NAMES or "",
                "descripcion": fila.Description or "",
                "imagen_url": fila.ImageUrl or "",
                "cantidad": cantidad,
                "precio_unitario": float(precio),
                "subtotal": float(sub),
                "stock_restante": int(fila.STOCK),
                "nueva_version": version_a_hex(fila.Versions),
            })

        conn.commit()
    except pyodbc.Error as e:
        if conn is not None:
            conn.rollback()
        return jsonify({"error": "Error de base de datos", "detalle": str(e)}), 500
    finally:
        if conn is not None:
            conn.close()

    iva = dos_decimales(subtotal * IVA_PORCENTAJE)
    total = subtotal + iva
    ahora = datetime.now()
    factura = {
        "numero": datos.get("numero") or f"F001-{ahora:%Y%m%d%H%M%S%f}"[:-3],
        "fecha": datos["fecha"],
        "cliente": datos["cliente"],
        "productos": lineas,
        "totales": {"subtotal": float(subtotal), "iva": float(iva), "total": float(total)},
        "estado": "Generada",
    }
    factura["xml"] = crear_factura_xml(factura)
    return jsonify(factura), 200


if __name__ == "__main__":
    # threaded=True para atender a los dos clientes a la vez
    app.run(host="0.0.0.0", port=5000, debug=True, threaded=True)
