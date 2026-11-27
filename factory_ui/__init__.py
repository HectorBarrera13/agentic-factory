"""Factory Studio: UI local tipo IDE para la fabrica (codigo fuente + factory).

Backend FastAPI que reutiliza `factory_app` (servicios y orquestador): no duplica la logica
de gates ni de runs. Solo escucha en 127.0.0.1 y exige un token por sesion.
"""
