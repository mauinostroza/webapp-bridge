# SAP2000 Windows agent

The agent has been intentionally limited to loopback and requires
`SAP2000_AGENT_TOKEN`. It attaches to an already open SAP2000 instance through
COM in one STA thread. SAP2000 OAPI access can only be validated on Windows
with SAP2000 installed and API access enabled; that validation is not possible
in this Linux workspace.

Configure the main backend with the same `SAP2000_AGENT_URL` and
`SAP2000_AGENT_TOKEN`. The only mutation is `POST /v1/sap/analysis/run`; model
and status endpoints are read-only.

## Cómo se conecta la web al puente

En la distribución portable para Windows, el puente ya no imprime un token en
consola para copiarlo a mano: `run_sap_bridge.py` arranca un icono en la
bandeja del sistema (`bridge/tray.py`) y no abre ninguna ventana.

- **Modo local** (la web corre en el mismo equipo que el puente): el puente
  escribe sus credenciales en un archivo bajo la carpeta de datos del usuario
  (`bridge/agent_file.py`); la web las lee sola vía
  `GET /sap/local-credentials` y no hace falta ninguna acción manual.
- **Modo servidor remoto** (la web corre en otro equipo o servidor): la
  primera vez, la web pide emparejarse (`POST /v1/sap/pairing/request`) y en
  la bandeja del sistema aparece un aviso con el origen exacto de quien lo
  pidió; una persona debe aceptarlo o rechazarlo con un clic en ese icono. Tras
  aceptar, la web recibe un secreto de emparejamiento (mediante
  `GET /v1/sap/pairing/poll/{request_id}`) que guarda en `localStorage` del
  propio navegador y reutiliza en adelante sin volver a preguntar.

### Rutas de emparejamiento

Ambas quedan exentas del middleware `require_token` — exigir ya un token para
pedir el primer secreto sería circular. El resto de rutas del agente aceptan
tanto `SAP2000_AGENT_TOKEN` como un secreto de emparejamiento válido.

| Ruta | Efecto |
|---|---|
| `POST /v1/sap/pairing/request` | Sin cuerpo. Lee el header `Origin`; si no está en `SAP2000_AGENT_ORIGINS`, responde `403`. Si lo está, crea una solicitud pendiente (vence a los 120 s) y dispara el aviso en la bandeja. Devuelve `{ "request_id", "expires_in": 120 }`. Como mucho una solicitud pendiente por origen (una segunda del mismo origen reemplaza a la anterior) y como mucho 5 pendientes en total entre todos los orígenes; superarlo responde `429`. |
| `GET /v1/sap/pairing/poll/{request_id}` | Sin cuerpo. La web hace polling (~1.5 s) sin token. Responde `404` si el id no existe. Si existe, `{ "status": "pending" \| "denied" \| "expired" }`, o — sólo la primera vez tras aceptar en la bandeja — `{ "status": "approved", "secret", "url" }`; en polls siguientes el secreto ya no se repite, sólo `{ "status": "approved" }`. |

La aceptación o el rechazo ocurren dentro del propio proceso del puente (la
ventana de la bandeja llama directamente a `pairing.approve`/`pairing.deny`);
no existe ningún endpoint HTTP para aprobar, precisamente para reducir la
superficie de ataque a un clic humano local.
