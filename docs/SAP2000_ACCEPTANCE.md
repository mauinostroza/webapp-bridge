# Matriz de Aceptación del Puente COM SAP2000

## Introducción

Este documento es una lista de verificación manual de un solo uso para validar que el puente COM SAP2000Bridge funciona contra una instancia real de SAP2000. Hasta hoy, **ninguna línea del puente se ha ejecutado contra SAP2000 real**: todas las pruebas automatizadas usan dobles (mocks) de la API COM. Esta prueba es el último escalón antes de declarar el puente apto para producción.

Un ingeniero con SAP2000 instalado en su equipo Windows debe:

1. Tener el repositorio clonado y ejecutable localmente (véase `docs/DEPLOYMENT.md`).
2. Recorrer cada paso numerado abajo, verificando en la interfaz de SAP2000 que lo que aparece es lo que se esperaba.
3. Registrar exactamente qué falló (si algo falla) y qué versión de SAP2000 se usó.

Cada paso es autónomo: puedes saltar uno si su requisito no se cumple, pero no alteres el orden de los que sí completes. Cualquier fallo debe reportarse con la versión SAP2000 exacta (Ayuda → Acerca de SAP2000) y una copia del texto del error.

---

## Prerrequisitos

Antes de comenzar:

- **SAP2000 instalado en Windows:** versión ******\_\_****** (consúltala en Ayuda → Acerca de SAP2000; anótala aquí).
- **Un modelo abierto en SAP2000** con al menos:
  - Algunas barras (frame objects) definidas y seleccionables.
  - Algunos puntos (nodos) con coordenadas cartesianas.
  - Un caso de carga simple (p. ej. DEAD, LIVE) o un caso de análisis.
  - Preferiblemente, reacciones disponibles en al menos un caso (resultado de haber corrido análisis previo).
  - Opcionalmente: un espectro de respuesta ya definido (para comparación en el paso 9).

- **SAP2000Bridge en ejecución** en tu equipo Windows, emparejado con la web local:
  - Abre PowerShell, navega a `webapp_workbench` y ejecuta `python run_sap_bridge.py`.
  - Verifica que aparezca el mensaje de que el puente escucha en `http://127.0.0.1:8765` (o tu puerto configurado).
  - En la web (`http://127.0.0.1:5173`), ve al panel SAP y acepta la solicitud de emparejamiento cuando aparezca en la bandeja del sistema.
  - Una vez emparejado, verás "Puente emparejado" en la interfaz web.

- **Conexión establecida:**
  - En la web, haz clic en "Conectar a SAP2000" en el panel _Conexión local_.
  - SAP2000Bridge debería conectarse al modelo abierto. La interfaz web mostrará "Conectado".

---

## Pasos de Aceptación

### 1. Verificar estado del puente

**Qué hacer:**

- En el panel _Conexión local_, haz clic en "Consultar estado".

**Qué debería pasar:**

- La interfaz web muestra un indicador _Conectado_ (verde o equivalente).
- El estado debe incluir la ruta del archivo del modelo abierto en SAP2000.

**Verificación en SAP2000:**

- El modelo permanece abierto sin cambios.

**Resultado:**

- ☐ Pasó
- ☐ Falló

**Notas:** ************************************\_\_\_************************************

---

### 2. Leer catálogo de materiales y secciones

**Qué hacer:**

- Desde el panel _Perfiles_, el catálogo debería haberse cargado al conectar. Verifica que la interfaz web muestre desplegables para "Material en SAP2000" y "Sección existente a asignar" con opciones disponibles.
- Si los desplegables están vacíos, no continúes hasta resolver esto.

**Qué debería pasar:**

- Los desplegables muestran al menos un material (p. ej. A36, ASTM A36, etc.) y al menos una sección (p. ej. W310x33, C200x10, etc.).
- Estos nombres deben coincidir exactamente con los definidos en SAP2000 (Definir → Secciones, Definir → Materiales).

**Verificación en SAP2000:**

- Abre Definir → Materiales y verifica que al menos uno de los nombres mostrados en la web existe.
- Abre Definir → Secciones y verifica que al menos una de los nombres mostrados en la web existe.

**Resultado:**

- ☐ Pasó
- ☐ Falló

**Notas:** ************************************\_\_\_************************************

---

### 3. Leer barras seleccionadas

**Qué hacer:**

- En SAP2000, selecciona una o más barras (frame objects) con clic o con una ventana de selección (no selecciones nodos ni otros objetos por ahora).
- En el panel _Perfiles_ de la web, haz clic en "Leer barras seleccionadas".

**Qué debería pasar:**

- La interfaz web muestra una lista de nombres de barras (p. ej. F1, F2, B1, etc.) bajo "barras seleccionadas:".
- Los nombres deben coincidir exactamente con los que ves en SAP2000 (Selecciona → Mostrar etiquetas si no las ves).

**Verificación en SAP2000:**

- Cada nombre listado en la web debe estar seleccionado y visible en SAP2000 (aparecerá resaltado o en otro color de selección).

**Resultado:**

- ☐ Pasó
- ☐ Falló

**Notas:** ************************************\_\_\_************************************

---

### 4. Leer propiedades de barras seleccionadas (Esbelteces)

**Qué hacer:**

- Con las mismas barras aún seleccionadas en SAP2000, regresa a la web.
- Ve al módulo _Esbelteces_, sección "Desde SAP2000" (parte inferior del panel de carga de datos).
- Haz clic en "Importar barras seleccionadas de SAP2000".

**Qué debería pasar:**

- Una tabla aparece con una fila por barra, mostrando:
  - Nombre de barra (frame).
  - Sección asignada actualmente.
  - Tipo de sección (I, Channel, Angle, Tube, o null si no se reconoce).
  - Dimensiones en mm: altura (h), ancho (b), espesor de ala (tf), espesor de alma (tw).
  - Longitud en mm.
  - Fy (tensión de fluencia) — **siempre aparece vacío** (este es el comportamiento esperado; ver paso de riesgo 4a más abajo).

**Verificación en SAP2000:**

- Para cada barra listada:
  - Haz doble clic en la barra en SAP2000 (o Editar → Propiedades de barra) y abre su sección.
  - Comprueba que el nombre de sección, dimensiones (h, b, tf, tw en mm) y longitud coinciden con lo mostrado en la web.
  - Nota especialmente si alguna barra aparece con tipo null: esto significa que la lectura de dimensiones falló y requiere investigación.

**Resultado:**

- ☐ Pasó
- ☐ Falló

**Notas:** ************************************\_\_\_************************************

---

### 5. PASO DE RIESGO VERIFICADO (4a): Heurística de detección de familia de sección

⚠️ **Este paso verifica un algoritmo nunca ejecutado contra SAP2000 real.**

**Contexto:**

- El puente intenta leer las dimensiones de una barra usando las llamadas `PropFrame.GetISection`, `PropFrame.GetChannel`, `PropFrame.GetAngle`, `PropFrame.GetTube` en ese orden, y se queda con la primera que SAP2000 acepta (retorna 0).
- Esta heurística **no ha sido verificada contra SAP2000 real** — es una derivación de llamadas de escritura que ya existen en el código.

**Qué hacer:**

- En SAP2000, asigna a una barra una sección de tipo **I** (p. ej. W310x33). Anótalo: ******\_\_\_\_******.
- En la web, en Esbelteces, importa datos y mira esa barra.

**Qué debería pasar:**

- La barra debe mostrar `section_type: "I"` y todas sus dimensiones deben ser números (no null).

**Si falla:**

- Anota exactamente qué tipo apareció (I, Channel, Angle, Tube, null) y qué era en SAP2000.
- Repite con una sección **Channel** y una **Angle** si tienes tiempo.

**Resultado:**

- ☐ Pasó (I detectada como I)
- ☐ Parcial (algunos tipos OK, otros no) — describe:
  ***
- ☐ Falló

**Notas:** ************************************\_\_\_************************************

---

### 6. PASO DE RIESGO VERIFICADO (4b): Fy (tensión de fluencia) no se lee

⚠️ **Este paso confirma que Fy siempre viaja como null.**

**Qué hacer:**

- Continúa con la tabla de importación de Esbelteces del paso 4.
- Mira la columna "Fy (MPa)".

**Qué debería pasar:**

- **Debe estar vacía (null) para todas las barras.**
- Este es el comportamiento esperado: no existe una llamada COM verificada para leer el Fy del material de la sección.
- El usuario debe completar este valor manualmente en la interfaz de Esbelteces.

**Verificación en SAP2000:**

- Para al menos una barra, abre su sección (Editar → Propiedades de barra → Modificar sección) y anota su material y Fy desde la ficha Propiedades del material.
- Vuelve a la web y confirma que Fy sigue en blanco.

**Resultado:**

- ☐ Pasó (Fy está vacío como se esperaba)
- ☐ Falló (Fy tiene un valor — **esto es un defecto**)

**Notas:** ************************************\_\_\_************************************

---

### 7. Crear nueva sección (Propiedad de barra)

**Qué hacer:**

- Ve al módulo _Perfiles_ (pestaña principal).
- En la sección izquierda del proyecto, hay un módulo "Perfiles con datos previos". Selecciona (o crea) un perfil con un catálogo de dimensiones (p. ej. un perfil I con h=300 mm, b=150 mm, tf=12 mm, tw=8 mm).
- En el panel _Perfiles_ de la web, con SAP2000 conectado:
  - Elige un material de la lista desplegable (p. ej. A36).
  - Haz clic en "Revisar creación de propiedad".

**Qué debería pasar:**

- La interfaz muestra una sección de confirmación con el nombre de la propiedad, tipo (I/Channel/Angle/Tube), material y dimensiones.
- Haz clic en "Confirmar y aplicar en SAP2000".
- La interfaz web devuelve un mensaje de operación completada.

**Verificación en SAP2000:**

- Abre Definir → Secciones.
- **La nueva sección debe estar en la lista** con el nombre exacto que se envió.
- Haz doble clic en ella y verifica que las dimensiones (h, b, tf, tw en mm) coinciden con las que se crearon.

**Resultado:**

- ☐ Pasó
- ☐ Falló

**Notas:** ************************************\_\_\_************************************

---

### 8. Asignar sección existente a barras

**Qué hacer:**

- En SAP2000, selecciona nuevamente una o más barras que no tengan la sección que acabas de crear (o cualquier otra sección que quieras probar).
- En la web (panel _Perfiles_):
  - Haz clic en "Leer barras seleccionadas" para actualizar la lista.
  - De la lista desplegable "Sección existente a asignar", elige la sección que creaste en el paso anterior (o cualquier otra).
  - Haz clic en "Revisar asignación".

**Qué debería pasar:**

- La interfaz muestra una confirmación con los nombres de las barras y la sección a asignar.
- Haz clic en "Confirmar y aplicar en SAP2000".
- La interfaz web devuelve un mensaje de operación completada, enumerando las barras a las que se asignó.

**Verificación en SAP2000:**

- Selecciona nuevamente las barras a las que acabas de asignar.
- Abre Editar → Propiedades de barra (o doble clic).
- La sección debe mostrar el nombre exacto de la sección que asignaste.
- Las propiedades mecánicas (módulo, inercia, etc.) deben corresponder a esa sección.

**Resultado:**

- ☐ Pasó
- ☐ Falló

**Notas:** ************************************\_\_\_************************************

---

### 9. Crear combinaciones de carga

**Qué hacer:**

- Ve al módulo _Combinaciones_ del proyecto.
- Configura al menos una combinación simple (p. ej. 1.4×DEAD + 1.6×LIVE, o similar según tu norma).
- Haz clic en "Calcular combinaciones" o el botón equivalente para generar la matriz.
- En el panel _Combinaciones_ de la web SAP:
  - Verifica que muestre "N combinaciones generadas en el proyecto".
  - Haz clic en "Revisar envío de combinaciones".

**Qué debería pasar:**

- La interfaz muestra una confirmación con los nombres de las combinaciones a crear y sus factores por caso de carga.
- Haz clic en "Confirmar y aplicar en SAP2000".
- La interfaz web devuelve un mensaje de operación completada, enumerando los nombres de las combinaciones creadas.

**Verificación en SAP2000:**

- Abre Definir → Combinaciones de carga.
- **Las combinaciones que creaste deben estar en la lista** con los nombres exactos.
- Haz doble clic en una para ver sus componentes: cada caso de carga debe aparecer con el factor que se envió desde la web.

**Resultado:**

- ☐ Pasó
- ☐ Falló

**Notas:** ************************************\_\_\_************************************

---

### 10. Crear función de espectro de respuesta

**Qué hacer:**

- Ve al módulo _Espectro_ del proyecto.
- Configura un espectro de respuesta (p. ej. siguiendo una norma como NCh2369, AISC, etc.).
- Haz clic en "Calcular espectro" o equivalente para generar los puntos (período, aceleración).
- En el panel _Función de espectro_ de la web SAP:
  - Elige un nombre para la función (p. ej. RS_X, RS_Y).
  - Elige una dirección (Diseño X, Diseño Y, o vertical).
  - Haz clic en "Revisar envío del espectro".

**Qué debería pasar:**

- La interfaz muestra una confirmación con el nombre, número de puntos y amortiguamiento (damping) de la función.
- Haz clic en "Confirmar y aplicar en SAP2000".
- La interfaz web devuelve un mensaje de operación completada con el nombre de la función creada.

**Verificación en SAP2000:**

- Abre Definir → Funciones → Espectro de respuesta.
- **La función que creaste debe estar en la lista** con el nombre exacto.
- Haz doble clic en ella para ver:
  - Los puntos (período, ordenada espectral Sa/g) — deben coincidir con los que se calcularon en el módulo Espectro.
  - El amortiguamiento — debe ser el que se envió.
- Opcionalmente, grafica la función y compárala con lo que calculó el módulo Espectro de la web.

**Resultado:**

- ☐ Pasó
- ☐ Falló

**Notas:** ************************************\_\_\_************************************

---

### 11. Leer reacciones en nodos (solo lectura, no escritura)

**Qué hacer:**

- En SAP2000, selecciona uno o más nodos (puntos) que tengan reacciones después de un análisis.
- En la web, ve al panel _Resultados de fundaciones_.
- Haz clic en "Importar datos del modelo a Fundaciones".

**Qué debería pasar:**

- La interfaz web devuelve un conjunto de datos que incluye:
  - Coordenadas cartesianas de los nodos seleccionados.
  - Reacciones (fuerzas F1, F2, F3 y momentos M1, M2, M3) por nodo y por caso de carga.
  - Desplazamientos (si se calcularon).

**Verificación en SAP2000:**

- Antes de importar, define en SAP2000 (Mostrar → Tablas de salida, o el selector de casos de la barra de resultados) cuál es el caso o combinación "activo" para la selección de salida: la extracción lee exactamente esa selección, no permite elegir un caso distinto desde la web (si se intentara, el agente respondería con un error explícito en vez de cambiar la selección por su cuenta).
- Para al menos un nodo, abre Resultados → Reacciones en nodos (o Resultados → Tabla de salida) con ese mismo caso activo.
- Comprueba que las reacciones (F1, F2, F3, M1, M2, M3) coinciden exactamente con lo importado.

**Resultado:**

- ☐ Pasó
- ☐ Falló

**Notas:** ************************************\_\_\_************************************

---

### 13. Listar casos y combinaciones (Fundaciones)

**Qué hacer:**

- Ve al módulo _Fundaciones_ del proyecto.
- En el panel izquierdo, verifica que bajo "Casos y combinaciones" aparezca una lista de casos de carga y combinaciones definidos en SAP2000.

**Qué debería pasar:**

- El listado debe mostrar al menos los casos que definiste en SAP2000 (p. ej. DEAD, LIVE, etc.).
- Las combinaciones creadas en el paso 9 deben aparecer en la lista.

**Verificación en SAP2000:**

- Abre Definir → Casos de carga y Definir → Combinaciones de carga para comparar.

**Resultado:**

- ☐ Pasó
- ☐ Falló

**Notas:** ************************************\_\_\_************************************

---

### 14. Usar selección actual (nodos y shells)

**Qué hacer:**

- En SAP2000, selecciona uno o más nodos (puntos) que formen parte de la cimentación o zona de estudio.
- Ve al módulo _Fundaciones_ de la web.
- Verifica que en el panel de selección aparezca "Selección actual: X nodos seleccionados".
- Si lo hay, selecciona también algunos shells/áreas (si tu modelo los tiene) y verifica que la selección se actualiza.

**Qué debería pasar:**

- La interfaz web refleja la selección actual de SAP2000 en tiempo real.
- Los nodos y shells seleccionados quedan resaltados o marcados visualmente.
- Si cambias la selección en SAP2000, la web se actualiza sin necesidad de hacer clic en un botón.

**Verificación en SAP2000:**

- La selección en SAP2000 debe mantenerse coherente con lo mostrado en la web.

**Resultado:**

- ☐ Pasó
- ☐ Falló

**Notas:** ************************************\_\_\_************************************

---

### 15. Importar grupos de SAP2000

**Qué hacer:**

- En SAP2000, define o asegúrate de que existen varios grupos (Seleccionar → Grupos → [Lista de grupos]).
- En la web (módulo _Fundaciones_), haz clic en "Importar grupos desde SAP2000".

**Qué debería pasar:**

- La interfaz web muestra un listado de todos los grupos definidos en SAP2000.
- Los nombres deben coincidir exactamente con los definidos en SAP2000.

**Verificación en SAP2000:**

- Abre Seleccionar → Grupos y comprueba que cada nombre que aparece en la web existe.

**Resultado:**

- ☐ Pasó
- ☐ Falló

**Notas:** ************************************\_\_\_************************************

---

### 16. Crear snapshot acotado a combinaciones

**Qué hacer:**

- En el módulo _Fundaciones_ de la web, elige una o más combinaciones del listado (pasos 13).
- Haz clic en "Crear snapshot de modelo para cálculos de fundaciones".
- Elige el alcance: "Solo combinaciones seleccionadas".

**Qué debería pasar:**

- La interfaz web muestra un mensaje de confirmación con el número de combinaciones incluidas.
- Haz clic en "Confirmar".
- El snapshot se crea (archivo JSON) y se muestra en el panel de datos.

**Verificación en SAP2000:**

- La selección de casos de SAP2000 debe cambiar automáticamente para reflejar solo las combinaciones incluidas en el snapshot (la web modifica la selección de salida en SAP2000 como parte de la operación).
- Abre Mostrar → Tablas de salida y verifica que el caso activo es uno de los de la combinación seleccionada.

**Resultado:**

- ☐ Pasó
- ☐ Falló

**Notas:** ************************************\_\_\_************************************

---

### 17. Leer cargas nodales existentes

**Qué hacer:**

- En SAP2000, asegúrate de que hay nodos con cargas nodales aplicadas (Asignar → Cargas nodales o Asignar → Cargas de área).
- En el módulo _Fundaciones_ de la web, en el panel "Cargas nodales", haz clic en "Leer cargas existentes".

**Qué debería pasar:**

- La interfaz muestra una tabla con:
  - Nodo (identificador del punto).
  - Caso de carga (el caso en el que se aplica).
  - Componentes de carga (Fx, Fy, Fz, Mx, My, Mz) con sus valores.

**Verificación en SAP2000:**

- Para al menos un nodo, abre Asignar → Cargas nodales y selecciona ese nodo.
- Verifica que las componentes de carga (valores numéricos de fuerzas y momentos) coinciden exactamente con lo mostrado en la tabla de la web.

**Resultado:**

- ☐ Pasó
- ☐ Falló

**Notas:** ************************************\_\_\_************************************

---

### 18. Comparar y aplicar cargas nodales

**Qué hacer:**

- Continuando desde el paso anterior, en la tabla de cargas nodales:
  - Selecciona una o más filas (cargas a modificar).
  - Escribe los nuevos valores de componentes de carga en los campos de entrada.
  - Haz clic en "Vista previa de cambios".

**Qué debería pasar:**

- La interfaz muestra un resumen de:
  - Qué cargas van a ser reemplazadas.
  - Cuáles son los valores actuales y cuáles los nuevos.
- Haz clic en "Aplicar cambios".
- El mensaje indica "Cargas nodales aplicadas exitosamente".

**Verificación en SAP2000:**

- Selecciona de nuevo los nodos que modificaste.
- Abre Asignar → Cargas nodales.
- Verifica que los nuevos valores (Fx, Fy, Fz, Mx, My, Mz) aparecen exactamente como los que aplicaste desde la web.

**Resultado:**

- ☐ Pasó
- ☐ Falló

**Notas:** ************************************\_\_\_************************************

---

### 19. Verificar que el modelo no se alteró

**Qué hacer:**

- Una vez completados los pasos anteriores, guarda el modelo en SAP2000 con otro nombre (Archivo → Guardar como → [nombre_modelo]\_post_test.sdb).
- Abre un análisis visual para confirmar que todo el modelo permanece íntegro (sin borrados, cambios no intencionados, etc.).

**Qué debería pasar:**

- El modelo abre sin errores.
- Las barras, nodos, casos de carga y definiciones que no fueron tocadas por los pasos anteriores están intactas.

**Resultado:**

- ☐ Pasó
- ☐ Falló

**Notas:** ************************************\_\_\_************************************

---

### 20. Conectar desde Perfiles y verificar estado de conexión

**Qué hacer:**

- Ve al módulo _Perfiles_ del proyecto.
- Verifica que en el top bar aparezca el estado "Estado SAP2000: Conectando..." mientras se establece la conexión.
- Una vez conectado, debería mostrar "Estado SAP2000: Conectado".
- Verifica que el combo "Material:" está poblado con materiales disponibles en SAP2000 (p. ej. A36, ASTM A36, acero estructural, etc.).

**Qué debería pasar:**

- La conexión se establece sin errores.
- El estado cambia a "Conectado" en menos de 5 segundos.
- Al menos un material aparece en la lista desplegable de materiales.

**Verificación en SAP2000:**

- El modelo permanece abierto y sin cambios.
- Abre Definir → Materiales y verifica que al menos uno de los materiales mostrados en la web existe.

**Resultado:**

- ☐ Pasó
- ☐ Falló

**Notas:** ************************************\_\_\_************************************

---

### 21. Crear una sección de cada familia (I, Canal, Ángulo, Tubo) y verificar en SAP2000

**Qué hacer:**

- En el módulo _Perfiles_, ve a cada pestaña (Perfiles H (I / W), Perfiles C (Canales), Perfiles L (Ángulos), Perfiles Rectangulares (Caja / Tubo)).
- Para cada familia:
  1. Selecciona un perfil de la tabla (p. ej. W310x33 para I, C200x10 para Canal, L100x100x8 para Ángulo, HSS100x100x4.8 para Tubo).
  2. Elige un material del combo (p. ej. A36).
  3. Haz clic en "Revisar creación de propiedad" (o el botón equivalente).
  4. Verifica la confirmación con nombre, tipo, material y dimensiones.
  5. Haz clic en "Confirmar y aplicar en SAP2000".
- Repite para las cuatro familias.

**Qué debería pasar:**

- Cada operación devuelve un mensaje de éxito ("Sección creada correctamente" o equivalente).
- La interfaz web no muestra errores.

**Verificación en SAP2000:**

- Después de cada creación, abre Definir → Secciones → Propiedades de secciones de marco.
- **Cada una de las cuatro secciones debe aparecer** con el nombre exacto que se envió.
- Haz doble clic en cada una y verifica:
  - **Tipo:** I, Canal, Ángulo o Tubo (según corresponda).
  - **Dimensiones en mm:** h (altura), b (ancho), tf (espesor de ala), tw (espesor de alma) deben coincidir exactamente con lo que se creó desde la web.
  - **Material:** el material seleccionado en la web debe estar asignado.

**Resultado:**

- ☐ Pasó (todas las cuatro familias creadas correctamente)
- ☐ Parcial (algunas familias OK, otras no) — describe: ************************************\_\_\_************************************
- ☐ Falló

**Notas:** ************************************\_\_\_************************************

---

### 22. Crear una sección que ya existe y verificar confirmación de reemplazo

**Qué hacer:**

- En el módulo _Perfiles_, selecciona un perfil de la tabla que corresponda a una sección **ya existente** en SAP2000 (puedes usar uno de los creados en el paso 21, o uno del catálogo original que ya esté en el modelo).
- Elige un material (puede ser el mismo o diferente).
- Haz clic en "Revisar creación de propiedad".
- Verifica la confirmación.
- Haz clic en "Confirmar y aplicar en SAP2000".

**Qué debería pasar:**

- Un diálogo de confirmación aparece diciendo (en español): "La sección [nombre] ya existe en SAP2000. ¿Deseas reemplazarla?".
- Hay dos botones: "Aceptar" y "Cancelar" (o equivalentes).
- Si haces clic en "Aceptar", la sección se reemplaza y aparece un mensaje de éxito.
- Si haces clic en "Cancelar", la operación se cancela y no hay cambios en SAP2000.

**Verificación en SAP2000:**

- Abre Definir → Secciones → Propiedades de secciones de marco.
- Selecciona la sección que acabas de reemplazar (mismo nombre).
- Verifica que sus dimensiones y material **corresponden exactamente** a lo que enviaste desde la web (no reflejan los valores anteriores).

**Resultado:**

- ☐ Pasó (confirmación y reemplazo funcionan)
- ☐ Falló (no aparece diálogo o el reemplazo no funciona)

**Notas:** ************************************\_\_\_************************************

---

### 23. Conectar y verificar importación de barras seleccionadas en Esbelteces

**Qué hacer:**

- Conecta a SAP2000 si no lo está ya (en el panel _Conexión local_, haz clic en "Conectar a SAP2000").
- En SAP2000, selecciona una o más barras (frame objects) de diferentes tipos: por lo menos una I (p. ej. W310x33), una Canal (p. ej. C200x10), una Ángulo (p. ej. L100x100x8) y una Tubo (p. ej. HSS100x100x4.8) si es posible.
- Ve al módulo _Esbelteces_ de la web.
- En la sección "Desde SAP2000" (parte inferior del panel de carga de datos), haz clic en "Importar seleccionados".

**Qué debería pasar:**

- Una tabla aparece con una fila por barra, mostrando:
  - Nombre de barra.
  - Sección asignada.
  - Tipo de sección (I, Canal, Ángulo, Tubo).
  - Dimensiones en mm: altura (h), ancho (b), espesor de ala (tf), espesor de alma (tw).
  - Radio de giro rx/ry en cm.
  - Radio de giro rz para ángulos en cm.
  - Longitud en mm.
  - Fy (tensión de fluencia) — puede estar vacío; el usuario lo completa manualmente.

**Verificación en SAP2000:**

- Para cada barra importada:
  - Haz doble clic en la barra en SAP2000 (o Editar → Propiedades de barra).
  - Verifica que el nombre de sección, dimensiones (h, b, tf, tw en mm), longitud y radios de giro (rx, ry, rz) coinciden exactamente con lo mostrado en la web.

**Resultado:**

- ☐ Pasó
- ☐ Falló

**Notas:** ************************************\_\_\_************************************

---

### 24. Importar por grupos y mapear a categorías

**Qué hacer:**

- En SAP2000, asegúrate de que existen varios grupos definidos (Seleccionar → Grupos → [Lista de grupos]) y que contienen barras diferentes.
- En el módulo _Esbelteces_ de la web, en la sección "Desde SAP2000", haz clic en "Importar por grupos...".
- Aparecerá un diálogo mostrando los grupos disponibles en SAP2000.
- Mapea cada grupo a una categoría disponible (las categorías corresponden a las clasificaciones de la norma activa).
- Haz clic en "Confirmar e importar".

**Qué debería pasar:**

- La tabla de datos se llena con barras de todos los grupos importados, cada una asignada a la categoría mapeada.
- Si una barra pertenece a dos grupos diferentes, se le asigna la categoría del primer grupo encontrado.
- La operación devuelve un mensaje de éxito indicando cuántas barras se importaron.

**Verificación en SAP2000:**

- En SAP2000, abre Seleccionar → Grupos y verifica que cada grupo que mapeaste existe.
- Selecciona un grupo y cuenta el número de barras que contiene.
- Vuelve a la web y filtra las barras por la categoría asignada a ese grupo.
- El número de barras debe coincidir (o ser menor si hay solapamientos entre grupos).

**Resultado:**

- ☐ Pasó
- ☐ Falló

**Notas:** ************************************\_\_\_************************************

---

### 25. Cambiar sección en barras seleccionadas

**Qué hacer:**

- En la tabla de Esbelteces, selecciona una o más filas (barras) que quieras modificar.
- Modifica la "Sección" en el panel de detalle (elige una sección diferente de la lista disponible).
- Haz clic en "Cambiar sección en SAP2000".
- La interfaz web devuelve un mensaje de confirmación indicando cuáles barras se van a modificar.
- Haz clic en "Confirmar".

**Qué debería pasar:**

- La interfaz web devuelve un mensaje de operación completada.
- Los valores de Kx, Ky, Lx, Ly y otros parámetros se recalculan automáticamente si es necesario.

**Verificación en SAP2000:**

- Para cada barra modificada:
  - Haz doble clic en la barra en SAP2000 (o Editar → Propiedades de barra).
  - Verifica que la sección asignada es exactamente la que enviaste desde la web.
  - Las propiedades mecánicas (módulo, inercia, etc.) deben corresponder a la nueva sección.
- En la tabla de Esbelteces, la fila recalculada debe mostrar nuevas dimensiones y radios de giro.

**Resultado:**

- ☐ Pasó
- ☐ Falló

**Notas:** ************************************\_\_\_************************************

---

### 26. Resaltar barras REVISAR en SAP2000

**Qué hacer:**

- En la tabla de Esbelteces, verifica que hay barras con estado "REVISAR" (aquellas que no cumplen los límites de la norma activa).
- Haz clic en el botón "Resaltar REVISAR en SAP2000".

**Qué debería pasar:**

- La interfaz web selecciona automáticamente las barras en SAP2000 cuyo estado es "REVISAR".
- Estas barras quedan resaltadas o seleccionadas visualmente en SAP2000.
- Solo las barras "REVISAR" quedan seleccionadas (las barras "CUMPLE" no se modifican).

**Verificación en SAP2000:**

- Verifica que las barras resaltadas en SAP2000 corresponden exactamente a las marcadas con "REVISAR" en la web.
- Las barras con "CUMPLE" no deben estar seleccionadas.
- Si modificas la tabla de Esbelteces y algunos estados cambian de "REVISAR" a "CUMPLE", repetir el paso debe reflejar la nueva selección.

**Resultado:**

- ☐ Pasó
- ☐ Falló

**Notas:** ************************************\_\_\_************************************

---

### 27. Aplicar coeficientes K a SAP2000 con código AISC 360-16

**Qué hacer:**

- En el módulo _Esbelteces_, configura los coeficientes de longitud efectiva:
  - En el panel de detalle, modifica los valores de Kx, Ky, Lx, Ly según corresponda (son editables).
  - Asegúrate de que la "Norma de diseño" está configurada como "AISC 360-16".
- Selecciona una o más filas (barras) a las que aplicar K.
- Haz clic en "Aplicar K a SAP2000".
- La interfaz web devuelve un mensaje de confirmación indicando cuáles barras se van a modificar.

**Qué debería pasar:**

- La interfaz web devuelve un mensaje de operación completada.
- Un mensaje de éxito aparece indicando que los coeficientes K2 (Mayor/Menor) se han escrito.

**Verificación en SAP2000:**

- Para cada barra modificada:
  - Haz clic en Diseño → Diseño de estructuras de acero → Ver/Revisar sobrescrituras (o equivalente).
  - En la tabla de sobrescrituras (Overwrite Settings), verifica que la columna K2 Major/Minor muestra los valores que se enviaron desde la web (calculados como Kx y Ky).
- Cambia la "Norma de diseño" en el módulo Esbelteces a otra norma (p. ej. NCh2369:2025) y repite "Aplicar K a SAP2000".

**Qué debería pasar (con otra norma):**

- Un mensaje de error aparece indicando que esa norma no es compatible con la aplicación de K (porque los parámetros K solo son estándar en AISC).
- Ningún valor se escribe en SAP2000.

**Verificación en SAP2000:**

- Abre nuevamente Ver/Revisar sobrescrituras.
- Verifica que no hay cambios respecto al paso anterior.

**Resultado:**

- ☐ Pasó (K2 escrito correctamente con AISC 360-16, error con otra norma)
- ☐ Parcial (solo uno de los dos casos funcionó) — describe: ************************************\_\_\_************************************
- ☐ Falló

**Notas (incluye versión de SAP2000):** ************************************\_\_\_************************************

---

### 28. Obtener SAP: importar casos y combinaciones

**Qué hacer:**

- En SAP2000, define al menos algunos casos de carga (p. ej. DEAD, LIVE, WIND) y algunas combinaciones de carga existentes (p. ej. 1.4×DEAD, 1.2×DEAD+1.6×LIVE).
- Ve al módulo _Combinaciones_ de la web.
- Haz clic en "Obtener SAP" (o el botón equivalente de importación desde SAP2000).

**Qué debería pasar:**

- Un diálogo aparece mostrando:
  - Lista de casos de carga encontrados en SAP2000 (todos con casilla verificada por defecto).
  - Lista de combinaciones existentes en SAP2000 (todas con casilla desmarcada por defecto).
- Selecciona los casos que quieres importar (deja marcados o marca algunos).
- Opcionalmente, marca algunas combinaciones existentes (para visualización).
- Haz clic en "Confirmar e importar".
- La interfaz web devuelve un mensaje indicando cuántos casos se importaron.

**Verificación en SAP2000:**

- En la web, ve a la pestaña "Combinaciones desglosadas".
- Los casos importados deben aparecer en el panel "Estados de carga" de la pestaña "Principal".
- Las combinaciones existentes (si se importaron) deben aparecer en la tabla de "Combinaciones desglosadas" con sus miembros listados.

**Resultado:**

- ☐ Pasó
- ☐ Falló

**Notas:** ************************************\_\_\_************************************

---

### 29. Enviar SAP: agregar a las existentes

**Qué hacer:**

- En el módulo _Combinaciones_ de la web, genera o configura al menos una combinación nueva (p. ej. una que no exista ya en SAP2000).
- En el panel SAP2000, haz clic en "Enviar SAP" (o el botón de envío).
- En el diálogo que aparece, asegúrate de que la opción "Agregar a las existentes" está seleccionada.
- Haz clic en "Confirmar y enviar".

**Qué debería pasar:**

- La interfaz web devuelve un mensaje de operación completada.
- Un mensaje indica cuántas combinaciones se crearon o actualizaron en SAP2000.

**Verificación en SAP2000:**

- Abre Definir → Combinaciones de carga.
- **Las nuevas combinaciones deben estar en la lista** con los nombres exactos que se enviaron desde la web.
- Haz doble clic en una combinación enviada y verifica:
  - Los casos de carga (Load Patterns) y sus factores coinciden con lo calculado en la web.
  - Las combinaciones existentes (que no se reemplazaron) permanecen intactas.

**Resultado:**

- ☐ Pasó
- ☐ Falló

**Notas:** ************************************\_\_\_************************************

---

### 30. Reemplazar todas las combinaciones

**Qué hacer:**

- En el módulo _Combinaciones_ de la web, genera un conjunto completamente nuevo de combinaciones (idealmente diferentes de las existentes en SAP2000).
- En el panel SAP2000, haz clic en "Enviar SAP" (o el botón de envío).
- En el diálogo, selecciona la opción "Reemplazar todas las combinaciones" (en lugar de "Agregar a las existentes").
- Haz clic en "Confirmar y enviar".

**Qué debería pasar:**

- La interfaz web devuelve un mensaje indicando que primero se eliminarán todas las combinaciones existentes.
- Después, devuelve un mensaje de operación completada indicando cuántas combinaciones nuevas se crearon.

**Verificación en SAP2000:**

- Abre Definir → Combinaciones de carga.
- **Solo las combinaciones enviadas desde la web deben estar presentes.**
- Las combinaciones que existían antes y no fueron incluidas en el nuevo conjunto deben haberse eliminado.
- Verifica que las nuevas combinaciones tienen los factores correctos por caso de carga.

**Resultado:**

- ☐ Pasó (todas las antiguas se borraron, solo las nuevas permanecen)
- ☐ Falló

**Notas:** ************************************\_\_\_************************************

---

### 31. Validar combinación con caso de carga no definido

**Qué hacer:**

- En el módulo _Combinaciones_ de la web, crea una combinación que haga referencia a un caso de carga que **no existe en SAP2000** (p. ej. una norma puede generar una combinación usando "VIENTO_ESTE" pero SAP2000 solo tiene "WIND").
- En el panel SAP2000, haz clic en "Enviar SAP".
- Intenta enviar esta combinación.

**Qué debería pasar:**

- La interfaz web devuelve un **mensaje de advertencia** indicando: "revisar que el estado de carga se encuentre creado en SAP2000".
- El envío se detiene o se completa pero marca la combinación problemática.
- Un diálogo adicional puede permitir "Cancelar", "Continuar de todos modos" o "Ir a Configuración".

**Verificación en SAP2000:**

- Si el envío se permitió parcialmente, abre Definir → Combinaciones de carga.
- Verifica que la combinación problemática **no aparece** (o aparece incompleta) en la lista.
- Las combinaciones válidas (que usan casos definidos) deben estar presentes.

**Resultado:**

- ☐ Pasó (advertencia mostrada, caso no definido manejado correctamente)
- ☐ Falló (no se mostró advertencia, o se envió silenciosamente)

**Notas:** ************************************\_\_\_************************************

---

### 32. Eliminar combinaciones en el diálogo de envío

**Qué hacer:**

- En el módulo _Combinaciones_ de la web, selecciona una o más combinaciones que quieras eliminar.
- En el panel SAP2000, haz clic en "Enviar SAP".
- En el diálogo que aparece, marca las casillas de combinaciones que deseas **eliminar** (si el diálogo ofrece esa opción).
- Alternativamente, si existe un botón "Eliminar" en la tabla de combinaciones del diálogo, úsalo para marcar para eliminación.
- Haz clic en "Confirmar".

**Qué debería pasar:**

- La interfaz web devuelve un mensaje de confirmación indicando cuántas combinaciones se van a eliminar.
- Las operaciones de eliminación se completan sin errores.
- Un mensaje final indica el resultado (combinaciones eliminadas exitosamente, errores si los hay).

**Verificación en SAP2000:**

- Abre Definir → Combinaciones de carga.
- Las combinaciones marcadas para eliminación **deben haber desaparecido** de la lista.
- Si una combinación no pudo eliminarse (p. ej. porque está en uso), un mensaje de error en la web debería indicarlo.

**Resultado:**

- ☐ Pasó (eliminación exitosa, errores reportados si aplica)
- ☐ Parcial (algunas se eliminaron, otras no) — describe: ************************************\_\_\_************************************
- ☐ Falló

**Notas:** ************************************\_\_\_************************************

---

### 33. Enviar espectro a SAP2000: función de respuesta espectral

**Qué hacer:**

- Ve al módulo _Espectro_ del proyecto.
- Configura un espectro de respuesta (p. ej. NCh2369:2025 con parámetros de diseño sísmico).
- En la pestaña _ESPECTRO_, verifica que se genera la tabla de 7 columnas con puntos del espectro.
- En el diálogo "Enviar espectro a SAP2000" (botón OBT. SAP):
  - Haz clic en "ENV. SAP" para listar funciones de espectro de respuesta existentes en SAP2000.

**Qué debería pasar:**

- Un diálogo aparece mostrando el listado de funciones de espectro de respuesta ya definidas en Define > Functions > Response Spectrum.
- El listado está vacío si no hay funciones definidas, o muestra nombres como "RS_X", "RS_Y", etc.
- Si completas los datos y confirmas el envío, se crea/reemplaza la función con nombre "NCh2369_Sa_design_Hx" (o "NCh2369_Sa_design_Hy" para el eje Y).

**Verificación en SAP2000:**

- Abre Definir → Funciones → Espectro de respuesta.
- **La función "NCh2369_Sa_design_Hx" (o Hy) debe estar en la lista** con los puntos exactos (período, Sa/g) calculados en la web.
- Haz doble clic para verificar:
  - Los puntos (período, ordenada espectral).
  - El amortiguamiento (damping = 0.05).
- Opcionalmente, grafica la función en SAP2000 y compárala visualmente con el gráfico de la pestaña ESPECTRO de la web.

**Resultado:**

- ☐ Pasó
- ☐ Falló

**Notas:** ************************************\_\_\_************************************

---

### 34. Obtener Ps (masa sísmica) desde SAP2000

**Qué hacer:**

- En el módulo _Espectro_ de la web, pestaña PARÁMETROS, sección "Configuración — Conexión SAP2000", diálogo "Enviar espectro a SAP2000":
  - Verifica que hay una fila "Mass Source" con opciones de fuentes (p. ej. "Peso propio + Sobrecarga").
  - Selecciona una masa (p. ej. "Sobrecarga"), que corresponde a Ps.
  - Haz clic en "Calcular Ps".

**Qué debería pasar:**

- La interfaz web lee las Base Reactions desde SAP2000 (requiere que se haya ejecutado análisis previo).
- Calcula Ps = Σ factor × ΣFZ (suma de fuerzas Z de todas las reacciones en la base).
- El valor de Ps aparece en la interfaz.

**Verificación en SAP2000:**

- En SAP2000, abre Resultados → Tablas de salida (o el selector de casos en la barra de resultados).
- Selecciona "Base Reactions" (Reacciones en la base).
- Suma manualmente los valores de FZ para todos los nodos: esto debe coincidir con el Ps mostrado en la web (multiplicado por el factor de masa seleccionado en la web).
- Verifica que el análisis fue ejecutado recientemente (Analyze > Run Analysis).

**Resultado:**

- ☐ Pasó (Ps coincide con ΣFZ)
- ☐ Falló

**Notas:** ************************************\_\_\_************************************

---

### 35. Obtener cortantes sísmicos Qx/Qy

**Qué hacer:**

- Continuando en el diálogo "Enviar espectro a SAP2000", en la sección de Sismo:
  - Selecciona casos de carga (p. ej. "Sismo X" → Qx, "Sismo Y" → Qy).
  - Haz clic en "Calcular Qx" (o el equivalente para Qy).

**Qué debería pasar:**

- La interfaz web lee las reacciones en la base desde SAP2000 (caso específico de análisis).
- Extrae la fuerza horizontal: Qx = FX de la reacción en el caso "Sismo X", Qy = FY de la reacción en el caso "Sismo Y".
- Los valores aparecen en la interfaz.

**Verificación en SAP2000:**

- En SAP2000, abre Resultados → Base Reactions con el caso "Sismo X" activo.
- Lee el valor total de FX (suma de reacciones en X) — debe coincidir con el Qx mostrado en la web.
- Repite con el caso "Sismo Y" y verifica que FY coincide con Qy.

**Resultado:**

- ☐ Pasó (Qx/Qy coinciden con FX/FY de Base Reactions)
- ☐ Falló

**Notas:** ************************************\_\_\_************************************

---

### 36. Obtener períodos modales T*x / T*y

**Qué hacer:**

- En el diálogo "Enviar espectro a SAP2000", sección "Modal":
  - Selecciona un caso de análisis modal (p. ej. "Modal").
  - Haz clic en "Obtener T*x / T*y".

**Qué debería pasar:**

- La interfaz web lee los datos de Modal Participating Mass Ratios desde SAP2000.
- Extrae el período con máxima participación de masa:
  - T\*x = período del modo con máximo UX en la tabla Modal Participating Mass Ratios.
  - T\*y = período del modo con máximo UY en la tabla.
- Los valores aparecen en la interfaz (en segundos).

**Verificación en SAP2000:**

- En SAP2000, abre Resultados → Tablas de salida → Modal Participating Mass Ratios.
- Busca la fila (modo) con mayor porcentaje en la columna "UX" — anota su período (primera columna).
- Verifica que coincide exactamente con el T\*x mostrado en la web.
- Repite para "UY" y T\*y.

**Resultado:**

- ☐ Pasó (T*x/T*y coinciden con períodos de max UX/UY)
- ☐ Falló

**Notas:** ************************************\_\_\_************************************

---

### 37. Mensaje de error sin análisis ejecutado

**Qué hacer:**

- En SAP2000, abre un modelo que **no haya sido analizado recientemente** (o crea un modelo nuevo con barras pero sin haber ejecutado análisis).
- En la web, módulo _Espectro_, intenta:
  - "Calcular Ps" sin análisis previo.
  - "Calcular Qx/Qy" sin análisis previo.

**Qué debería pasar:**

- La interfaz web muestra un mensaje de error que incluye **textualmente**: "Corra el análisis en SAP2000 primero (Analyze > Run Analysis)."
- El mensaje es clara y actionable.
- Las operaciones no retornan valores parciales o incorrectos.

**Resultado:**

- ☐ Pasó (mensaje incluye la indicación exacta)
- ☐ Falló (otro mensaje o sin mencionar que ejecutar análisis)

**Notas:** ************************************\_\_\_************************************

---

### 38. Verificar unidades del modelo (tonf, m)

**Qué hacer:**

- En SAP2000, abre el modelo usado en los pasos anteriores.
- Abre Archivo → Información del modelo (o un diálogo equivalente) para verificar las unidades del modelo.

**Qué debería pasar:**

- Las unidades **deben ser tonf (toneladas fuerza) para fuerzas y m (metros) para distancias.**
- Si las unidades son diferentes (p. ej. kN, mm, kgf), los cálculos de Ps y Qx/Qy serán incorrectos.
- La interfaz web **no realiza conversión automática** — asume el modelo en tonf/m (igual comportamiento que el desktop).

**Registro:**

- Anota las unidades del modelo: ************************************\_\_\_************************************
- Versión de SAP2000 (Ayuda → Acerca de SAP2000): ************************************\_\_\_************************************

**Resultado:**

- ☐ Pasó (unidades son tonf/m, sin conversión necesaria)
- ☐ Falló (unidades diferentes, conversión no implementada)

**Notas:** ************************************\_\_\_************************************

---

### 39a. Unidades en Fundaciones

**Qué hacer:**

- En el módulo _Fundaciones_, importa o calcula datos de un modelo en SAP2000.
- Selecciona un nudo (punto) que tenga una reacción vertical (F3) después de un análisis.
- En la web (Fundaciones → Panel "Resultados"), localiza ese nudo en la tabla de reacciones.
- Anota el valor de F3 mostrado en la web.

**Qué debería pasar:**

- Abre SAP2000 y ve a Resultados → Tablas de salida → Joint Reactions (Reacciones en nodos).
- Busca el mismo nudo.
- El valor de F3 en la tabla Joint Reactions (en tonf) **debe coincidir exactamente** con el valor mostrado en la web.
- Las unidades son toneladas fuerza (tonf) en ambas plataformas.

**Resultado:**

- ☐ Pasó (F3 coincide en tonf)
- ☐ Falló (valores no coinciden o unidades diferentes)

**Notas:** ************************************\_\_\_************************************

---

### 39b. Importar grupos desde SAP2000

**Qué hacer:**

- En SAP2000, define o asegúrate de que existen varios grupos con nombres y nodos diferentes (Seleccionar → Grupos).
- En la web (módulo _Fundaciones_), haz clic en "Importar grupos desde SAP2000".

**Qué debería pasar:**

- Un diálogo muestra cada grupo con:
  - Nombre del grupo (idéntico al definido en SAP2000).
  - **Número de nodos** que contiene el grupo.
- El número de nodos debe ser exacto y corresponder a lo definido en SAP2000 para cada grupo.

**Verificación en SAP2000:**

- Para cada grupo importado, abre Seleccionar → Grupos → [Nombre del grupo].
- Verifica que el número de nodos seleccionados coincide con lo mostrado en el diálogo de la web.

**Resultado:**

- ☐ Pasó (cada grupo muestra su número correcto de nodos)
- ☐ Falló (números no coinciden o falta información)

**Notas:** ************************************\_\_\_************************************

---

### 39c. Presión de contacto y diagrama

**Qué hacer:**

- En SAP2000, asegúrate de que el modelo tiene definida "Soil Pressure" (Presión de suelo) en la definición de la cimentación.
- En el módulo _Fundaciones_ de la web, ve al panel "Presión de contacto".
- Crea un snapshot con los datos del modelo o importa resultados de un análisis previo.
- En la tabla de "Presión de contacto", haz clic en "Ver diagrama" para una de las combinaciones.

**Qué debería pasar:**

- La interfaz web muestra un diagrama con los valores de presión de contacto.
- Los valores numéricos en la tabla **deben coincidir exactamente** con la tabla de SAP2000 en Ton/m² (toneladas por metro cuadrado).
- Si abres Resultados → Tablas de salida → Soil Pressure o Shell Stress Resultants en SAP2000, los valores de presión deben ser idénticos.

**Resultado:**

- ☐ Pasó (presión coincide en Ton/m²)
- ☐ Falló (valores no coinciden)

**Notas:** ************************************\_\_\_************************************

---

### 39d. Cargas nodales: leer, aplicar y verificar

**Qué hacer:**

- En el módulo _Fundaciones_, ve al panel "Cargas nodales".
- Haz clic en "Leer cargas existentes" para importar cargas nodales de SAP2000.
- Selecciona una carga existente y modifica uno de los componentes (p. ej. Fx, Fy o Fz) con un nuevo valor.
- En el panel SAP, elige el modo "Reemplazar" (no sumar).
- Haz clic en "Aplicar cargas nodales".

**Qué debería pasar:**

- La interfaz web devuelve un mensaje de operación completada.
- En SAP2000, abre Asignar → Cargas nodales (o Assign → Joint Loads).
- Selecciona el mismo nudo.
- Las componentes de carga (Fx, Fy, Fz en tonf) deben coincidir exactamente con los valores que aplicaste desde la web.
- El modelo debe devolver a las unidades originales que tenía antes de la operación (Ton_m_C → unidades anteriores).

**Verificación adicional (modo suma):**

- Repite el proceso pero elige "Sumar" en lugar de "Reemplazar".
- Aplica una carga adicional.
- En SAP2000, verifica que la carga total es la suma de la anterior más la nueva.

**Resultado:**

- ☐ Pasó (valores coinciden en tonf, modo reemplazar y sumar funcionan, unidades restauradas)
- ☐ Falló (valores no coinciden o unidades no se restauran)

**Notas:** ************************************\_\_\_************************************

---

### 39e. Combinación en Asentamientos/Presión (nombre sin paso)

**Qué hacer:**

- En el módulo _Fundaciones_, ve a los paneles "Asentamientos" o "Presión de contacto".
- Crea un snapshot con múltiples combinaciones de carga.
- En la tabla de resultados, busca la columna "Combinación".

**Qué debería pasar:**

- La columna "Combinación" muestra **solo el nombre del caso de carga o combinación** (p. ej. "Comb_1", "DEAD", "1.2D+1.6L").
- **No aparece Max/Min ni indicación de paso tipo** (los asentamientos y presión de contacto se agrupan solo por OutputCase, sin StepType).
- Esto coincide con el comportamiento del escritorio.

**Verificación:**

- Si abres la tabla en el escritorio con el mismo modelo, la columna "Combinación" debe mostrar exactamente lo mismo sin pasos.

**Resultado:**

- ☐ Pasó (solo nombre de combinación, sin Max/Min ni paso)
- ☐ Falló (aparece paso o indicación Max/Min)

**Notas:** ************************************\_\_\_************************************

---

### 40a. Fundaciones: generar información de exportación solo con Deslizamiento

**Qué hacer:**

- En el módulo _Fundaciones_, ve a la sección "Generar información de exportación".
- Marca **solo la opción "Deslizamiento"** (desmarcar todas las demás: Porcentaje comprimido, Asentamientos, Presión de contacto).
- Haz clic en "Generar información".

**Qué debería pasar:**

- La fuente (reporte de fundaciones) aparece en el Report Builder.
- Se vincula automáticamente a un título (p. ej. "Análisis de Deslizamiento").
- Al sincronizar el capítulo del documento Word, muestra los valores de φ (ángulo de fricción) y FS (factor de seguridad al deslizamiento).

**Resultado:**

- ☐ Pasó
- ☐ Falló

**Notas:** ************************************\_\_\_************************************

---

### 40b. Fundaciones: imágenes de dos campos con Porcentaje comprimido/Asentamientos/Presión

**Qué hacer:**

- En el módulo _Fundaciones_, marca las opciones "Porcentaje comprimido", "Asentamientos" y "Presión de contacto".
- Genera la información de exportación.
- En el Report Builder, sincroniza el documento Word.

**Qué debería pasar:**

- Las imágenes (diagramas) de estos tres campos aparecen en el documento.
- Se distribuyen de **dos en dos por fila** (alineadas horizontalmente).
- Cada imagen tiene su pie (caption) descriptivo.

**Resultado:**

- ☐ Pasó
- ☐ Falló

**Notas:** ************************************\_\_\_************************************

---

### 40c. Espectro: GUARDAR vincula y sincroniza contenido

**Qué hacer:**

- En el módulo _Espectro_, configura un espectro de respuesta (p. ej. NCh2369:2025).
- Haz clic en "GUARDAR".
- La fuente (reporte de espectro) se envía al Report Builder.
- En el Report Builder, sincroniza el documento Word.

**Qué debería pasar:**

- La fuente se vincula automáticamente en el documento.
- Al sincronizar, el capítulo muestra:
  - Tabla de puntos especiales (período, Sa/g).
  - Gráfico del espectro de respuesta.
  - Valores finales de parámetros de diseño (Ps, Qx, Qy, T*x, T*y).

**Resultado:**

- ☐ Pasó
- ☐ Falló

**Notas:** ************************************\_\_\_************************************

---

### 40d. Sincronización sin duplicación y eliminación de anclas

**Qué hacer:**

- En el Report Builder, con una o varias fuentes (anchors) ya sincronizadas:
  - Haz clic en "Sincronizar" nuevamente sin cambios en el documento.
  - Verifica que **no se duplica contenido**.
  - Selecciona un ancla y haz clic en "Eliminar ancla".

**Qué debería pasar:**

- La re-sincronización actualiza el contenido existente sin duplicar párrafos, imágenes o tablas.
- Al eliminar un ancla, **solo se borra su contenido** (el contenido generado por esa fuente); otros anchors permanecen intactos.
- El documento no queda corrupto ni se pierden referencias de formato.

**Resultado:**

- ☐ Pasó (sin duplicación, eliminación limpia)
- ☐ Falló

**Notas:** ************************************\_\_\_************************************

---

### Bridge Routes Documentation

Los siguientes endpoints en `/v1/sap/actions/espectro/*` portan la lógica de `sap2000_bridge.py` del escritorio:

- `POST /v1/sap/actions/espectro/read_response_functions` — lista funciones de respuesta espectral existentes en SAP2000.
- `POST /v1/sap/actions/espectro/send` — crea o reemplaza funciones Sa_diseño_Hx/Hy.
- `POST /v1/sap/actions/espectro/base_reactions` — lee reacciones en la base para Ps, Qx, Qy.
- `POST /v1/sap/actions/espectro/modal_periods` — lee períodos de máxima participación de masa (T*x, T*y).
- Véase `docs/SAP_ACTIONS.md` "## Espectro (puerto del escritorio)" para el contrato completo.

---

### Bridge Routes Documentation

Los siguientes endpoints en `/v1/sap/actions/combinaciones/*` portan la lógica de `io/sap2000_com.py` del escritorio:

- `POST /v1/sap/actions/combinaciones/read` — lee casos y combinaciones existentes desde SAP2000.
- `POST /v1/sap/actions/combinaciones/send` — envía combinaciones nuevas o reemplaza todas las existentes.
- `POST /v1/sap/actions/combinaciones/delete` — elimina combinaciones de SAP2000.
- Véase `docs/SAP_ACTIONS.md` "## Combinaciones (puerto del escritorio)" para el contrato completo.

---

### 41. Conexión única en SAP2000; todos los módulos muestran conectado

**Qué hacer:**

- En el panel _Conexión local_ del proyecto (módulo _Fundaciones_), haz clic en "Conectar a SAP2000".
- Aguarda a que aparezca "Conectado" en la barra de estado.
- Sin desconectar, abre sucesivamente los módulos _Perfiles_, _Esbelteces_, _Combinaciones_ y _Espectro_.

**Qué debería pasar:**

- Todos los módulos muestran "Conectado" (o el indicador equivalente).
- No hay reconexiones ni mensajes de "Conectando..." múltiples.
- La conexión se reutiliza (una sola instancia de conexión para todos).

**Verificación en SAP2000:**

- El modelo permanece abierto y accesible desde todos los módulos sin interrupciones.

**Resultado:**

- ☐ Pasó
- ☐ Falló

**Notas:** ************************************\_\_\_************************************

---

### 42. Cancelar conexión y mensaje de cancelación

**Qué hacer:**

- Con la conexión establecida (paso 41), en el panel _Conexión local_ haz clic en un botón "Cancelar" o "Desconectar".

**Qué debería pasar:**

- La interfaz web muestra el mensaje **textualmente**: "Se canceló la conexión."
- El indicador de estado cambia a "Desconectado".
- El diálogo de conexión se cierra.

**Verificación en SAP2000:**

- SAP2000 permanece abierto sin cambios, pero ya no responde a solicitudes de la web (si intenta operar sin reconectar, lo hará con error).

**Resultado:**

- ☐ Pasó
- ☐ Falló

**Notas:** ************************************\_\_\_************************************

---

### 43. Diálogo de combinaciones: redimensionable, una línea por item, scroll y persistencia

**Qué hacer:**

- En el módulo _Fundaciones_, en el Dashboard, haz clic en "Seleccionar combinaciones...".
- El diálogo de SeleccionCombinacionesDialog abre.
- Verifica:
  1. El diálogo tiene un tamaño mínimo de 700×500 px (intenta redimensionarlo a menos; debería rechazar la acción).
  2. Entre las dos listas (seleccionadas/disponibles) hay un splitter arrastrable.
  3. Cada combinación ocupa una línea en la tabla.
  4. Si el nombre es largo, hay scroll horizontal en la tabla (no ajusta al ancho de la celda).
- Selecciona algunas combinaciones.
- Cierra el diálogo haciendo clic en "Aceptar".

**Qué debería pasar:**

- El diálogo acepta redimensión hasta el mínimo (700×500).
- El splitter es arrastrable y mantiene sus posiciones.
- Los nombres largos se muestran completos con scroll, sin truncarse.
- La selección se guarda en el contexto del proyecto.

**Verificación:**

- Vuelve a abrir "Seleccionar combinaciones...".
- Las combinaciones que seleccionaste en el paso anterior aparecen aún marcadas.
- Cambia a otro módulo (p. ej. Perfiles) y regresa a Fundaciones.
- Abre nuevamente el diálogo de combinaciones.
- La selección persiste.

**Resultado:**

- ☐ Pasó (redimensión, splitter, scroll, persistencia funcionan)
- ☐ Parcial (algunos de los anteriores no funcionan) — describe: ************************************\_\_\_************************************
- ☐ Falló

**Notas:** ************************************\_\_\_************************************

---

### 44. Dashboard: botones de selección y etiqueta de estado

**Qué hacer:**

- En el módulo _Fundaciones_, ve al Dashboard (panel principal visible al entrar).
- Verifica que hay una fila con dos botones:
  - "Seleccionar fundaciones..."
  - "Seleccionar combinaciones..."
- Junto a los botones hay una etiqueta mostrando el estado actual.

**Qué debería pasar:**

- La etiqueta muestra una de estas formas:
  - Si fundaciones seleccionadas: `{n} fundación/es elegida/s` · `{n} combinación/es elegida/s`
  - Si todas las fundaciones: `todas las fundaciones guardadas` · `{n} combinación/es elegida/s`
  - Si sin combinaciones: `{n} fundación/es elegida/s` · `sin combinaciones elegidas`
  - Los separadores son " · " (espacio-punto-espacio).
- Los valores se actualizan cuando haces clic en los botones y cambias la selección.

**Verificación:**

- Haz clic en "Seleccionar fundaciones..." y selecciona algunas.
- Cierra el diálogo (clic "Aceptar").
- La etiqueta refleja el nuevo número.
- Repite con "Seleccionar combinaciones...".

**Resultado:**

- ☐ Pasó (etiqueta muestra estado correcto y se actualiza)
- ☐ Falló (etiqueta ausente, incorrecta o no se actualiza)

**Notas:** ************************************\_\_\_************************************

---

### 45. "Calcular todos los módulos" con cancelación en un módulo

**Qué hacer:**

- En el módulo _Fundaciones_, crea un snapshot con datos básicos.
- En el Dashboard, haz clic en "Calcular todos los módulos" (si existe un botón equivalente).
- Alternativamente, desde el panel de cálculo, inicia un cálculo de cada módulo secuencialmente.
- Cuando aparezca el diálogo de progreso de uno de los módulos, haz clic en "Cancelar".

**Qué debería pasar:**

- El mensaje "Se canceló el cálculo." aparece.
- El loop de cálculo se detiene; los módulos posteriores no se calculan.
- En el log (si es accesible), aparece una advertencia: "Cálculo de todos los módulos cancelado (se detuvo en '[título del módulo cancelado]')."

**Verificación:**

- Verifica que el módulo cancelado no tiene resultados nuevos.
- Abre el módulo siguiente en la secuencia.
- Sus resultados anteriores persisten (no se borraron) — el cálculo no afectó módulos no iniciados.

**Resultado:**

- ☐ Pasó (cancelación detiene el loop, mensaje muestra módulo)
- ☐ Falló (loop continúa o mensaje ausente)

**Notas:** ************************************\_\_\_************************************

---

### 46. Toolbar "Inicio": botón y navegación a pantalla de inicio

**Qué hacer:**

- En la barra de herramientas (toolbar) del proyecto, verifica que existe un primer botón/acción etiquetado "Inicio".
- El botón debe tener un tooltip "Ir a la pantalla de inicio".
- Haz clic en él.

**Qué debería pasar:**

- La interfaz navega a la pantalla de inicio del proyecto (generalmente un dashboard o página de resumen).
- Un evento "Inicio" se emite (internamente; puede no ser visible al usuario, pero el cambio de navegación es evidente).
- Un separador visual separa el botón "Inicio" del resto de la barra de herramientas.

**Resultado:**

- ☐ Pasó (botón existe, tooltip funciona, navega a inicio)
- ☐ Falló (botón ausente, no navega, o no hay separador)

**Notas:** ************************************\_\_\_************************************

---

### 47. Cargas nodales: aplicar a nodo inexistente muestra mensaje de error

**Qué hacer:**

- En el módulo _Fundaciones_, ve al panel "Cargas nodales".
- Crea o importa una carga nodal.
- En el campo "Nodo" (o identificador del nodo), escribe un ID que **no existe en el modelo SAP2000 actualmente abierto** (p. ej. "99999" si no hay nodos tan altos).
- Intenta aplicar la carga (haz clic en "Aplicar cargas nodales").

**Qué debería pasar:**

- La operación falla con un mensaje de error que contiene textualmente: "nodo(s) del grupo no existen en el modelo conectado" (o "los nodos no existen en el modelo conectado", según la forma).
- La carga **no se aplica** en SAP2000.
- Un botón o enlace de "Reintentar" o similar permite corregir el nodo y volver a intentar.

**Verificación en SAP2000:**

- Abre Asignar → Cargas nodales.
- Verifica que el nodo problemático **no aparece** en la lista de cargas nodales (no se creó).

**Resultado:**

- ☐ Pasó (mensaje muestra "nodo(s) del grupo no existen en el modelo conectado")
- ☐ Falló (no hay mensaje, o la carga se aplica a pesar del error)

**Notas:** ************************************\_\_\_************************************

---

### 48. Diálogos de selección multi-combo con drag y Shift+click

**Qué hacer:**

- En cualquier módulo (Deslizamiento, Asentamientos, etc.) que tenga un selector de combinaciones, abre el diálogo "Seleccionar combinaciones...".
- En la lista de combinaciones disponibles, selecciona un rango usando Shift+click (haz clic en una combinación, luego Shift+click en otra más abajo para seleccionar el rango completo).
- Intenta arrastrar (drag) una o varias combinaciones de la lista disponible a la lista seleccionada.

**Qué debería pasar:**

- Shift+click selecciona un rango continuo de combinaciones en la lista.
- Drag permite mover combinaciones entre listas (si el diálogo lo soporta).
- El estado se actualiza correctamente en la UI.

**Resultado:**

- ☐ Pasó (rango y drag funcionan como se espera)
- ☐ Falló (rango no selecciona correctamente o drag no funciona)

**Notas:** ************************************\_\_\_************************************

---

### 49. Guardar proyecto sin nombre muestra diálogo de nombre y confirmación

**Qué hacer:**

- En la pantalla principal del Workbench, haz clic en "Guardar" sin haber creado un proyecto previamente (o usa "Guardar como" con un nuevo nombre).
- Se debería abrir un diálogo pidiendo el nombre del proyecto.
- Ingresa un nombre (p. ej. "Mi Proyecto Prueba").
- Confirma la guardada.

**Qué debería pasar:**

- El diálogo cierra y muestra un mensaje: "Proyecto guardado: {nombre del proyecto}" (o similar).
- El proyecto se crea con el nombre ingresado.
- La interfaz refleja el nuevo nombre del proyecto (en el título o estado).

**Resultado:**

- ☐ Pasó (diálogo aparece, nombre se guarda, mensaje de confirmación mostrado)
- ☐ Falló (no hay diálogo o no se guarda)

**Notas:** ************************************\_\_\_************************************

---

### 50. Fundaciones/combos se mantienen tras recarga tras guardar

**Qué hacer:**

- En el módulo de Fundaciones, selecciona algunas fundaciones y combinaciones.
- Guarda el proyecto (usa "Guardar").
- Recarga la página del navegador (F5 o Ctrl+R).
- Vuelve a abrir el módulo de Fundaciones.

**Qué debería pasar:**

- Las fundaciones y combinaciones seleccionadas previamente se mantienen después de la recarga (se restauran desde el proyecto guardado).
- El estado de la selección es idéntico al de antes de la recarga.

**Resultado:**

- ☐ Pasó (selección restaurada tras recarga)
- ☐ Falló (selección perdida o no se restaura)

**Notas:** ************************************\_\_\_************************************

---

### 51. Presión de contacto hereda combos y Calcular habilitado

**Qué hacer:**

- Abre el módulo "Presión de contacto" en Fundaciones.
- Selecciona algunas fundaciones y combinaciones desde el panel principal o el diálogo de selección.
- Verifica que el botón "Calcular" esté habilitado.
- Abre Presión de contacto nuevamente (cierra y vuelve a abrir el módulo).

**Qué debería pasar:**

- Las combinaciones seleccionadas se heredan al módulo Presión de contacto.
- El botón "Calcular" está habilitado si hay tanto fundaciones como combinaciones seleccionadas.
- La herencia persiste entre módulos en la misma sesión.

**Resultado:**

- ☐ Pasó (combos heredados, Calcular habilitado)
- ☐ Falló (combos no heredados o Calcular deshabilitado)

**Notas:** ************************************\_\_\_************************************

---

### 52. "Calcular todos" incluye Presión de contacto

**Qué hacer:**

- En el Dashboard (Fundaciones), marca la casilla para "Presión de contacto" en el diálogo de exportación o en el listado de módulos.
- Asegúrate de que haya fundaciones y combinaciones seleccionadas.
- Haz clic en "Calcular todos los módulos".

**Qué debería pasar:**

- El proceso de cálculo incluye Presión de contacto en la secuencia de módulos a calcular.
- Se muestra progreso para Presión de contacto ("Calculando presión de suelo en SAP2000..." o similar).
- Los resultados de Presión de contacto se muestran en la tarjeta del Dashboard.

**Resultado:**

- ☐ Pasó (Presión de contacto calculado junto con otros módulos)
- ☐ Falló (Presión de contacto omitido de "Calcular todos")

**Notas:** ************************************\_\_\_************************************

---

### 53. Modelo no corrido muestra ventana "Modelo no corrido"

**Qué hacer:**

- En SAP2000, abre un modelo que **no ha sido corrido** (sin análisis ejecutado).
- En Fundaciones, selecciona una o más fundaciones y combinaciones.
- Intenta calcular cualquier módulo (p. ej. Deslizamiento).

**Qué debería pasar:**

- Se muestra una ventana (o modal) con el título "Modelo no corrido" o mensaje similar.
- El contenido indica que el modelo debe haber sido corrido con análisis antes de calcular.
- El cálculo no produce resultados.

**Resultado:**

- ☐ Pasó (ventana "Modelo no corrido" aparece)
- ☐ Falló (no hay ventana o cálculo continúa sin validar)

**Notas:** ************************************\_\_\_************************************

---

### 54. Exportación cierra y muestra resumen

**Qué hacer:**

- En el módulo de Fundaciones, selecciona al menos un módulo para exportar (p. ej. Deslizamiento).
- Asegúrate de que hay combinaciones y fundaciones seleccionadas.
- Haz clic en "Generar información de exportación..." y completa el proceso (o prueba con un módulo sin imágenes para acelerar).
- Verifica que no haya errores.

**Qué debería pasar:**

- El diálogo de exportación **se cierra** después de completar la generación.
- Una ventana con el resumen aparece (p. ej. "Módulos exportados: Deslizamiento. Se generaron X imagen(es)...").
- Las imágenes (si las hay) se incluyen en el paquete del Report Builder, no en una carpeta local "<proyecto>\_imagenes".

**Resultado:**

- ☐ Pasó (diálogo cierra, resumen mostrado, imágenes en Report Builder)
- ☐ Falló (diálogo no cierra o resumen no aparece)

**Notas:** ************************************\_\_\_************************************

---

### 55. Aviso para reconectar tras recargar la página

**Qué hacer:**

- Conecta con SAP2000 desde el menú "SAP2000" o desde Fundaciones.
- Recarga la página (F5) sin cerrar SAP2000Bridge.

**Qué debería pasar:**

- Aparece una barra arriba: "El puente con SAP2000 está disponible, pero esta ventana no está conectada. ¿Reconectar?", con los botones "Reconectar" y "Ahora no".
- "Reconectar" conecta de nuevo (la barra desaparece).
- "Ahora no" oculta la barra sin conectar, y no vuelve a aparecer sola en esa misma carga de página.
- Si no había ningún puente emparejado, la barra no aparece.

**Resultado:**

- ☐ Pasó
- ☐ Falló

**Notas:** ************************************\_\_\_************************************

---

### 56. Diseño de secciones: abrir el módulo (Beta) y revisar el aspecto

**Qué hacer:**

- Abre Fundaciones y mira la barra lateral y la portada.
- Entra a "Diseño de secciones (Beta)".

**Qué debería pasar:**

- En la barra lateral y en la portada el módulo sigue rotulado "Diseño de secciones (Beta)", con el título en gris (igual que "Diagramas de shells (Beta)").
- Al abrirlo NO muestra "En desarrollo.": aparece el panel completo (texto de ayuda, tabla "Nombre / Grupo SAP / Elementos / Estado", botones "Nuevo section cut...", "Nuevo section cut desde grupo SAP2000...", "Importar section cuts desde SAP2000...", "Eliminar del registro", "Seleccionar combinaciones...", "Extraer fuerzas", "Guardar esquema (gráfico)...", y a la derecha "Section cut:" con "Exportar PNG...").
- Sin conexión a SAP2000, cada botón que lee SAP2000 avisa "Sin conexión — Conéctese a SAP2000 primero.".
- El visor muestra "Sin section cuts definidos.".

**Resultado:**

- ☐ Pasó
- ☐ Falló

**Notas:** ************************************\_\_\_************************************

---

### 57. Diseño de secciones: nuevo section cut desde frames seleccionados (modelo SIN correr)

**Qué hacer:**

- Abre un modelo **sin correr** (desbloqueado) con varios frames y conéctalo en Fundaciones.
- En SAP2000 selecciona 2 o más frames en pantalla.
- En "Diseño de secciones (Beta)" pulsa "Nuevo section cut...", deja el nombre sugerido (SC_001) y acepta.
- Con el modelo SIN seleccionar frames, repite y comprueba el aviso "Sin selección".

**Qué debería pasar:**

- Se crea el grupo `GRP_SC_001` con esos frames y la fila aparece como "SC_001 / GRP_SC_001 / N / Creado en SAP2000".
- En SAP2000, Define > Section Cuts muestra el corte "SC_001" definido por grupo (Group / Analysis).
- Se pregunta "¿Seleccionar combinaciones ahora para extraer sus fuerzas?"; "Sí" abre el diálogo de combinaciones y, al aceptar, extrae y dibuja.
- Un nombre repetido avisa "Nombre repetido — Ya existe un section cut llamado '…'.".
- Sin frames seleccionados: "No hay frames (barras) seleccionados en SAP2000…" y no se crea nada.

**⚠ No verificado contra un SAP2000 real:** la creación edita la tabla "Section Cut Definitions" con `DatabaseTables.GetTableForEditingArray` + `SetTableForEditingArray` + `ApplyEditedTables(True)` (única vía del OAPI; el original del escritorio tampoco lo verificó). Si tu versión nombra distinto los campos de la tabla, o rechaza la edición, debería aparecer un error "No se pudo crear el section cut en SAP2000" con instrucciones para definirlo a mano; anota la versión de SAP2000, el mensaje exacto y los campos que informa (campos disponibles: …).

**Resultado:**

- ☐ Pasó
- ☐ Falló

**Notas:** ************************************\_\_\_************************************

---

### 58. Diseño de secciones: modelo bloqueado (ya analizado)

**Qué hacer:**

- Corre el análisis (Analyze > Run Analysis) en un modelo, de modo que quede bloqueado.
- En "Diseño de secciones (Beta)" crea un section cut (desde frames seleccionados o desde un grupo).
- Primero responde "No" y luego repite respondiendo "Sí".

**Qué debería pasar:**

- Aparece "El modelo de SAP2000 está bloqueado porque ya fue analizado… ¿Desbloquear el modelo y crear el section cut de todas formas?" con "No" como opción por defecto.
- "No": no se crea nada ni se desbloquea el modelo (la fila no aparece y SAP2000 sigue bloqueado).
- "Sí": el modelo se desbloquea, se crea el corte y aparece "Modelo desbloqueado — … corre el análisis (Analyze > Run Analysis) antes de extraer fuerzas.". Los resultados del análisis quedan borrados.
- Intentar "Extraer fuerzas" ahora muestra "Modelo no corrido".

**Resultado:**

- ☐ Pasó
- ☐ Falló

**Notas:** ************************************\_\_\_************************************

---

### 59. Diseño de secciones: nuevo section cut desde grupo e importar section cuts

**Qué hacer:**

- En SAP2000 define un grupo con nodos/shells (o frames) y, aparte, define a mano un section cut (Define > Section Cuts > Add Section Cut > Defined by Group, modelo sin correr).
- Pulsa "Nuevo section cut desde grupo SAP2000..." y elige el grupo (la lista no incluye "ALL").
- Pulsa "Importar section cuts desde SAP2000...".
- Repite la importación inmediatamente.

**Qué debería pasar:**

- Desde grupo: se crea el corte y la fila muestra el grupo elegido y la cantidad de elementos (nodos + shells + frames del grupo).
- Importar: trae los section cuts del modelo que no estén en el registro ("N section cut(s) importado(s) del modelo.") como "Creado en SAP2000".
- La segunda importación avisa "Los N section cut(s) del modelo ya están en el registro.".
- En un modelo sin section cuts: "El modelo conectado no tiene section cuts definidos…".

**⚠ No verificado contra un SAP2000 real:** la lectura de los section cuts existentes usa `DatabaseTables.GetTableForDisplayArray("Section Cut Definitions")`. Anota si la tabla devuelve el nombre y el grupo de cada corte en tu versión.

**Resultado:**

- ☐ Pasó
- ☐ Falló

**Notas:** ************************************\_\_\_************************************

---

### 60. Diseño de secciones: extraer fuerzas y gráfico (modelo corrido)

**Qué hacer:**

- Con el modelo **corrido** y un section cut ya creado (pasos anteriores, o importado), pulsa "Seleccionar combinaciones..." y elige 2 o más combinaciones.
- Pulsa "Extraer fuerzas" y, durante la extracción, prueba "Cancelar" una vez.
- Repite sin cancelar y revisa el gráfico y la tabla; compara 1 o 2 valores con Display > Show Tables / Section Cut forces de SAP2000 (unidades tonf, m).
- Cambia de módulo y vuelve a Diseño de secciones.

**Qué debería pasar:**

- Se muestra el modal de progreso con "Cancelar"; cancelar muestra "Se canceló el cálculo." y no cambia el gráfico.
- El gráfico "Section cut: … — Esfuerzos por combinación" tiene 3 subplots (Axial P, Corte V2/V3/V total con "máx: …", Momento M2/M3/M total con "máx: …") y las combinaciones rotadas 45° en el eje X.
- La tabla (Combinación, P, V2, V3, V total, M2, M3, M total, T; 3 decimales) resalta la fila de V total máximo y la de M total máximo.
- Mapeo esperado: P = F3, V2 = F1, V3 = F2, M2 = M2, M3 = M3, T = M1 de `SectionCutAnalysis`; V total = √(V2²+V3²) y M total = √(M2²+M3²).
- Al elegir otra fila de la tabla de section cuts, el combo "Section cut:" del visor cambia a ese corte.
- Al volver al módulo el gráfico y las combinaciones siguen ahí.
- Con un modelo sin correr: "Modelo no corrido"; con combinaciones sin resultados: "Sin resultados — SAP2000 no devolvió fuerzas para: …".

**⚠ No verificado contra un SAP2000 real:** `Results.Setup.SetSectionCutSelectedForOutput(nombre, True)` y `Results.SectionCutAnalysis(nombre, …)` (11 arreglos de salida: Obj, Elm, LoadCase, StepType, StepNum, F1, F2, F3, M1, M2, M3; se toman los últimos 11 del retorno). Si falla con "SectionCutAnalysis devolvio N arreglos, se esperaban al menos 11", anota el N y la respuesta que muestra el mensaje. Verifica también que los signos y ejes locales coincidan con los de SAP2000 y que el corte devuelva UNA fila por combinación (si devuelve varios pasos Max/Min por combinación, anótalo).

**Resultado:**

- ☐ Pasó
- ☐ Falló

**Notas:** ************************************\_\_\_************************************

---

### 61. Diseño de secciones: exportar PNG y guardar esquema

**Qué hacer:**

- Con un gráfico visible, pulsa "Exportar PNG..." y luego "Guardar esquema (gráfico)...".

**Qué debería pasar:**

- Cada botón descarga un archivo `Section cut <nombre>.png` con el gráfico completo (los 3 subplots, título y subtítulo "Grupo SAP: … | N elemento(s) | Generado: AAAA-MM-DD HH:MM"), fondo blanco.
- Desviación del escritorio: allí "Guardar esquema" lo agrega al proyecto de esquemas y "Exportar PNG..." pide la ruta con un diálogo; en la web ambos descargan el PNG (el navegador no puede escribir en disco ni hay proyecto de esquemas).

**Resultado:**

- ☐ Pasó
- ☐ Falló

**Notas:** ************************************\_\_\_************************************

---

### 62. Diseño de secciones: eliminar del registro

**Qué hacer:**

- Elige una fila de la tabla y pulsa "Eliminar del registro"; responde "No" y luego "Sí".

**Qué debería pasar:**

- Se pregunta "¿Eliminar '…' del registro? Esto NO borra nada en SAP2000, solo el registro de este módulo.".
- "Sí" quita la fila; el section cut sigue existiendo en SAP2000 (verifícalo en Define > Section Cuts y con "Importar section cuts desde SAP2000...").

**Resultado:**

- ☐ Pasó
- ☐ Falló

**Notas:** ************************************\_\_\_************************************

---

### 63. Diagramas de shells: franjas (definir, vista previa, detalle)

**Qué hacer:**

- Genera un diagrama con una fundación guardada como "Fuente:" (shells importados) y pulsa "Definir franja...".
- En el diálogo "Definir franja" revisa "Longitud:" (1), "Ancho:" (precargado con el ancho sugerido) y "Orientación:" (Horizontal/Vertical); acepta y mueve el mouse sobre el diagrama; haz clic para ubicarla.
- Pasa el mouse sobre la franja y haz clic derecho sobre su línea.

**Qué debería pasar:**

- El chip informa "Haz clic en el diagrama para ubicar el centro de la franja (la línea de preview sigue al mouse)." y una línea naranja punteada sigue al mouse.
- La franja queda dibujada y llamada "Franja 1"; al pasar el mouse sobre ella aparece "Franja 1 — máx. promedio {componente}: {valor}".
- El clic derecho abre "Detalle — Franja 1" con las barras, la línea, el área y los marcadores de máximo y mínimo; sin shells en la franja dice "Sin shells en la franja.".
- Con "Selección actual" o "Todas las guardadas" como fuente aparece "Franja no guardada" ("La franja se dibuja igual, pero no se guarda: elige una fundación guardada como fuente…").

**Resultado:**

- ☐ Pasó
- ☐ Falló

**Notas:** ************************************\_\_\_************************************

---

### 64. Diagramas de shells: pedestales (clic con snap, coordenadas, quitar)

**Qué hacer:**

- Pulsa "Definir pedestal..."; revisa "Largo:" y "Ancho:" (0.6), "Orientación:" (Largo en X/Largo en Y) y "Ubicar por:".
- Con "Clic en el diagrama" acepta, mueve el mouse y haz clic cerca del centro de la fundación; repite con "Coordenadas" ("Centro X:"/"Centro Y:" se habilitan sólo en ese modo).
- Abre "Más acciones ▾" > "Quitar todos los pedestales".

**Qué debería pasar:**

- Con "Clic en el diagrama" se ven las guías punteadas azules H/V por el centro sugerido y el rectángulo achurado de vista previa; cerca del centro el pedestal se engancha exactamente a él.
- Con "Coordenadas" el pedestal se crea al aceptar, sin clic.
- Con una fundación no guardada aparece "Pedestal no guardado"; con una guardada, el pedestal reaparece al volver a generar el diagrama o reabrir el proyecto.
- "Quitar todos los pedestales" los borra también de la fundación guardada.

**Resultado:**

- ☐ Pasó
- ☐ Falló

**Notas:** ************************************\_\_\_************************************

---

### 65. Diagramas de shells: detección automática de pedestales (modelo con columnas)

**Qué hacer:**

- Con un diagrama generado de shells apoyados sobre columnas verticales, abre "Más acciones ▾" > "Detectar pedestales automáticamente...".
- Desmarca algún candidato y pulsa "Aceptar seleccionados".
- Repite en un modelo sin columnas conectadas, y con SAP2000 desconectado.

**Qué debería pasar:**

- Aparece el progreso "Buscando frames verticales conectados a los shells..." y luego "Pedestales detectados automáticamente" con Incluir, Frame, Largo, Ancho, Orientación, X, Y y Aviso (filas aproximadas en amarillo).
- Se crean pedestales llamados "Pedestal <frame>" sólo con los marcados.
- Sin candidatos: "Sin candidatos — No se detectaron pedestales automáticos…"; error: "No se pudo completar la detección automática de pedestales. Revisa el log para más detalle."; sin conexión: el aviso de conexión de SAP2000.
- ⚠ No verificado contra un SAP2000 real: la lectura de secciones de frames (rectangular/circular) y de ejes locales.

**Resultado:**

- ☐ Pasó
- ☐ Falló

**Notas:** ************************************\_\_\_************************************

---

### 66. Diagramas de shells: lista lateral y "Elementos del diagrama"

**Qué hacer:**

- Pulsa "Ocultar lista de pedestales" y luego "Mostrar lista de pedestales".
- En el panel lateral expande "Pedestales" y "Franjas" (colapsados al inicio) y una fila; edita "Nombre:", "Centro X:", "Centro Y:", "Largo:", "Ancho:" y "Largo en X (si no, en Y)"; usa "Destacar" y "Eliminar".
- Abre "Más acciones ▾" > "Elementos del diagrama..." y prueba "Mostrar" y "Borrar" en cada fila, sin cerrar la ventana.

**Qué debería pasar:**

- Cada edición redibuja el diagrama de inmediato (3 decimales, paso 0.1).
- "Destacar" resalta el elemento en rojo y, en franjas, expande su fila; un clic sobre el elemento hace lo mismo.
- "Elementos del diagrama" es una ventana no modal con los grupos "Franjas" y "Pedestales" y los textos "Sin franjas definidas."/"Sin pedestales definidos." cuando están vacíos.

**Resultado:**

- ☐ Pasó
- ☐ Falló

**Notas:** ************************************\_\_\_************************************

---

### 67. Diagramas de shells: tabla de valores y tabla de esfuerzos por franja

**Qué hacer:**

- Pulsa "Ver tabla de valores...", ordena por X y haz doble clic en una fila.
- Define una franja visible y pulsa "Ver tabla de esfuerzos por franja..." (prueba también "Cancelar" durante la extracción).
- Haz doble clic en un valor de la tabla.
- Sin franjas visibles, intenta abrir la tabla por franja.

**Qué debería pasar:**

- La tabla de valores marca el punto con una estrella dorada y su valor en el diagrama.
- El progreso "Extrayendo esfuerzos por combinación de SAP2000..." recorre todas las combinaciones (las envolventes como "[max]" y "[min]"); la tabla trae una fila por franja visible y combinación, con el extremo gobernante o "—".
- El doble clic cambia combinación y componente, activa "Ver diagrama de esfuerzos en franjas", regenera el diagrama y destaca esa franja (la franja debe estar guardada en la fundación para sobrevivir a la regeneración).
- Sin franjas visibles: "Sin datos — Genera un diagrama y define al menos una franja visible antes de pedir la tabla por franja.".

**Resultado:**

- ☐ Pasó
- ☐ Falló

**Notas:** ************************************\_\_\_************************************

---

### 68. Diagramas de shells: persistencia de franjas y pedestales y Renombrar

**Qué hacer:**

- Con una fundación guardada como fuente, define franjas y pedestales; guarda el proyecto y reábrelo (o cambia de módulo y vuelve).
- En "Fundaciones guardadas..." marca la fundación y usa "Renombrar..."; vuelve a generar el diagrama con el nuevo nombre.

**Qué debería pasar:**

- Franjas y pedestales (con nombre, medidas, orientación y visibilidad) reaparecen al generar o restaurar el diagrama.
- Tras "Renombrar..." siguen presentes bajo el nombre nuevo; los proyectos guardados antes de esta función se abren sin errores y sin franjas ni pedestales.

**Resultado:**

- ☐ Pasó
- ☐ Falló

**Notas:** ************************************\_\_\_************************************

---

## Registro

**Fecha de prueba:** ********************************\_********************************

**Ingeniero/Operador:** ******************************\_\_******************************

**Versión SAP2000 (Ayuda → Acerca de):** **********************\_\_\_**********************

**Build de SAP2000:** ******************************\_\_\_\_******************************

**Versión Windows (o Sistema operativo):** ********************\_\_\_\_********************

**Rama del repositorio probada:** ************************\_\_\_\_************************

**Commit / Hash (git log --oneline -1):** ********************\_\_\_\_********************

### Resumen de resultados

Pasos completados: **\_** / 54

Pasos que pasaron: **\_** / **\_** completados

Pasos que fallaron: **\_** / **\_** completados

## Módulos integrados (diagrama, hormigón simple y equilibrio plástico)

Los puentes de estos tres módulos sólo se probaron con un COM simulado. Este guion los valida con SAP2000 real. Requiere un `SAP2000Bridge` compilado con estos módulos; si el puente es anterior, cada diálogo avisa «Su SAP2000Bridge es anterior a este módulo».

### 70. Diagrama de interacción: grupos y demandas

- Abre un modelo con un grupo de columnas (por ejemplo `COL`) y corre el análisis.
- En Diagrama de interacción, pestaña SAP2000, conecta y elige el grupo y varias combinaciones.
- Esperado: el diálogo lista los grupos y las combinaciones del modelo; «Traer desde SAP2000» devuelve una fila por frame, combinación y estación, con N en compresión positiva y momentos en kN·m.
- Repite con una selección de columnas en SAP2000 (modo «seleccionados»): mismas filas, sólo para los frames seleccionados.

### 71. Hormigón simple: demanda de una viga

- En Hormigón simple, Armado de viga, «Conexión con SAP2000»: lista los frames con longitud y sección y las combinaciones.
- Elige un frame y 2 o más combinaciones y pulsa «Traer envolvente».
- Esperado: vista previa con `xs`, `mu_pos`, `mu_neg` y `vu_max`; los valores coinciden con el diagrama de momentos y cortantes de SAP2000 (tonf, m). Con «invertir M3» el signo de los momentos se invierte.
- Un frame inexistente responde con el mensaje de error del puente, sin colgar la web.

### 72. Equilibrio plástico: grupo y frame

- «Importar SAP» en la pestaña Grupos: elige un grupo de diagonales y un combo sísmico. Esperado: un miembro por frame con `Tn_req = Pn_req` = máxima |P| del combo (en tonf), y H y L del frame; si falla la geometría de un frame, H = L = 5,0 con un aviso.
- «H y L desde SAP2000» en la pestaña Proyecto: selecciona un nivel de la tabla, escribe el nombre del frame y los combos sísmico y gravitacional. Esperado: al aplicar, el nivel recibe H, Tdis (axial sísmico) y qgrav (axial gravitacional) y el marco recibe L.
- Con el modelo en unidades distintas de tonf y m, los valores deben salir en las unidades del modelo y SAP2000 debe quedar en sus unidades originales al terminar.

### Defectos encontrados

(Anota cada defecto exacto, con paso número, descripción breve, y si es bloqueante o menor.)

1. ***

   ***

   ***

2. ***

   ***

   ***

3. ***
   ***
   ***

### Observaciones generales

(Anota cualquier comportamiento inesperado, limitaciones encontradas, o aspectos que funcionaron muy bien.)

---

---

---

---

## Decisión final

- ☐ **Apto:** Todos los pasos se completaron sin defectos bloqueantes. El puente COM está listo para uso en producción.
- ☐ **Apto con salvedad:** Algunos pasos fallaron, pero no son bloqueantes para el MVP. Listar el trabajo necesario abajo.
- ☐ **No apto:** Uno o más defectos bloqueantes detectados. No continuar sin resolver.

### Trabajo necesario (si aplica)

---

---

---

## Anexo: validación de caducidad y revocación del token (S-07, tras reconstruir el puente)

Ejecutar en Windows con el puente reconstruido con `build-bridge.yml`.

1. Empareja la web con el puente (acepta la solicitud en la bandeja) y conecta a un modelo: debe funcionar con normalidad.
2. Con `SAP2000_AGENT_TOKEN_TTL_HOURS=0.01` (unos 36 s) en el entorno del puente, emparejar de nuevo, esperar a que pase ese tiempo y pulsar «Conectar»: debe aparecer «El token del puente venció; vuelva a conectar» y la web debe pedir reemparejar (el emparejamiento guardado se borró de `localStorage`). Restaurar el valor por defecto (12 h) después.
3. Con un emparejamiento vigente, pulsar «Desconectar»: en `localStorage` ya no existe `sap_bridge_pairing` y la entrada desaparece de «Conexiones emparejadas» en la bandeja. Reutilizar el secreto antiguo (p. ej. con `curl -H "Authorization: Bearer <secreto>" http://127.0.0.1:8765/v1/sap/status`) debe dar `401`.
4. Emparejamientos hechos con el puente anterior (si existen en `pairings.json` antes de la actualización) deben seguir funcionando tras actualizar.
5. Modo local (token de arranque): «Desconectar» no debe romper la conexión local ni revocar nada.


## Puente compartido: SAP Graph y SAP2000 Viewer

Requisitos: SAP2000 abierto con un modelo guardado y el `SAP2000Bridge` de esta versión (con `actions/graph` y `actions/viewer`).

### SAP Graph (`https://sap-graph.invalid`)
1. Abrir la web, pulsar conectar y aprobar el código en la ventana del puente.
2. Importar el modelo: deben aparecer puntos, barras, shells, secciones y unidades correctas; revisar los avisos.
3. Comprobar que la web no puede llamar a rutas de otras familias (el puente responde 403).
4. Esperado si falla: aviso «No se pudo determinar el orden del código de retorno» → hay un modelo abierto y guardado.

### SAP2000 Viewer (`https://viewer.invalid`)
1. Emparejar como arriba. Leer el snapshot: geometría, secciones, apoyos y cargas.
2. Con el modelo analizado: casos y combinaciones, razones D/C, esfuerzos de área, desplazamientos y esfuerzos por barra.
3. Factores K: `preview` (diferencias y nonce), `apply` con confirmación; verificar que se creó la copia de seguridad en `%LOCALAPPDATA%\EngineeringWorkbench` y que la relectura coincide. Si el modelo está bloqueado, pide desbloquear.
4. Cerrar el puente y comprobar que no queda el proceso COM.

## Placa Base (`https://placa-base.invalid`)

Puente compartido: `bridge/placa_base.py` (rutas `/v1/sap/actions/placa-base/*`). Ninguna de estas pruebas se ha hecho con SAP2000 real: el código se verificó sólo con COM falso (`tests/test_bridge_placa_base.py`).

1. **Emparejar.** Abrir la web de Placa Base, pulsar conectar y aprobar el código en la ventana del puente. Esperado: la web muestra el nombre del modelo abierto en SAP2000.
2. **Listar cargas.** Esperado: aparecen todos los casos y combinaciones del modelo, y en `seleccion` los que SAP tiene marcados para salida. Si aparece el aviso de selección no leída (`seleccion_desconocida`), revisar en SAP2000 la selección de salida: ese aviso no se da por no seleccionado.
3. **Seleccionar.** Marcar dos casos y una combinación; comprobar en SAP2000 que la selección de salida coincide. Incluir un nombre inexistente: debe quedar en `ignorados` y no dar error. Mensajes esperados: sin nombres, «Seleccione al menos un estado de carga o combinación» (422); si ninguno existe, «Ninguno de los estados de carga o combinaciones indicados existe en el modelo» (422). Si SAP rechaza una marca, 409 con el código devuelto por la API.
4. **Listar nodos.** Seleccionar en SAP2000 un nodo restringido y uno libre. Esperado: ambos en `seleccionados`; sólo el restringido en `para_diseno`. Sin selección, `para_diseno` coincide con `restringidos`.
5. **Reacciones con ángulo.** Con el análisis ejecutado, pedir reacciones de un nodo restringido sin ángulo y comparar P, Vx, Vy, Mx, My y Mz con la tabla de reacciones de SAP2000, en tonf y m. Repetir con un ángulo de 30 grados: Vx, Vy, Mx y My deben girar y P y Mz no; `ejes` debe ser `locales`. Comprobar que las unidades de SAP2000 quedan como estaban antes de la lectura. Sin análisis, el mensaje esperado es «SAP2000 no devolvió reacciones. Ejecute el análisis y revise la selección de resultados.» (409).
6. **Columna en nodo con sección I.** En el nodo de base de una columna con perfil I, comparar `section`, `length_m` (m) y las cuatro dimensiones (`depth_cm`, `width_cm`, `flange_thickness_cm`, `web_thickness_cm`, en cm) con las propiedades del perfil en SAP2000. Un nodo sin columna y un nodo de viga horizontal deben dar `columna: null`. Una sección que no es I (por ejemplo, rectangular) debe dar la sección y la longitud, con las cuatro dimensiones en `null`.
7. **Resaltar.** Tras resaltar varios nodos, en SAP2000 esos nodos aparecen seleccionados y la vista se actualiza. Si SAP rechaza alguno de los pasos, la respuesta es un 409 con el código devuelto por la API.
8. **Alcance.** Desde el origen de Placa Base, `GET /v1/sap/actions/graph/model` debe responder 403. Sin token, cualquier ruta de Placa Base responde 401.

## CAD-DRWR — validación en Windows (ZWCAD/AutoCAD y SAP2000 reales)

Estas pruebas NO se pueden ejecutar en CI (COM simulado en `tests/test_cad_drwr_bridge*.py`). Hay que hacerlas con el puente recompilado (`build-bridge.yml`) y **vuelto a emparejar**.

1. Abre ZWCAD (o AutoCAD) con un dibujo vacío y el puente. En el módulo «CAD-DRWR» pulsa **Enviar a CAD**: el diálogo debe mostrar programa y documento.
2. **Sin clic**: desmarca «Ubicar con clic» y envía la placa base. Comprueba capas, cotas nativas (editables), textos, tablas y que las entidades omitidas (si hay) se listan con motivo.
3. **Con clic**: marca «Ubicar con clic». Debe aparecer «Haga clic en la ventana de ZWCAD/AutoCAD…» y la ventana del CAD pasar al frente. Haz clic: el dibujo debe quedar con su origen en ese punto. Repite cancelando con Esc: el mensaje debe mostrar el error real y ofrecer «Reintentar» y «Enviar sin ubicar».
4. Repite el paso 3 con el navegador maximizado y con otra ventana encima del CAD (el foco forzado con `AttachThreadInput` es lo que más depende de la versión de Windows). Anota si aparece `RPC_E_SERVERFAULT` (-2147417851).
5. Envía los 7 módulos y revisa visualmente contra la vista previa web y contra los DXF exportados.
6. **SAP2000**: abre un modelo con un grupo que contenga nodos y shells de fundaciones (y columnas apoyadas). En «Fundación SAP2000» pulsa «Leer grupos» y luego «Leer fundación»: debe mostrar progreso y cancelarse. Verifica con el modelo: número de fundaciones (shells adyacentes = una fundación), contorno único con solo las esquinas, espesores, y pedestales con su dimensión real (incluida una columna apoyada en un nodo INTERIOR de la malla).
7. Modelo grande: comprueba que la lectura supera los 30 s del actor sin perder el resultado (el trabajo sigue en segundo plano y el estado pasa a «listo»).
8. Cancela una lectura a mitad y comprueba que las unidades de SAP2000 se restauran (el original las fuerza a N-mm-°C durante la lectura).

Pendiente de verificar en real: formas de retorno de `GetNameList`, `GetAssignments`, `GetPoints`, `GetLocalAxes`, `GetShell`, `GetRectangle`, `GetCoordCartesian`; conteo anidado de `CoInitialize`/`CoUninitialize`; comando `getpoint` con `USERR1..3`; formato `VARIANT` de `AddLightWeightPolyline`, `AddDimRotated` y `AddHatch`; estilo `txt.shx` y grosores de capa; que un `pick` que supera su `timeout_s` mantiene bloqueado el hilo COM hasta que el usuario pulse Esc (las demás llamadas CAD esperan en cola).

## Zapatas biaxiales (fase 2)

Nada de esta fase se ha ejecutado contra SAP2000 real. Las pruebas del repositorio usan dobles de la API COM y los resultados de referencia salen del escritorio Fundaciones (`837a7ca`). Las pruebas del puente están en `tests/test_bridge_zapata_biaxial.py` (108 con COM falso y 2 `xfail` que documentan un defecto preexistente de `_call_get_coord_cartesian` con el orden de comtypes; ver `docs/MODULES_SOURCE.md`).

Este guion lo ejecuta un ingeniero con SAP2000 instalado en Windows. Hay que anotar en cada paso lo que se ve, no lo que se espera.

**Pantallas de la web:** la página «Zapatas biaxiales» tiene los botones de la barra «SAP2000» (Conectar, Importar nodos y reacciones…, Actualizar reacciones, Editar grupos…, Ver reacciones…) y «Exportar a SAP2000…». Cada paso de este guion puede hacerse con esas pantallas o, para aislar un fallo, con llamadas HTTP directas al puente (`http://127.0.0.1:8765`, con `Authorization: Bearer`) y al backend (`/zapata-biaxial/...`), por ejemplo con `Invoke-RestMethod` en PowerShell. Anote en cada paso qué vía usó.

### Prerrequisitos

- **SAP2000 instalado en Windows:** versión ______ (Ayuda → Acerca de SAP2000).
- **Modelo A (analizado):** con una base de nodos apoyados (restringidos) bajo una columna o carga, un grupo de nodos con esos apoyos, al menos dos casos de carga y una combinación. Ejecutar el análisis. Tras el análisis, SAP2000 lo muestra como bloqueado.
- **Modelo B (vacío):** Archivo → Nuevo modelo → en blanco, unidades Ton_m_C, sin guardar.
- **Escritorio Fundaciones@837a7ca** abierto con el mismo modelo A, para comparar reacciones e importaciones.
- **Puente en ejecución** y emparejado (ver los prerrequisitos de esta matriz y `docs/PUENTE_COMPARTIDO.md`).
- **Backend** local con el módulo de zapatas, para las rutas `/zapata-biaxial/*`.

### Tolerancias de comparación

Propuesta para esta prueba manual. No se derivó de una medición: si un valor supera la tolerancia, el paso se marca como fallo y se anota el valor.

| Magnitud | Unidad | Tolerancia absoluta |
|---|---|---|
| Coordenadas X, Y, Z de nodos | m | 1e-6 |
| Fuerzas F1, F2, F3 de reacciones y cargas | tonf | 1e-4 |
| Momentos M1, M2, M3 de reacciones y cargas | tonf·m | 1e-4 |
| Cargas de la fundación importada (P, Vx, Vy, Mx, My) | tonf, tonf·m | 1e-3 |
| Dimensiones de la malla y del pedestal, espesor de shell | m | 1e-6 |
| Relectura frente al plan: nombres y tipos | — | exacta |
| Relectura frente al plan: valores numéricos | — | 1e-6 |

### 1. Conectar y leer el estado del modelo

**Qué hacer:**

- Con SAP2000 abierto en el modelo A, arranca el puente y comprueba que se empareja.
- Llama a `GET /v1/sap/actions/zapata-biaxial/estado`.
- Repite la llamada con el modelo B abierto.

**Qué debería pasar:**

- Respuesta 200 con `modelo` igual al nombre del archivo abierto.
- Modelo A: `bloqueado: true` (tiene análisis). Modelo B: `bloqueado: false`.
- El valor de `bloqueado` coincide con lo que muestra SAP2000.

- ☐ Pasó
- ☐ Falló

Observaciones: ____________________

### 2. Leer casos y combinaciones

**Qué hacer:**

- Con el modelo A abierto, llama a `GET .../casos`.
- Revisa en SAP2000 la lista de casos y combinaciones (Define → Load Cases, Define → Load Combinations).
- Revisa la selección de salida actual (Analyze → Set Analysis Output, o Results → Setup).
- Con el modelo B, llama de nuevo a `GET .../casos`.

**Qué debería pasar:**

- Los nombres de `casos` y `combinaciones` coinciden con SAP2000, sin omitir ni añadir ninguno.
- `seleccion` coincide con la selección de salida de SAP2000 y `seleccion_desconocida` es `false`.
- En el modelo B aparecen sólo los casos por defecto (p. ej. DEAD y MODAL).

- ☐ Pasó
- ☐ Falló

Observaciones: ____________________

### 3. Nodos por selección

**Qué hacer:**

- En SAP2000 (modelo A), selecciona cuatro nodos de la base de la fundación.
- Llama a `POST .../nodos` sin cuerpo.
- Anota las unidades de SAP2000 antes y después de la llamada (barra de estado).

**Qué debería pasar:**

- Respuesta 200 con `origen: "seleccion"`, `unidades: "tonf-m"` y cuatro filas.
- Las coordenadas coinciden con las de SAP2000 (ver el nodo en Assign → Joint, o Display → Show Tables → Joint Coordinates).
- La selección de SAP2000 sigue siendo los mismos cuatro nodos.
- Las unidades de SAP2000 no han cambiado.

- ☐ Pasó
- ☐ Falló

Observaciones: ____________________

### 4. Nodos sin selección

**Qué hacer:**

- En SAP2000 (modelo A), deselecciona todo.
- Llama a `POST .../nodos` sin cuerpo.
- Compara los nodos devueltos con los que tienen restricciones (Assign → Joint → Restraints, o la tabla de restricciones).
- Con el modelo B abierto y sin selección, llama de nuevo a `POST .../nodos`.
- Opcional: con más de 500 nodos seleccionados en un modelo grande, comprueba el rechazo.

**Qué debería pasar:**

- Modelo A: `origen: "restringidos"`, y la lista coincide con los nodos restringidos. La selección sigue vacía.
- Modelo B: 409 con `code: "sin_nodos"`.
- Con más de 500 seleccionados: 409 con `code: "demasiados_nodos"`. Si el modelo no tiene tantos nodos, marcar este punto como no aplicable.

- ☐ Pasó
- ☐ Falló
- ☐ No aplicable

Observaciones: ____________________

### 5. Reacciones (comparar con el escritorio)

**Qué hacer:**

- En el modelo A analizado, con los cuatro nodos del paso 3, llama a `POST .../reacciones` con un caso y una combinación.
- En el escritorio Fundaciones, con el mismo modelo, importa los mismos nodos y el mismo caso, y anota las reacciones de cada nodo.
- En SAP2000, compara con Display → Show Tables → Joint Reactions en tonf y m.
- Repite con un nodo sin análisis (modelo B con nodos creados, sin correr el análisis) y con un caso que no existe.

**Qué debería pasar:**

- Columnas exactamente: `Joint, OutputCase, CaseType, StepType, F1, F2, F3, M1, M2, M3`.
- `CaseType` es `Combination` para la combinación y `LoadCase` para el caso.
- Cada valor coincide con SAP2000 y con el escritorio dentro de la tolerancia.
- Tras la llamada, la selección de salida de SAP2000 contiene sólo los casos y combinaciones pedidos (la llamada no restaura la selección anterior; es lo esperado).
- Sin análisis: 409 con `code: "sin_resultados"`. Caso inexistente: 409 con `code: "caso_desconocido"`.

- ☐ Pasó
- ☐ Falló

Observaciones: ____________________

### 6. Importación: modo «única equivalente»

**Qué hacer:**

- Usa las coordenadas (`nodos`) y las reacciones del paso 5 como tablas.
- Llama a `POST /zapata-biaxial/importar` del backend con `modo: "unica_equivalente"`, un grupo `G1` con los cuatro nodos, los casos del paso 5 y la geometría de la fundación (3 × 2 m, hp = 1 m).
- En el escritorio, haz lo mismo con «Única equivalente» y la misma geometría.
- Compara las cargas de cada caso (N, Vx, Vy, Mx, My) y la posición del centro de gravedad.

**Qué debería pasar:**

- Una sola fundación; las cargas de cada caso coinciden con el escritorio dentro de la tolerancia.
- La salida trae `grupos`, `casos_sap`, `reacciones`, `coordenadas` y `cargas`.

- ☐ Pasó
- ☐ Falló

Observaciones: ____________________

### 7. Importación: modo «individuales» con merge

**Qué hacer:**

- Importa el grupo `G1` (un nodo por grupo, p. ej. `Nodo 1` y `Nodo 3`) con `modo: "individuales"` y sin `actual`. Guarda la salida como `A`.
- Importa un grupo `G2` (dos nodos distintos de `G1`) con `modo: "individuales"`, pasando `actual: A` y una `marca` distinta (p. ej. `marca: "G2"`).
- En el escritorio, repite la misma secuencia: importa `G1` y después `G2` en modo individuales.
- Compara las cargas resultantes.

**Qué debería pasar:**

- Tras el segundo paso, la fundación contiene `G1` y `G2`, sin casos duplicados.
- En `individuales` todos los casos se evalúan sobre una sola zapata cuyo centro es el punto medio del rectángulo de los nodos registrados. Cada carga es `Mx = M1 + F3·dy` y `My = M2 − F3·dx` (dx, dy en ejes de planta SAP2000: X a la derecha, Y arriba). Un nodo en el centro no cambia sus momentos.
- Las cargas por nodo y caso coinciden con el escritorio dentro de la tolerancia.
- Si la importación se repite con la misma marca, no se duplican los datos (comprobarlo con una tercera llamada).

- ☐ Pasó
- ☐ Falló

Observaciones: ____________________

### 8. Importación: modo «agrupado»

**Qué hacer:**

- Con los grupos `G1` y `G2` del paso 7, llama a `POST /zapata-biaxial/importar` con `modo: "agrupado"`.
- En el escritorio, haz la misma importación en modo «Agrupado».
- Compara las cargas.

**Qué debería pasar:**

- Una sola fundación con una carga por cada par (grupo, caso), en lugar de una por nodo.
- Las cargas de cada par coinciden con el escritorio dentro de la tolerancia.

- ☐ Pasó
- ☐ Falló

Observaciones: ____________________

### 9. Exportar: revisión de un plan en el modelo vacío

**Qué hacer:**

- Llama a `POST /zapata-biaxial/plan-exportacion` del backend con geometría Lx = 3, Ly = 2, hz = 0,5, hp = 1, Lpx = Lpy = 0,5, tamaño de malla 0,5, una carga con P = 10, Vx = 1, Vy = 0,5, Mx = 0,2, My = 0,1, y los valores por defecto del resto.
- Llama a `POST .../exportar/preview` con el modelo B abierto y el plan obtenido.

**Qué debería pasar:**

- Plan con `resumen`: `divisiones: [6, 4]`, `nodos: 36`, `shells: 24`, `frames: 1`, `resortes: 35`, `patrones: 1`.
- Preview: `ok: true`, `bloqueado: false`, `conflictos: []`, `material_existe: false`.
- `avisos` incluye el aviso de que los resortes no se han validado en SAP2000 real.
- `huella` tiene 16 caracteres hexadecimales.

- ☐ Pasó
- ☐ Falló

Observaciones: ____________________

### 10. Exportar: aplicar en el modelo vacío y revisar visualmente

**Qué hacer:**

- Llama a `POST .../exportar/apply` con el mismo plan, la `huella` del paso 9 y `confirmed: true`.
- En SAP2000, revisa la zapata en vista 3D y en planta.
- Revisa cada elemento según la lista de abajo.

**Qué debería pasar:**

- Respuesta 200 con `ok: true`, `verificado: true` y `creado` con: material 1, secciones_frame 1, secciones_shell 1, nodos 36, shells 24, frames 1, patrones 1, cargas 1, resortes 35.
- Malla: 6 × 4 shells (24 áreas) con la cara superior en z = 0.
- Pedestal: un frame vertical de z = 0 a z = 1 m, con su extremo inferior en el nodo central de la malla (x = 0, y = 0, z = 0) y conectado a él (no coincidente con otro nodo). Comprobarlo con Assign → Frame o con la vista de conexión.
- Secciones en metros: `PEDESTAL_SEC_<sufijo>` de 0,5 × 0,5 m (no 500 mm); `ZAPATA_SEC_<sufijo>` con espesor 0,5 m y tipo de shell **Thick**.
- Material `HORMIGON_FUND_<sufijo>` con E ≈ 2,3875 × 10⁶ tonf/m² (15 100 · √250 · 10) y peso específico 2,5 tonf/m³.
- Patrón `FUND_CARGAS_<sufijo>` de tipo Other, peso propio 0, y su caso de carga.
- Cargas en el nodo superior `NS_<sufijo>`: F1 = 1, F2 = 0,5, F3 = −10, M1 = 0,2, M2 = 0,1, M3 = 0.
- Resortes: 35 nodos de la malla, cada uno con rigidez vertical U3 de 857,14 tonf/m (5 000 · 3 · 2 / 35). La relectura automática no comprueba los resortes: revisarlos a mano (Assign → Joint → Springs).
- Los nombres del plan (nodos, shells, frame, patrones) aparecen tal cual en SAP2000.

- ☐ Pasó
- ☐ Falló

Observaciones: ____________________

### 11. Exportar: modelo bloqueado (debe negarse)

**Qué hacer:**

- Con el modelo A (analizado y bloqueado), llama a `exportar/preview` y después a `exportar/apply` con la `huella` del preview y `confirmed: true`.
- Anota el número de nodos y de áreas antes y después.

**Qué debería pasar:**

- Preview: `ok: false`, `bloqueado: true`, y `avisos` con el mensaje de modelo bloqueado.
- Apply: 409 con `code: "bloqueado"`.
- El número de nodos y áreas no cambia.

- ☐ Pasó
- ☐ Falló

Observaciones: ____________________

### 12. Exportar: nombres duplicados (conflicto)

**Qué hacer:**

- Con el modelo B del paso 10 (ya creado), vuelve a llamar a `exportar/preview` con el mismo plan.
- Llama a `exportar/apply` con `confirmed: true`.
- Opcional: cambia en el plan un nombre de nodo solo en mayúsculas o minúsculas y repite la revisión.

**Qué debería pasar:**

- Preview: `ok: false`, `conflictos` con «Ya existe…» para cada nodo, shell, frame y patrón, y `material_existe: true`.
- Apply: 409 con `code: "conflictos"` y la lista `conflictos`.
- Con el nombre cambiado solo en mayúsculas, aparece conflicto igualmente (SAP2000 no distingue mayúsculas).
- No se crea ni modifica ningún objeto.

- ☐ Pasó
- ☐ Falló

Observaciones: ____________________

### 13. Exportar: cancelar antes de confirmar

**Qué hacer:**

- Con el modelo B vacío (sin la zapata), llama a `exportar/preview` y no llames a apply. Cuenta nodos, áreas y frames.
- Llama a `exportar/apply` con `confirmed: false` (o sin el campo). Comprueba el conteo.
- Llama a `exportar/apply` con `confirmed: true` y la `huella` del plan original, pero con `hz` cambiado en el plan (p. ej. 0,6). Comprueba el conteo.

**Qué debería pasar:**

- Tras el preview, el modelo no cambia.
- Con `confirmed` ausente o `false`: 422, y el modelo no cambia.
- Con `huella` que no coincide: 409 con `code: "plan_cambiado"`, y el modelo no cambia.

**Nota:** el puente no ofrece cancelar un apply en curso. Si el proceso del puente se interrumpe durante una escritura, el estado del modelo es indeterminado: hay que revisarlo a mano antes de reintentar. Este paso no prueba esa interrupción.

- ☐ Pasó
- ☐ Falló

Observaciones: ____________________

### Registro de la validación

| Campo | Valor |
|---|---|
| Versión de SAP2000 | |
| Versión de Fundaciones (escritorio) | `837a7ca` o la que se use |
| Fecha | |
| Responsable | |
| Pasos pasados / fallidos / no aplicables | |
| Fallos abiertos | |

## Acero AISC 360-22 — fuerzas de barras

**Validado con SAP2000 23.3.1 (sonda v3, 2026-10-14).** Lectura de fuerzas, signo de P, eje fuerte M3, combinaciones y Cb, según la sonda `scripts/sonda_acero_aisc360.py` (22 PASS, 3 INCONCLUSO; registro en `docs/TRASPASO_ACERO_AISC360.md`). Este guion sirve para repetir la comprobación sobre el modelo de prueba.

Puente: `bridge/acero_aisc360_fuerzas.py`. Rutas: `/v1/sap/actions/acero-aisc360/combos`, `selected` y `fuerzas` (ver `docs/SAP_ACTIONS.md`).

**Preparación:** modelo con una viga simple de 6 m apoyada en sus extremos y una carga uniforme de 10 kN/m en una combinación `U1` (sin peso propio). Una barra en voladizo de 3 m con carga puntual de 10 kN en el extremo libre, en la combinación `U1`. Ambas con sección W sencilla y las unidades del modelo en kip-ft, para comprobar la restauración.

**Pasos:**

1. `GET combos`. Comprueba que `U1` aparece en `combinaciones` y el caso DEAD en `casos`.
2. Selecciona la viga en SAP2000 y `GET selected`. Comprueba la sección y que `longitud_m` es 6,0 (± 1e-6).
3. `POST fuerzas` con `{"frames": ["VIGA"], "combos": ["U1"]}`. Comprueba:
   * `Mux_kNm` ≈ 45,0 (wL²/8), `Vux_kN` ≈ 30,0 (wL/2), `Pu_kN` = 0, `x_Mmax_m` ≈ 3,0.
   * `Cb` ≈ 1,136 (50/44).
   * `unidades` = `kN-m`. En SAP2000, las unidades presentes vuelven a kip-ft tras la llamada.
4. `POST fuerzas` con el voladizo. Comprueba `Mux_kNm` ≈ 30,0 en x = 0, `Vux_kN` ≈ 10,0 y `Cb` ≈ 1,667 (5/3).
5. `POST fuerzas` con `{"frames": ["VIGA", "NOEXISTE"], "combos": ["U1"]}`. Comprueba que responde 200, que solo aparece VIGA en `frames` y que `warnings` menciona NOEXISTE.
6. `POST fuerzas` con `{"frames": ["VIGA"], "combos": ["NOPE"]}`. Comprueba 409 con `code: "sap_error"`.
7. `POST fuerzas` con `{"frames": "selected", "combos": ["U1"]}` sin barras seleccionadas. Comprueba 409.
8. Fuerza un fallo al restaurar las unidades (p. ej. con el modelo bloqueado para cambios de unidades, si SAP2000 lo permite). Comprueba que `warnings` avisa de que no se pudieron restaurar las unidades y que las unidades del modelo no cambian sin aviso.
9. Sobre el modelo, con un caso de tracción en otra combinación (`U2`), comprueba que `envolvente[].gobierna.Tu_kN` = `U2` y que `Pu_kN` viene de `U1`.

**Qué debería pasar:**

* Los valores de los pasos 3 y 4 coinciden con la teoría con una tolerancia del 1 %.
* Las unidades del modelo quedan como estaban antes de cada llamada, también tras un error.
* La respuesta trae `validado_con_sap: true`, `validado_con` y `validado_fecha`.
* `warnings` avisa de `Lb` (no se calcula) y de la envolvente conservadora; avisa de interpolación de Cb solo si falta una estación en L/4, L/2 o 3L/4.

- ☐ Pasó
- ☐ Falló

Observaciones: ____________________

## Acero AISC 360-22 — enviar perfiles

**Validado con SAP2000 23.3.1 (sonda v3, 2026-10-14).** Creación por tipo, asignación, relectura, sobrescritura, asignación parcial sin reversión y desbloqueo, según la sonda v3 (registro en `docs/TRASPASO_ACERO_AISC360.md`). Este guion sirve para repetir la comprobación sobre el modelo de prueba.

Puente: `bridge/acero_aisc360_enviar.py`. Rutas: `/v1/sap/actions/acero-aisc360/enviar/preview`, `apply` y `revertir` (ver `docs/SAP_ACTIONS.md`).

**Preparación:** modelo de prueba con dos vigas simples de 6 m (`VIGA1`, `VIGA2`) con sección W sencilla y un material `A36`. Unidades del modelo en kip-ft, para comprobar la restauración. Un tercer frame `VIGA3` en el modelo, que no se incluirá en la lista. Guardar el modelo antes de empezar.

**Pasos:**

1. `POST enviar/preview` con `{"perfil": {"nombre": "W-PRUEBA", "tipo": "I", "dimensiones_mm": {"d": 300, "bf": 150, "tf": 12, "tw": 8}}, "material": "A36", "barras": ["VIGA1", "VIGA2"]}`. Comprueba que no cambia nada en SAP2000 (Define > Frame Sections sin «W-PRUEBA», las barras con su sección anterior), que `seccion.accion` = `crear` y `cambios` = 2.
2. `POST enviar/apply` con el mismo cuerpo. Comprueba 200, `ok` = `true`, `seccion.accion` = `creada`, que `W-PRUEBA` aparece en Define > Frame Sections con d = 300 mm, bf = 150 mm, tf = 12 mm y tw = 8 mm (comprobación a mano; la geometría de una I se relee con `GetISection` y debe coincidir, pero conviene mirarla también en la interfaz), que `VIGA1` y `VIGA2` tienen `W-PRUEBA`, y que `VIGA3` sigue con su sección anterior.
3. Repite el `apply`. Comprueba 409 `section_exists` y que no hay cambios.
4. `apply` con `"sobrescribir": true` y `"dimensiones_mm": {"d": 400, "bf": 180, "tf": 14, "tw": 9}`. Comprueba `seccion.accion` = `sobrescrita` y que `preview` avisaba de cuántas barras fuera de la lista usan `W-PRUEBA` (aquí, `VIGA3` si se le asignó esa sección antes).
5. `apply` con `"material": "NOEXISTE"`. Comprueba 422 con la lista de materiales y que no hay cambios.
6. Con el modelo analizado (bloqueado), `apply` con `"desbloquear": false`. Comprueba 409 `modelo_bloqueado` y que el modelo sigue bloqueado. Repite con `"desbloquear": true` y comprueba `modelo_desbloqueado` = `true`, `resultados_invalidados` = `true`, el aviso de que se borran los resultados, y que SAP2000 aparece sin resultados (hay que volver a analizar).
7. Fallo a mitad: con una barra inexistente o que se elimine desde otra ventana antes del apply, comprueba 409 con `partial` = `true`, que `aplicadas` lista las barras anteriores a la fallida, que `revertir` tiene la sección previa de cada una y que las barras aplicadas NO se revierten solas (aviso en `warnings`).
8. `POST enviar/revertir` con el `revertir` del paso anterior. Comprueba `ok` = `true` y que las barras vuelven a su sección previa en SAP2000.
9. `POST enviar/apply` con `"barras": "selected"` tras seleccionar en SAP2000 `VIGA1`. Comprueba que `barras` del preview contiene solo `VIGA1`, y sin selección, 409.
10. `POST enviar/apply` con un tipo `"tee"`. Comprueba 422 con mensaje sobre tees, sin cambios.
11. Tubo circular: `apply` con `"tipo": "pipe"` y `{"D": 114.3, "t": 4}`. Comprueba que SAP2000 crea una sección de tubo circular (Pipe) con D = 114,3 mm y t = 4 mm, y que la unidad de la sección no cambia.
12. Tras cada llamada, comprueba que las unidades del modelo (kip-ft) no cambian, también tras un error.

**Qué debería pasar:**

* Las secciones creadas, sobrescritas y asignadas coinciden con lo pedido, y las barras no pedidas no cambian.
* Ninguna respuesta de `preview` cambia el modelo.
* Las unidades del modelo quedan como estaban antes de cada llamada.
* La respuesta trae `validado_con_sap: true`, `validado_con` y `validado_fecha`.
* Canal, ángulo, tubo y pipe: `warnings` avisa de que la geometría no se relee en la acción (la sonda sí la comprobó). Solo la sección I se compara con `GetISection`.

**Nota:** si una llamada falla por firma o por orden de argumentos, corríjala en `bridge/actions.py::create_section` y repita el paso 2.

- ☐ Pasó
- ☐ Falló

Observaciones: ____________________

## Sonda de Acero AISC 360-22 (reconocimiento con SAP2000 real)

Antes de la validación manual de esta sección, ejecute `scripts/sonda_acero_aisc360.py` en Windows: evalúa con
modelos de solución teórica las hipótesis de `bridge/acero_aisc360_fuerzas.py` (EJE_FUERTE, signo de P, Cb) y de
`bridge/acero_aisc360_enviar.py` (secciones, asignación, bloqueo). Instrucciones, veredictos y tabla de cambios:
`docs/SONDA_ACERO_AISC360.md`. Validada con SAP2000 23.3.1 (sonda v3, 2026-10-14): 22 PASS, 3 INCONCLUSO (D2 y A4 informativos; F2 pendiente de la firma de `SetOutputStations`).
