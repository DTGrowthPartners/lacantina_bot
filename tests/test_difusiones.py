from app.difusiones import normalizar_etiqueta_difusion, render_mensaje_difusion


def test_render_mensaje_difusion_personaliza_nombre():
    mensaje = render_mensaje_difusion(
        "Hola {primer_nombre}, te esperamos en La Cantina. Ref {numero}.",
        nombre="Laura Martinez",
        numero="+573001112233",
    )

    assert mensaje == "Hola Laura, te esperamos en La Cantina. Ref +573001112233."


def test_render_mensaje_difusion_deja_variables_desconocidas():
    mensaje = render_mensaje_difusion("Hola {nombre}, {evento}", nombre="", numero="+57")

    assert mensaje == "Hola parce, {evento}"


def test_normalizar_etiqueta_difusion_es_conservadora():
    assert normalizar_etiqueta_difusion("cliente") == "cliente"
    assert normalizar_etiqueta_difusion("personal") == "todos"
    assert normalizar_etiqueta_difusion("") == "todos"
