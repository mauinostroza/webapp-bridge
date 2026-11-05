"""Start the Windows-only SAP2000 loopback bridge."""

from __future__ import annotations

import os
import secrets
import sys
import threading


def _run_linux(token: str) -> None:
    """Comportamiento actual, sin bandeja: imprime el token y bloquea con uvicorn."""
    print(f"SAP2000_AGENT_TOKEN={token}", flush=True)
    try:
        import uvicorn

        from bridge.sap2000_agent import app
    except Exception as exc:
        raise SystemExit(f"No se pudo iniciar el puente SAP2000: {exc}") from exc
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="info")


def open_logs_folder() -> None:
    """Entrada "Abrir carpeta de registros" de la bandeja (solo Windows)."""
    import runtime_paths

    folder = runtime_paths.logs_dir("EngineeringWorkbench")
    folder.mkdir(parents=True, exist_ok=True)
    os.startfile(folder)  # type: ignore[attr-defined]  # noqa: S606


def _run_windows(token: str, extra_menu_items=None, on_exit_extra=None) -> None:
    """Servidor uvicorn en un hilo daemon + bandeja del sistema en el hilo principal.

    Mismo patrón que ``run_workbench.py`` usa para su propio servidor: el
    hilo de la interfaz (aquí, ``TrayApp.run()`` bombeando mensajes de
    Windows) es el que bloquea al final; el de uvicorn corre en segundo
    plano y se detiene al salir.
    """
    import uvicorn

    from bridge import agent_file, pairing, tray
    from bridge.sap2000_agent import app, session

    # Antes de crear cualquier ventana: sin esto Windows escala (y desenfoca)
    # las ventanas de la bandeja en pantallas de alta densidad.
    tray.enable_dpi_awareness()

    port = 8765
    agent_file.write_agent_file(port=port, token=token)

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="info"))
    server_thread = threading.Thread(target=server.run, name="sap-bridge-server", daemon=True)
    server_thread.start()

    def on_revoke(pairing_id: str) -> None:
        pairing.revoke(pairing_id)

    def on_exit() -> None:
        server.should_exit = True
        server_thread.join(timeout=5)
        agent_file.remove_agent_file()
        if on_exit_extra is not None:
            on_exit_extra()

    tray_app = tray.TrayApp(
        port=port,
        get_status=session.status,
        get_pairings=pairing.list_pairings,
        on_revoke=on_revoke,
        on_exit=on_exit,
        extra_menu_items=extra_menu_items,
        on_open_logs=open_logs_folder,
    )
    pairing.set_notifier(tray_app.notify_pairing_request)
    # Banner de arranque: no bloquea y se cierra al escuchar el servidor (o a los 2,5 s).
    tray_app.show_splash(is_ready=lambda: bool(server.started))

    try:
        tray_app.run()
    finally:
        # Cubre también el caso de que la bandeja se cierre por otra vía
        # (p. ej. WM_DESTROY de la ventana oculta) sin pasar por "Salir".
        if server_thread.is_alive():
            on_exit()


def default_origins(workbench_port: str) -> str:
    """Web local de este equipo, la web alojada y las aplicaciones que comparten el
    puente (SAP Graph, Viewer, Placa Base): SAP2000_AGENT_ORIGINS reemplaza los
    orígenes por defecto del agente, así que si falta alguno el navegador lo
    rechaza por CORS al emparejar."""
    from bridge.hosted import FUSION_ORIGIN, HOSTED_ORIGIN, SHARED_APP_ORIGINS

    origins = [
        f"http://127.0.0.1:{workbench_port}",
        f"http://localhost:{workbench_port}",
        HOSTED_ORIGIN,
        FUSION_ORIGIN,
    ]
    return ",".join([*origins, *SHARED_APP_ORIGINS])


def main() -> None:
    # A --windowed PyInstaller build has no console: sys.stdout/sys.stderr are None
    # (not closed streams), which crashes uvicorn's default logging setup the moment
    # it touches stream.isatty(). Give it real (discarded) streams before anything
    # else runs.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")

    import runtime_paths

    runtime_paths.configure_file_logging("sap2000_bridge")

    # Set the token before importing sap2000_agent: that module validates it at import time.
    token = os.environ.get("SAP2000_AGENT_TOKEN")
    if not token:
        token = secrets.token_urlsafe(32)
        os.environ["SAP2000_AGENT_TOKEN"] = token
    workbench_port = os.environ.get("WORKBENCH_PORT", "8000")
    os.environ.setdefault("SAP2000_AGENT_ORIGINS", default_origins(workbench_port))

    if sys.platform == "win32":
        _run_windows(token)
    else:
        _run_linux(token)


if __name__ == "__main__":
    main()
